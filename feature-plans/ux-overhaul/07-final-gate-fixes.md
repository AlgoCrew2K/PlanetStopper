# Feature: Final gate fixes before deploy
Status: ready
Type: bugfix
Suite: ux-overhaul 07
Repos: PlanetStopper
Base: lane/ux-fix after its test closer, with lane/clock-test merged
Sources, under `.claude/review/38b0cbe8007e/`: `pipelines-expert.json` (PIPE-n), `code-reviewer.json` (CR-n), `quant-code-reviewer.json` (QR-n), `security-auditor.md` (S-n). Each holds file:line, the failure and evidence. Reproductions: `.claude/review/38b0cbe8007e/repro/`.

Operator's two named failures: a number updating while the market is closed; the refresh cycle not starting in the morning. Decisions below were made with the operator away and are recorded as PM-ASSUMED.

Frozen: `alpha_bot_execution.py`, `math_engine.py`, `reporting.py`, `static/tokens.css`. The `/api/sell_account` route and the liquidation request stay byte-identical. No Composer call on a request path. Autoescape; no `| safe`.

## AC-1 A window never slides on the wall clock (PIPE-1)
`analytics._window_cutoff_date` reads the UTC date and `static/index.js` `windowCutoffIso` reads the browser's. Both make the headline, hero chart and Performance figures move at 00:00 UTC while closed.
- Every trailing and YTD window is counted from one anchor: the ET trading day of the session the figures belong to. While closed or pre-market that is the day of the stored close snapshot; during a session it is today's session. It changes only when a new session starts.
- Every consumer uses it: `/api/state`, `/api/strip/<w>`, `/api/hero-chart/<w>`, `/api/performance`, `/api/guard-alpha-summary`, the History windows, and the client. The client takes the anchor from the server and never reads its own clock for a cutoff.
- `tests/app/test_clocked_weekend_to_monday_open.py::test_a_session_on_the_edge_of_the_30d_window_stays_in_it_until_the_next_session` passes, unedited.

## AC-2 Just after the close the page never shows the previous session (PIPE-2)
From the close until today's snapshot is stored, the frozen branch must not serve an older session's stored snapshot as the current figures. It serves the last committed state of the session that just closed, cut at the close, so a poll across the close shows no jump (15:59:50 and 16:00:05 read the same Today). If the snapshot never arrives, those figures hold all night and `/health` and the header state say so with reason `close_snapshot_missing`. Reproduction: `repro_misc.py`.

## AC-3 A hung engine run cannot stop the session (PIPE-3, PIPE-9)
This reverses plan 02 S-5.3 and changes `tests/app/test_scheduler_single_flight.py:195` accordingly. Single flight stays, with a bound:
- At 09:25 ET on a trading day, a run that started on an earlier ET date is ended and the slot freed, so the open is never blocked. Autotune on Friday is not touched by this before then.
- On a trading day, a run that started before that day's close and is alive over 600 s is ended and the slot freed, unless forced. Autotune starts only after the close, so this does not cut it.
- Ending means terminate, then kill after 10 s. The run's outcome is `terminated`; it is logged and shows as a `/health` reason. The next minute spawns normally: confirm the engine's own lock does not then block it for more than one minute.
- The print at `app.py:1185` goes inside the try, so it cannot leak the slot.

## AC-4 A restart while closed moves nothing (PIPE-4)
The account-totals latch survives a restart: Today, Cumulative and account value read the same before a restart, after it, and after the first refresh, until the next session. Store it durably in a table the daemon owns. `bot_state` is rewritten by the engine, so do not rely on a key there surviving. An additive migration is allowed if no existing table fits. The stored close snapshot holds no account totals, so the first start after deploy latches the first refresh after the capture; state that in the handoff.

## AC-5 Performance and sparklines hold after the close (PIPE-5)
`/api/performance` and `/api/chart` cut rows at the snapshot's capture while closed, the same way the `/` routes do, so rows written 16:01 to 16:05 do not move them. Reproduction: `repro_perf.py`.

## AC-6 Polls answer fast (PM measurement)
`/api/state` and `GET /` take 13 to 14 s on the production snapshot, against 6.5 to 9.7 s at base, and `/api/strip/<w>` 3.7 to 5.5 s. A poll is abandoned at 20 s, so the 2-core droplet can read a false Connection Lost. `_compute_frozen_portfolio_strip` recomputes everything each poll: 73 fetches through the temp view over 386,503 rows; `get_symphony_cumulative_return` runs 33 times.
- Closed or pre-market: the result is held per snapshot identity and window anchor. A second poll runs no `shadow_history` query, returns byte-identical JSON, and answers under 1 s. Held results are bounded, thread-safe and replaced when the snapshot or anchor changes. `/api/strip/<w>` and `/api/hero-chart/<w>` likewise.
- Open: a request never runs the same trajectory query twice for one symphony and day. State measured time on the snapshot before and after; the first computation must not be slower than base.
- Warm the closed result on a background thread at startup and after a new snapshot, so the first poll does not pay for it.
- Tests pin query counts and byte equality with the hold on and off, not wall time.

## AC-7 A window label only ever sits beside its own value (CR-6, QR-1)
On a window click the headline, Max DD row and Ann. Vol row show the dash until `/api/strip/<w>` answers. On failure, they stay dashed and a visible notice says that window could not be loaded; a later click or the next successful answer clears it. Row deltas beside a pp headline do not print `%`.

## AC-8 A missing figure is a dash; the ghost is gone (QR-2, QR-3, backlog)
- The hero Today and Cumulative rows and `writeDetailFigures` print the dash for an unknown figure, never `+0.00%` or `0`.
- `/api/guard-alpha-preconditions` (`app.py:5436`) excludes no-longer-held symphonies, and its "replay unavailable N of M" count follows.
- Every drawdown figure in tenure evidence prints as a positive magnitude, including `realized_`, `backtest_`, `with_` and `without_`; a gap between drawdowns reads correctly in that convention. Verdict logic stays on the signed values.

## AC-9 The missed morning start is visible (PIPE-10)
A `/health` reason `no_cycle_since_open`, also driving the header's Degraded state: the market has been open 3 minutes or more and no cycle has committed since the open. It clears on the next commit, fires from 09:35 ET when Opening ends, and never fires while closed.

## AC-10 Security (S-1, S-2, S-3, S-4, S-5, S-6, S-8, S-12, S-13, S-14, S-16)
- S-1, S-13: `renderDetailLogs` and `renderDetailVars` render text as text. A symphony name carrying markup shows literally.
- S-2: `_validate_csrf` reads the form field only on the login endpoint; other routes need the header. POSTs with `Sec-Fetch-Site: same-site` or `cross-site` are refused.
- S-3: `POST /api/settings` validates every value before any write, all or nothing: finite float for algorithm parameters, `H:MM or HH:MM, hours 0-23` for `EXECUTION_START_TIME`, the enum for `EXIT_AUTHORITY`. The payload in the finding returns 400 and leaves `.env` byte-identical.
- S-4: suggestion Apply and Dismiss use data attributes and a listener. A name with an apostrophe works. This is a live bug on 2 of 6 captured names.
- S-5: security headers in `deploy/caddy/Caddyfile`; the app's own inline scripts and CDN must keep working, so no CSP that breaks them. S-6: the CDN script is pinned with SRI, or vendored. S-8: `.gitignore` covers the real backups. S-12: a non-ASCII token is a clean 403. S-14: Panic Confirm has an in-flight guard and the request bytes stay identical. S-16: `composer_series_client` does not follow redirects.
- Not in scope, for the operator: S-7 (real account id in the public repo), S-15 (credentials in the local stash), S-9, S-10, S-11, S-17.

## AC-11 Spend (CR-7)
The tenure review and the recommender do not defeat explanation reuse for each other: reuse compares a row to the newest row from the same producer.

## AC-12 The Settings flush keeps production history (S-INFO-2)
`_REAL_POST_MORTEM_DATES` (`app.py:8757`) ends at 2026-05-20, so a legitimate flush deletes every production post-mortem since the 2026-06-22 go-live. A post-mortem file dated on or after the go-live floor is kept; the existing synthetic-file deletion stays for older dates not in the set. The confirm text and the response counts stay truthful.

## Lanes
Two lanes fork from `lane/fix07`. `lane/fix07-time`: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-9. `lane/fix07-ui`: AC-7, AC-8, AC-10, AC-11, AC-12.

## Testing
Each item has a failing test first, on the real route or the node harness; run with the frozen-clock world of `tests/app/test_clocked_weekend_to_monday_open.py` where time matters, using a world of 45 or more days of rows so a window edge is real. `tests/app/test_closed_market_numbers_never_move.py` keeps passing. CI has no `.env`: run with `ANTHROPIC_API_KEY` and Composer credentials blanked.
