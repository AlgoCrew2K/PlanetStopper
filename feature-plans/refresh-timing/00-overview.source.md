# Refresh-timing suite: source as given

Saved verbatim from the PM's dispatch brief of 2026-10-22. The refresh-timing audit itself is the suite's evidence, not its source, and is not copied here; the plans cite it by finding id (TA-1 to TA-52).

## Operator source (2026-10-22)
I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll.

## Context (cited as context only)
It was sent while the UX overhaul suite (feature-plans/ux-overhaul/) was being built, after the operator's earlier message "Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly." That earlier message is owned by the ux-overhaul suite; cite it as context only.

## Production facts the PM verified on the droplet at 2026-10-22T22:20-22:25Z
Host timezone is Etc/UTC, with NTP synchronized.
/etc/caddy/Caddyfile is exactly `104-248-7-101.sslip.io { reverse_proxy localhost:8090 }` (Caddy v2.11.4). It has no `encode` and no `flush_interval`.
One daemon runs (planetstopper.service, `.venv/bin/python app.py`) with one engine child. The engine is triggered once per minute around the clock, after the close included. Its in-text log clock is UTC.
Stdout is block-buffered: lines printed from 18:05 to 21:58 UTC all reached the journal at 21:59.
4 daemon restarts on 2026-10-22.

## PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop)

### Building
1. Every number, chart, label and status on every page shows the newest data the engine has produced. It updates without a reload, never reverts or flickers when no new data arrived, and never shows stale or wrong-day data without a dated, honest cue. It recovers by itself after a daemon restart, laptop sleep, tab return or network loss.
2. Users: the operator, through `/`, `/history`, `/performance`, `/ai-advisor`, `/settings` and the shared header. The server side is the minute scheduler, the engine's close snapshot, `/api/state`, `/api/events`, `/health` and the other GET routes the pages poll. Diagnosis uses the droplet journal and `alphabot_daemon.log`.
3. Flows: keep `/` open through a whole trading day: pre-market, the 09:30 open, intraday, the 15:53-15:59 blackout, the 16:00 close, the evening and overnight.
   - Along the way: pick a hero window, pick and clear an account, open a detail panel, use Cash Now, Force run and Emergency Liquidate.
   - Keep tabs open across a deploy or restart and a laptop sleep; switch tabs and come back; use the other pages.
   - Expected: values change within one cycle of new data; the operator's selections persist through polls; every freshness stamp carries a date and its true age; after a restart, live updates, actions and the header recover without a reload.
   - Use the workflow files under `.claude/workflows/` if any have a dated Confirmed line; otherwise write "no workflow file" with the reason.
4. Negatives:
   - An `/api/state` 401/403/500/503/timeout or an error-shaped body is never drawn as data. The last good values stay, with a visible stale/connection cue and retry; a 401 goes to login.
   - A Composer timeout or 503 shows a stale marker with its as-of.
   - An engine cycle longer than 60 s never runs two engines at once and never lets an older writer win.
   - An exception in the scheduler loop never stops updates, and `/health` reports the degradation.
   - After a restart, the event stream reconnects and the CSRF token refreshes.
   - **SAFETY:** no POST is ever re-sent automatically after a token refresh. That covers Emergency Liquidate, Cash Now, settings saves and approvals. The page tells the operator the action was not sent and needs a new click.
   - Overlapping requests: the newest issued request wins everywhere, not only `loadState`. Two tabs are independent.
   - A poll arriving while the settings modal has unsaved edits never changes which symphony the modal saves (TA-38).
5. Data and contracts:
   - `/api/state` freshness fields carry a date and time, and the frozen branch states its snapshot date. One portfolio computation per response.
   - The error body shape is distinguishable from data.
   - The `cycle-complete` event payload may gain a cycle id and timestamp so the client can skip unchanged data.
   - `/health` gains scheduler liveness.
   - Log lines for cycle duration, Composer latency and requests.
   - The stored `bot_state.last_market_close_snapshot` holds one snapshot with no nested earlier snapshot. A one-time repair of the stored state keeps the latest close's values exactly.
   - Caddy response compression (deployment config and docs/DEPLOYMENT.md).
6. Repos: PlanetStopper only.
7. Sensitive: YES.
   - Plan 01 changes the engine file's close-snapshot builder.
   - Plan 04 touches CSRF and token handling, the controls Emergency Liquidate, Cash Now and the settings modal, and the live flag (TA-38).

### Not building
1. No visual redesign; the ux-overhaul suite and Claude Design own visuals. No new data sources. Server-sent events stay; they are fixed, not replaced.
2. Must stay exactly as it is:
   - the engine's exit-decision path (`math_engine.py` zero diff; `alpha_bot_execution.py` changes only in the close-snapshot builder);
   - trade, liquidation and order request payloads;
   - `LIVE_EXECUTION`;
   - the engine's once-a-minute cadence, around the clock;
   - the 1-minute execution path gets no blocking I/O;
   - the value each metric computes, except where the audit shows two computations disagreeing inside one response (TA-6), where one computation replaces two;
   - the database schema, unless additive.
3. Items the audit marks COVERED stay with their plans (ux-overhaul 05, C3 round 2, 04 AC-8/AC-9, 03 AC-2). This suite names them under "Earlier criteria on the same surface" and does not re-specify them.
4. Composer, Alpaca and Anthropic APIs are consumed, never changed.

## The split (PM-agreed; plan numbers fixed). Assign every NEW and PARTLY COVERED TA id to exactly one plan, or to an NB with a reason
- **01-server-state-truth.** TA-1 (engine close-snapshot nesting plus the one-time stored-state repair), TA-3 server half (the after-close branch carries the per-card guard alpha the verdict needs), TA-6, TA-7, TA-8, TA-9 server half, TA-14, TA-18 server stamps, TA-23 (verdict "at exit" moving each cycle: propose a default in Open Questions, either truthful labelling or freezing the exit value), TA-40.
- **02-scheduler-events-health.** TA-12, TA-13 event half (fire with a cycle id; the client can skip unchanged data), TA-15, TA-16, TA-17, TA-31, TA-33, TA-39, TA-42.
- **03-dashboard-refresh** (`/`). TA-2, TA-4, TA-10, TA-19, TA-21, TA-22, TA-24, TA-25, TA-26, TA-27, TA-29, TA-32, TA-36, TA-41, and the client halves of TA-3 and TA-9. This plan supersedes ux-overhaul plan 05's S-4.7 (the headline revert) and NB-4 (the event stream kept as is); record both under Earlier criteria with the reason.
- **04-pages-and-header.** TA-5 (CSRF refresh on every page; never an automatic re-send), TA-20, TA-28, TA-34, TA-35, TA-37, TA-38 (Sensitive).
- **05-observability-transport.** TA-30 (stdout line buffering despite the re-wrap at app.py around :9001; cycle-duration, Composer-latency and request logs), TA-13 payload half (Caddy `encode` and payload size), and the section 8 production measurements as this suite's live verification.

## Stack (put in the overview's Stack and Merge Rules; the architect may refine with reasons)
- model-unpin lands first. It edits `app.py`, `templates/ai_advisor.html`, `static/ai_advisor.js` and other advisor files.
- 01, 02 and 05 are server-side and may start from origin/main now. They still edit `app.py`, as ux-overhaul 05 C3 does around `/api/state`.
- 03 stacks on the assembled `ux-overhaul/05` branch (C1 61707cd6, C2 d10040f7, C3 67cf9ca3 plus round 2 in flight, which adds a newest-request guard to `loadState`).
- 04 stacks after ux-overhaul plan 04 (app shell), which rewrites every page header.
- Note these overlaps in the overview: 03 shares `static/index.js` with ux-overhaul 05; 04 shares `static/chrome.js` and every template header with ux-overhaul 04.

## Earlier criteria on the same surface (find and cite them all)
- ux-overhaul plan 05: S-4.7, NB-3, NB-4, its PM-7 decision, AC-3, AC-6.
- ux-overhaul plan 04: AC-8, AC-9.
- ux-overhaul plan 03: AC-2.
- model-unpin C9 header badges.
- The earlier decisions on `/api/state`, `data_as_of` and account-basis in the project CLAUDE.md Key Files rows (DE-EOD-BASIS-001, DE-AUDIT-BL4-001, DE-OPS-CLUSTER-001).

## PM ruling at gate-1 (2026-10-22, [PM-ASSUMED]; operator not available)
REVISION (plan 01, Q-3 / AC-1): The default is approved: the repair runs at daemon start, before the scheduler thread starts.
Add one requirement and scenario: before the one-time repair writes, the daemon saves a copy of the pre-repair state row, so the repair can be reversed by hand.
The copy goes to a file beside the state DB or an additive table; that choice is Gate 2.
A failed backup write means no repair this start, plus a logged reason with the exception type name only, and a retry at the next start.

## PM amendments from the Gate-2 approaches (2026-10-22, [PM-ASSUMED]; operator not available)
PLAN 03: TA-36 criterion, new scenario: Sleeves rows added or removed while `/` is open appear or disappear on the next poll, without a reload.
PLAN 03: data-refresh-count increments for the newest request's handled outcome, whether a rendered body or a handled failure. A superseded request never counts.
PLAN 03: NEW gap, found by the ux05 C1 closer: when the poll series has no row inside the picked window, the client currently returns before drawing or captioning. The new window's label then sits over the previous window's series with no cue. This happens when a refused /api/hero-chart meets a stale poll series, or on 1 January for YTD. Default: keep the old series with a visible cue saying the picked window has no data yet. Add it as a scenario under the window criterion.
PLAN 04: The Frontrunner on-demand build writes one append-only FRONTRUNNER_BUILD_SUMMARY advisor_observations row per run, following the RETIREMENT_RUN_SUMMARY precedent. This uses the existing table and needs no schema change. Narrow overview NB-3 to allow it.
PLAN 04: New scenario: POST /api/fleet-alert/dismiss waits on its write with a bounded timeout and answers 503 on failure. It must never answer ok before the write lands.
PLAN 05: New scenario, ruled in: the existing ERROR lines at app.py:905-909 (exception text plus traceback; a requests error message carries the URL with the account id) and app.py:6407 log the exception type name only at WARNING or above, with the detail at DEBUG.

## PM final-approval amendments (2026-10-22, [PM-ASSUMED]; from the suite approach approach/00-suite.md and two late rulings)
"Degraded" header state: Overview SD-13 gains a state, "Degraded", at rank 3. The full order is: Connection Lost > Not updating > Degraded > Closed > Rebalance pause > Opening > Stale > Live. Only `/health`'s `degraded_reasons` changes the label. Add a scenario to plan 03 AC-4 (on `/`) and to plan 04 AC-1 (the four other pages): when `degraded_reasons` is non-empty, the header reads "Degraded", with a tooltip naming the reasons.
Plan 04, new scenario: When `POST /api/settings/flush-resync` fails, its JSON response must carry the exception type name only. Place it under the settings-flush criterion (TA-37 area) and cite the project rule: no exception text in responses.
