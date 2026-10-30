# Epic: Refresh timing: every page shows the newest data, dated, through every refresh
Status: planned (approved by the PM at Step 7 with `feature-plans/refresh-timing/approach/00-suite.md` and the five plan approaches, [PM-ASSUMED], operator may override)
Repos: PlanetStopper
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864

## North Star
Every number, chart, label and status on `/`, `/history`, `/performance`, `/ai-advisor`, `/settings` and the shared header shows the newest data the engine has produced, updates without a reload, never reverts or flickers when no new data arrived, carries a dated stamp that tells the truth about its age, and recovers by itself after a daemon restart, a laptop sleep, a tab return or a network loss, while the engine's exit decisions, trade and liquidation requests, `LIVE_EXECUTION` and the once-a-minute cadence stay exactly as they are. The evidence is the refresh-timing audit at the ref (a review artifact outside the tracked tree, dated 2026-10-22, run by three auditors against a copy of the production state database and the PM's droplet reads): 52 findings, TA-1 to TA-52, of which 35 are new, 6 are partly covered and 11 are covered by plans already in flight. The 41 new and partly covered findings span five surfaces with different files, risks and bases, so the suite is split into five plans: the server's state truth, including the engine's close snapshot (01); the scheduler, the cycle event and `/health` (02); the client refresh on `/` (03), which stacks on the UX overhaul's dashboard plan; the other pages and the shared header (04), which stacks on the UX overhaul's app-shell plan; and observability and transport (05). One approval per plan keeps each PR small enough for a reviewer, and the Suite Gate proves the stack against the operator's whole-day flow.

## Requirements as Given
[G-1] I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll. 00-overview.source.md:6
[G-2] (Context) It was sent while the UX overhaul suite (feature-plans/ux-overhaul/) was being built, after the operator's earlier message "Your whole job for the rest of the day is an overhaul of the entire application, ensuring it fucking looks right on top of functions properly." 00-overview.source.md:9
[G-3] (Context) That earlier message is owned by the ux-overhaul suite; cite it as context only. 00-overview.source.md:9
[G-4] (Production fact) Host timezone is Etc/UTC, with NTP synchronized. 00-overview.source.md:12
[G-5] (Production fact) /etc/caddy/Caddyfile is exactly `104-248-7-101.sslip.io { reverse_proxy localhost:8090 }` (Caddy v2.11.4). 00-overview.source.md:13
[G-6] (Production fact) It has no `encode` and no `flush_interval`. 00-overview.source.md:13
[G-7] (Production fact) One daemon runs (planetstopper.service, `.venv/bin/python app.py`) with one engine child. 00-overview.source.md:14
[G-8] (Production fact) The engine is triggered once per minute around the clock, after the close included. 00-overview.source.md:14
[G-9] (Production fact) Its in-text log clock is UTC. 00-overview.source.md:14
[G-10] (Production fact) Stdout is block-buffered: lines printed from 18:05 to 21:58 UTC all reached the journal at 21:59. 00-overview.source.md:15
[G-11] (Production fact) 4 daemon restarts on 2026-10-22. 00-overview.source.md:16
[G-12] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:18
[G-13] (Building 1) Every number, chart, label and status on every page shows the newest data the engine has produced. 00-overview.source.md:21
[G-14] (Building 1) It updates without a reload, never reverts or flickers when no new data arrived, and never shows stale or wrong-day data without a dated, honest cue. 00-overview.source.md:21
[G-15] (Building 1) It recovers by itself after a daemon restart, laptop sleep, tab return or network loss. 00-overview.source.md:21
[G-16] (Building 2) Users: the operator, through `/`, `/history`, `/performance`, `/ai-advisor`, `/settings` and the shared header. 00-overview.source.md:22
[G-17] (Building 2) The server side is the minute scheduler, the engine's close snapshot, `/api/state`, `/api/events`, `/health` and the other GET routes the pages poll. 00-overview.source.md:22
[G-18] (Building 2) Diagnosis uses the droplet journal and `alphabot_daemon.log`. 00-overview.source.md:22
[G-19] (Building 3) Flows: keep `/` open through a whole trading day: pre-market, the 09:30 open, intraday, the 15:53-15:59 blackout, the 16:00 close, the evening and overnight. 00-overview.source.md:23
[G-20] (Building 3) Along the way: pick a hero window, pick and clear an account, open a detail panel, use Cash Now, Force run and Emergency Liquidate. 00-overview.source.md:24
[G-21] (Building 3) Keep tabs open across a deploy or restart and a laptop sleep; switch tabs and come back; use the other pages. 00-overview.source.md:25
[G-22] (Building 3) Expected: values change within one cycle of new data; the operator's selections persist through polls; every freshness stamp carries a date and its true age; after a restart, live updates, actions and the header recover without a reload. 00-overview.source.md:26
[G-23] (Building 3) Use the workflow files under `.claude/workflows/` if any have a dated Confirmed line; otherwise write "no workflow file" with the reason. 00-overview.source.md:27
[G-24] (Building 4) An `/api/state` 401/403/500/503/timeout or an error-shaped body is never drawn as data. 00-overview.source.md:29
[G-25] (Building 4) The last good values stay, with a visible stale/connection cue and retry; a 401 goes to login. 00-overview.source.md:29
[G-26] (Building 4) A Composer timeout or 503 shows a stale marker with its as-of. 00-overview.source.md:30
[G-27] (Building 4) An engine cycle longer than 60 s never runs two engines at once and never lets an older writer win. 00-overview.source.md:31
[G-28] (Building 4) An exception in the scheduler loop never stops updates, and `/health` reports the degradation. 00-overview.source.md:32
[G-29] (Building 4) After a restart, the event stream reconnects and the CSRF token refreshes. 00-overview.source.md:33
[G-30] (Building 4) SAFETY: no POST is ever re-sent automatically after a token refresh. 00-overview.source.md:34
[G-31] (Building 4) That covers Emergency Liquidate, Cash Now, settings saves and approvals. 00-overview.source.md:34
[G-32] (Building 4) The page tells the operator the action was not sent and needs a new click. 00-overview.source.md:34
[G-33] (Building 4) Overlapping requests: the newest issued request wins everywhere, not only `loadState`. 00-overview.source.md:35
[G-34] (Building 4) Two tabs are independent. 00-overview.source.md:35
[G-35] (Building 4) A poll arriving while the settings modal has unsaved edits never changes which symphony the modal saves (TA-38). 00-overview.source.md:36
[G-36] (Building 5) `/api/state` freshness fields carry a date and time, and the frozen branch states its snapshot date. 00-overview.source.md:38
[G-37] (Building 5) One portfolio computation per response. 00-overview.source.md:38
[G-38] (Building 5) The error body shape is distinguishable from data. 00-overview.source.md:39
[G-39] (Building 5) The `cycle-complete` event payload may gain a cycle id and timestamp so the client can skip unchanged data. 00-overview.source.md:40
[G-40] (Building 5) `/health` gains scheduler liveness. 00-overview.source.md:41
[G-41] (Building 5) Log lines for cycle duration, Composer latency and requests. 00-overview.source.md:42
[G-42] (Building 5) The stored `bot_state.last_market_close_snapshot` holds one snapshot with no nested earlier snapshot. 00-overview.source.md:43
[G-43] (Building 5) A one-time repair of the stored state keeps the latest close's values exactly. 00-overview.source.md:43
[G-44] (Building 5) Caddy response compression (deployment config and docs/DEPLOYMENT.md). 00-overview.source.md:44
[G-45] (Building 6) Repos: PlanetStopper only. 00-overview.source.md:45
[G-46] (Building 7) Sensitive: YES. 00-overview.source.md:46
[G-47] (Building 7) Plan 01 changes the engine file's close-snapshot builder. 00-overview.source.md:47
[G-48] (Building 7) Plan 04 touches CSRF and token handling, the controls Emergency Liquidate, Cash Now and the settings modal, and the live flag (TA-38). 00-overview.source.md:48
[G-49] (Not building 1) No visual redesign; the ux-overhaul suite and Claude Design own visuals. 00-overview.source.md:51
[G-50] (Not building 1) No new data sources. 00-overview.source.md:51
[G-51] (Not building 1) Server-sent events stay; they are fixed, not replaced. 00-overview.source.md:51
[G-52] (Not building 2) Must stay exactly as it is: the engine's exit-decision path (`math_engine.py` zero diff; `alpha_bot_execution.py` changes only in the close-snapshot builder); 00-overview.source.md:52-53
[G-53] (Not building 2) trade, liquidation and order request payloads; 00-overview.source.md:54
[G-54] (Not building 2) `LIVE_EXECUTION`; 00-overview.source.md:55
[G-55] (Not building 2) the engine's once-a-minute cadence, around the clock; 00-overview.source.md:56
[G-56] (Not building 2) the 1-minute execution path gets no blocking I/O; 00-overview.source.md:57
[G-57] (Not building 2) the value each metric computes, except where the audit shows two computations disagreeing inside one response (TA-6), where one computation replaces two; 00-overview.source.md:58
[G-58] (Not building 2) the database schema, unless additive. 00-overview.source.md:59
[G-59] (Not building 3) Items the audit marks COVERED stay with their plans (ux-overhaul 05, C3 round 2, 04 AC-8/AC-9, 03 AC-2). 00-overview.source.md:60
[G-60] (Not building 3) This suite names them under "Earlier criteria on the same surface" and does not re-specify them. 00-overview.source.md:60
[G-61] (Not building 4) Composer, Alpaca and Anthropic APIs are consumed, never changed. 00-overview.source.md:61
[G-62] The split (PM-agreed; plan numbers fixed). Assign every NEW and PARTLY COVERED TA id to exactly one plan, or to an NB with a reason 00-overview.source.md:63
[G-63] 01-server-state-truth. TA-1 (engine close-snapshot nesting plus the one-time stored-state repair), TA-3 server half (the after-close branch carries the per-card guard alpha the verdict needs), TA-6, TA-7, TA-8, TA-9 server half, TA-14, TA-18 server stamps, TA-23 (verdict "at exit" moving each cycle: propose a default in Open Questions, either truthful labelling or freezing the exit value), TA-40. 00-overview.source.md:64
[G-64] 02-scheduler-events-health. TA-12, TA-13 event half (fire with a cycle id; the client can skip unchanged data), TA-15, TA-16, TA-17, TA-31, TA-33, TA-39, TA-42. 00-overview.source.md:65
[G-65] 03-dashboard-refresh (`/`). TA-2, TA-4, TA-10, TA-19, TA-21, TA-22, TA-24, TA-25, TA-26, TA-27, TA-29, TA-32, TA-36, TA-41, and the client halves of TA-3 and TA-9. 00-overview.source.md:66
[G-66] This plan supersedes ux-overhaul plan 05's S-4.7 (the headline revert) and NB-4 (the event stream kept as is); record both under Earlier criteria with the reason. 00-overview.source.md:66
[G-67] 04-pages-and-header. TA-5 (CSRF refresh on every page; never an automatic re-send), TA-20, TA-28, TA-34, TA-35, TA-37, TA-38 (Sensitive). 00-overview.source.md:67
[G-68] 05-observability-transport. TA-30 (stdout line buffering despite the re-wrap at app.py around :9001; cycle-duration, Composer-latency and request logs), TA-13 payload half (Caddy `encode` and payload size), and the section 8 production measurements as this suite's live verification. 00-overview.source.md:68
[G-69] Stack (put in the overview's Stack and Merge Rules; the architect may refine with reasons) 00-overview.source.md:70
[G-70] model-unpin lands first. 00-overview.source.md:71
[G-71] It edits `app.py`, `templates/ai_advisor.html`, `static/ai_advisor.js` and other advisor files. 00-overview.source.md:71
[G-72] 01, 02 and 05 are server-side and may start from origin/main now. 00-overview.source.md:72
[G-73] They still edit `app.py`, as ux-overhaul 05 C3 does around `/api/state`. 00-overview.source.md:72
[G-74] 03 stacks on the assembled `ux-overhaul/05` branch (C1 61707cd6, C2 d10040f7, C3 67cf9ca3 plus round 2 in flight, which adds a newest-request guard to `loadState`). 00-overview.source.md:73
[G-75] 04 stacks after ux-overhaul plan 04 (app shell), which rewrites every page header. 00-overview.source.md:74
[G-76] Note these overlaps in the overview: 03 shares `static/index.js` with ux-overhaul 05; 04 shares `static/chrome.js` and every template header with ux-overhaul 04. 00-overview.source.md:75
[G-77] Earlier criteria on the same surface (find and cite them all) 00-overview.source.md:77
[G-78] ux-overhaul plan 05: S-4.7, NB-3, NB-4, its PM-7 decision, AC-3, AC-6. 00-overview.source.md:78
[G-79] ux-overhaul plan 04: AC-8, AC-9. 00-overview.source.md:79
[G-80] ux-overhaul plan 03: AC-2. 00-overview.source.md:80
[G-81] model-unpin C9 header badges. 00-overview.source.md:81
[G-82] The earlier decisions on `/api/state`, `data_as_of` and account-basis in the project CLAUDE.md Key Files rows (DE-EOD-BASIS-001, DE-AUDIT-BL4-001, DE-OPS-CLUSTER-001). 00-overview.source.md:82

[G-83] (PM ruling at gate-1) REVISION (plan 01, Q-3 / AC-1): The default is approved: the repair runs at daemon start, before the scheduler thread starts. 00-overview.source.md:85
[G-84] (PM ruling at gate-1) Add one requirement and scenario: before the one-time repair writes, the daemon saves a copy of the pre-repair state row, so the repair can be reversed by hand. 00-overview.source.md:86
[G-85] (PM ruling at gate-1) The copy goes to a file beside the state DB or an additive table; that choice is Gate 2. 00-overview.source.md:87
[G-86] (PM ruling at gate-1) A failed backup write means no repair this start, plus a logged reason with the exception type name only, and a retry at the next start. 00-overview.source.md:88

[G-87] (PM amendment) PLAN 03: TA-36 criterion, new scenario: Sleeves rows added or removed while `/` is open appear or disappear on the next poll, without a reload. 00-overview.source.md:91
[G-88] (PM amendment) PLAN 03: data-refresh-count increments for the newest request's handled outcome, whether a rendered body or a handled failure. A superseded request never counts. 00-overview.source.md:92
[G-89] (PM amendment) PLAN 03: NEW gap, found by the ux05 C1 closer: when the poll series has no row inside the picked window, the client currently returns before drawing or captioning. The new window's label then sits over the previous window's series with no cue. This happens when a refused /api/hero-chart meets a stale poll series, or on 1 January for YTD. Default: keep the old series with a visible cue saying the picked window has no data yet. Add it as a scenario under the window criterion. 00-overview.source.md:93
[G-90] (PM amendment) PLAN 04: The Frontrunner on-demand build writes one append-only FRONTRUNNER_BUILD_SUMMARY advisor_observations row per run, following the RETIREMENT_RUN_SUMMARY precedent. This uses the existing table and needs no schema change. Narrow overview NB-3 to allow it. 00-overview.source.md:94
[G-91] (PM amendment) PLAN 04: New scenario: POST /api/fleet-alert/dismiss waits on its write with a bounded timeout and answers 503 on failure. It must never answer ok before the write lands. 00-overview.source.md:95
[G-92] (PM amendment) PLAN 05: New scenario, ruled in: the existing ERROR lines at app.py:905-909 (exception text plus traceback; a requests error message carries the URL with the account id) and app.py:6407 log the exception type name only at WARNING or above, with the detail at DEBUG. 00-overview.source.md:96

[G-93] (PM final amendment) "Degraded" header state: Overview SD-13 gains a state, "Degraded", at rank 3. The full order is: Connection Lost > Not updating > Degraded > Closed > Rebalance pause > Opening > Stale > Live. Only `/health`'s `degraded_reasons` changes the label. Add a scenario to plan 03 AC-4 (on `/`) and to plan 04 AC-1 (the four other pages): when `degraded_reasons` is non-empty, the header reads "Degraded", with a tooltip naming the reasons. 00-overview.source.md:99
[G-94] (PM final amendment) Plan 04, new scenario: When `POST /api/settings/flush-resync` fails, its JSON response must carry the exception type name only. Place it under the settings-flush criterion (TA-37 area) and cite the project rule: no exception text in responses. 00-overview.source.md:100

## Requirement Inventory
Child plans keep these R ids for the items they own. An item owned by several plans is delivered in part by each, and each plan's inventory states its part. Every new or partly covered finding has one owning plan: 01 owns TA-1, TA-3 (server), TA-6, TA-7, TA-8, TA-9 (server), TA-14, TA-18, TA-23 and TA-40; 02 owns TA-12, TA-13 (event), TA-15, TA-16, TA-17, TA-31, TA-33, TA-39 and TA-42; 03 owns TA-2, TA-3 (client), TA-4, TA-9 (client), TA-10, TA-19, TA-21, TA-22, TA-24, TA-25, TA-26, TA-27, TA-29, TA-32, TA-36 and TA-41; 04 owns TA-5, TA-20, TA-28, TA-34, TA-35, TA-37 and TA-38; 05 owns TA-13 (payload) and TA-30. The covered findings TA-11 and TA-43 to TA-52 stay with their plans (NB-12).

| R-n | Requirement item | Source (G ids) | Maps to (plan NN, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Every timing issue the refresh-timing audit found is resolved | G-1, G-62 | 01, 02, 03, 04, 05, NB-12 | full |
| R-2 | No shown value changes, reverts or flickers when no new data arrived ("wonky updates") | G-1, G-14 | 01, 03 | full |
| R-3 | Every datum the poll carries reaches the screen without a reload ("no updates") | G-1, G-14 | 02, 03, 04 | full |
| R-4 | Every number, chart, label and status on every page shows the newest data the engine has produced | G-13 | 01, 02, 03, 04 | full |
| R-5 | Stale or wrong-day data is shown only with a dated, honest cue | G-14, G-22 | 01, 03 | full |
| R-6 | Every page recovers by itself after a daemon restart, a laptop sleep, a tab return or a network loss | G-15, G-11 | 02, 03, 04 | full |
| R-7 | The suite covers `/`, `/history`, `/performance`, `/ai-advisor`, `/settings`, the shared header, the minute scheduler, the close snapshot, `/api/state`, `/api/events`, `/health` and the GET routes the pages poll | G-16, G-17 | 01, 02, 03, 04, 05 | full |
| R-8 | Production timing is diagnosable from the droplet journal and `alphabot_daemon.log` | G-18, G-9 | 05 | full |
| R-9 | A whole trading day on `/` (pre-market, the 09:30 open, intraday, the 15:53-15:59 blackout, the 16:00 close, the evening and overnight) shows correct, current values | G-19 | 01, 02, 03 | full |
| R-10 | The operator's selections (hero window, account, open detail panel) persist through every refresh, and Cash Now, Force run and Emergency Liquidate keep working through the day | G-20, G-22 | 03, 04 | narrowed: Force run's reply and Emergency Liquidate's request, phrase gate and refusal messages are ux-overhaul 04 AC-4, AC-8 and AC-9 (NB-12); this suite adds the token refresh and the not-sent message |
| R-11 | Tabs kept open across a deploy, a restart or a sleep, switched away and back, and the other pages stay current | G-21 | 03, 04 | full |
| R-12 | Values change within one engine cycle of new data | G-22 | 02, 03 | full |
| R-13 | Every freshness stamp carries a date and its true age | G-22, G-36 | 01, 03, 04 | full |
| R-14 | After a restart, live updates, actions and the header recover without a reload | G-22, G-29 | 02, 03, 04 | full |
| R-15 | Each plan with a screen names its workflow file or states "no workflow file" with the reason | G-23 | 01, 02, 03, 04 | full |
| R-16 | An error, refused, unavailable or timed-out poll response is never drawn as data; the last good values stay with a visible stale or connection cue and a retry; a 401 goes to login (TA-10 on `/`, and the same rule on the other pages' polls) | G-24, G-25, G-65 | 03, 04 | full |
| R-17 | A Composer timeout or 503 shows a stale marker with its as-of | G-26 | 02 | full |
| R-18 | An engine cycle longer than 60 s never runs two engines at once and never lets an older writer win (TA-16) | G-27, G-7, G-64 | 02 | full |
| R-19 | An exception in the scheduler loop never stops updates, and a slow account refresh never delays the engine spawn (TA-15) | G-28, G-64 | 02 | full |
| R-20 | After a restart the event stream reconnects, and the $-saved panel refreshes even while no stream is open (TA-4) | G-29, G-51, G-65 | 03 | full |
| R-21 | After a restart every open page's CSRF token refreshes, so its controls work without a reload (TA-5) | G-29, G-67 | 04 | full |
| R-22 | No POST is ever re-sent automatically after a token refresh (Emergency Liquidate, Cash Now, settings saves, approvals), and the page tells the operator the action was not sent and needs a new click | G-30, G-31, G-32 | 04 | full |
| R-23 | The newest issued request wins on every page, not only `loadState`, and two tabs are independent | G-33, G-34 | 03, 04 | full |
| R-24 | Neither a poll nor a late response while the settings modal is open changes which symphony the modal shows or saves (TA-38) | G-35, G-67 | 04 | full |
| R-25 | `/api/state` freshness fields carry a date and a time, and the frozen branch states its snapshot date | G-36 | 01 | full |
| R-26 | One portfolio computation per `/api/state` response, so the server render and the first poll agree (TA-6) | G-37, G-57, G-63 | 01 | full |
| R-27 | An error body is distinguishable from data | G-38 | 03, 04 | full |
| R-28 | The `cycle-complete` event names its cycle, its time and whether data changed, and an open `/` tab skips unchanged data (TA-13 event half) | G-39, G-64 | 02 | full |
| R-29 | `/health` reports scheduler liveness and its degradation | G-40, G-28 | 02 | full |
| R-30 | Log lines for each engine cycle's duration, each Composer call's latency and each request (TA-30) | G-41, G-68 | 05 | full |
| R-31 | The stored close snapshot holds one snapshot with no nested earlier snapshot, and a one-time repair keeps the latest close's values exactly (TA-1) | G-42, G-43, G-63 | 01 | full |
| R-32 | Caddy compresses responses, in the deployment configuration and in `docs/DEPLOYMENT.md` (TA-13 payload half) | G-44, G-5, G-6, G-68 | 05 | full |
| R-33 | PlanetStopper is the only repository changed | G-45 | NB-1 | full |
| R-34 | Plan 01 (the engine's close-snapshot builder) and plan 04 (CSRF and tokens, Emergency Liquidate, Cash Now, the settings modal and the live flag) are marked Sensitive | G-46, G-47, G-48 | 01, 04 | full |
| R-35 | No visual redesign; the UX overhaul suite and Claude Design own visuals | G-49, G-2, G-3 | NB-2 | full |
| R-36 | No new data sources | G-50 | NB-3 | full |
| R-37 | Server-sent events stay, fixed and not replaced | G-51 | 02, 03, NB-4 | full |
| R-38 | `math_engine.py` carries zero diff and `alpha_bot_execution.py` changes only in the close-snapshot builder | G-52 | NB-5, 01 | full |
| R-39 | Trade, liquidation and order request payloads stay exactly as they are | G-53 | NB-6, 03, 04 | full |
| R-40 | `LIVE_EXECUTION` stays exactly as it is | G-54 | NB-7, 04 | full |
| R-41 | The engine's once-a-minute cadence, around the clock, stays | G-55, G-8 | NB-8, 02 | full |
| R-42 | The 1-minute execution path gets no blocking I/O | G-56 | NB-9, 02 | full |
| R-43 | The value each metric computes stays, except where two computations disagree inside one response (TA-6) | G-57 | NB-10, 01 | full |
| R-44 | The database schema changes only additively | G-58 | NB-11 | full |
| R-45 | Findings the audit marks covered stay with their plans and are named under each plan's earlier criteria, not re-specified | G-59, G-60 | NB-12, 01, 02, 03, 04, 05 | full |
| R-46 | Composer, Alpaca and Anthropic APIs are consumed, never changed | G-61 | NB-13 | full |
| R-47 | After the close the frozen state carries each triggered card's guard alpha (TA-3 server half) | G-63 | 01 | full |
| R-48 | The frozen branch checks and states its snapshot's trading day (TA-7) | G-63 | 01 | full |
| R-49 | After the close the frozen figures do not drift with live account totals, and the window routes agree with the frozen state (TA-8) | G-63 | 01 | full |
| R-50 | The frozen branch carries every card's state and values, and the server render after the close uses them (TA-9 server half) | G-63 | 01 | full |
| R-51 | A poll that lands mid-cycle never pairs two cycles' values (TA-14) | G-63 | 01 | full |
| R-52 | Server freshness stamps name the data's moment, never the request time or a cycle that fetched nothing (TA-18 server stamps) | G-63 | 01 | full |
| R-53 | The verdict figure's label tells the truth about whether it moves (TA-23) | G-63 | 01 | full |
| R-54 | No renderer writes to a missing element, and the server stops computing what no client reads (TA-40) | G-63 | 01 | narrowed: the unread but documented `/api/state` fields (`html`, `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time`) stay (plan 01 Q-4) |
| R-55 | The account-basis stale chip is not raised by the routine post-cycle refresh (TA-12) | G-64 | 02 | full |
| R-56 | The scheduled 09:30 start and the 15:53-15:59 blackout never read as a fault (TA-17) | G-64 | 02 | narrowed: the dated as-of before the first 09:30 cycle is R-52's rule, delivered by plan 01 |
| R-57 | The event stream opens at once on connect (TA-31) | G-64 | 02 | full |
| R-58 | After a restart the account totals are filled before the first engine minute (TA-33) | G-64 | 02 | full |
| R-59 | On the UTC host every "today" the server uses is the ET trading date (TA-39) | G-64, G-4 | 02 | narrowed: the user-visible date consequences on `/api/chart` (TA-29) and `/api/history` (TA-28) are delivered by plans 03 and 04 |
| R-60 | A daemon-side reader of the shadow trajectory sees the engine's newest rows (TA-42) | G-64 | 02 | full |
| R-61 | The guard-alpha headline keeps the window the operator picked through every refresh (TA-2) | G-65 | 03 | full |
| R-62 | `/` shows a stale cue by data age, catches up at once on tab return, wake and network return, and its "Stale" check does not trust the browser clock (TA-19) | G-65, G-15 | 03 | full |
| R-63 | The LIVE / DRY RUN mode pill and the hero legend follow the live flag without a reload (TA-21) | G-65 | 03, 04 | full |
| R-64 | The Ann. Vol row keeps the picked window through every refresh (TA-22) | G-65 | 03 | full |
| R-65 | The open detail panel's numbers follow every refresh (TA-24) | G-65 | 03 | full |
| R-66 | The detail panel shows only the symphony it is open on (TA-25) | G-65 | 03 | full |
| R-67 | The comparison bars' winner and loser follow every refresh (TA-26) | G-65 | 03 | full |
| R-68 | Cash Now is disabled on a card that exits while the page is open (TA-27) | G-65 | 03 | full |
| R-69 | Sparklines and MC dials show only the current session's readings (TA-29) | G-65 | 03 | full |
| R-70 | The NEXT countdown is true and keeps one format (TA-32) | G-65 | 03 | full |
| R-71 | The Sleeves panel, the CVaR panel and the per-card labels follow every refresh (TA-36) | G-65, G-87 | 03 | full |
| R-72 | The stored theme applies before first paint and across open tabs (TA-41) | G-65 | 03 | full |
| R-73 | A triggered card keeps its verdict after the close (TA-3 client half) | G-65 | 03 | full |
| R-74 | After the close and pre-market the cards and section badges match the poll (TA-9 client half) | G-65 | 03 | full |
| R-75 | Plan 03 supersedes ux-overhaul plan 05's S-4.7 and NB-4 and records both, with the reason, under Earlier criteria | G-66 | 03 | full |
| R-76 | The header on `/history`, `/performance`, `/ai-advisor` and `/settings` refreshes (TA-20) | G-67 | 04 | full |
| R-77 | `/history` keeps a drilled day through the poll, keeps "Today's exits" on the ET date, never draws an error as data and shows its data age (TA-28) | G-67 | 04 | full |
| R-78 | `/performance` renders each panel independently, keeps the last good rows on a failed request and shows its data age (TA-34) | G-67 | 04 | full |
| R-79 | `/ai-advisor` never shows an error as an empty list and signals a Frontrunner build's completion (TA-35) | G-67, G-90 | 04 | full |
| R-80 | `/settings` shows a load failure and its flush confirm reads a small response (TA-37) | G-67 | 04 | full |
| R-81 | Daemon output reaches the journal line by line (TA-30) | G-10, G-68 | 05 | full |
| R-82 | The audit's section 8 production measurements are this suite's live verification | G-68 | 05 | full |
| R-83 | The model-unpin plan lands first; it edits `app.py`, `templates/ai_advisor.html`, `static/ai_advisor.js` and other advisor files | G-70, G-71, G-69 | 01, 02, 03, 04, 05 | full |
| R-84 | Plans 01, 02 and 05 start from origin/main and edit `app.py` as ux-overhaul 05 C3 does | G-72, G-73 | 01, 02, 05 | full |
| R-85 | Plan 03 stacks on the assembled `ux-overhaul/05` branch | G-74 | 03 | full |
| R-86 | Plan 04 stacks after ux-overhaul plan 04 | G-75 | 04 | full |
| R-87 | The overview records the file overlaps with the UX overhaul suite | G-76 | 03, 04 | full |
| R-88 | Each plan lists every earlier criterion on its surface with a disposition | G-77, G-78, G-79, G-80, G-81, G-82 | 01, 02, 03, 04, 05 | full |
| R-89 | Open questions are answered by recorded defaults, never by stopping | G-12 | 01, 02, 03, 04, 05 | full |
| R-90 | A refresh with unchanged data sends no per-symphony `/api/chart` requests (TA-13, the chart fan-out) | G-1 | 03 | full |
| R-92 | Before the one-time repair writes, the daemon saves a copy of the pre-repair state row so the repair can be reversed by hand; a failed copy means no repair that start, a logged reason with the exception type name only, and a retry at the next start | G-83, G-84, G-85, G-86 | 01 | full |
| R-93 | `POST /api/fleet-alert/dismiss` answers only after its write lands, within a bounded wait, and answers 503 when it fails | G-91 | 04 | full |
| R-94 | The dashboard's refresh counter counts each newest request's handled outcome, rendered or failed, and never a superseded one | G-88 | 03 | full |
| R-95 | When the picked window has no data, the hero keeps its previous series with a visible no-data-yet cue | G-89 | 03 | full |
| R-96 | The existing ERROR lines for a failed account refresh and a failed flush resync log the exception type name only, with the detail at DEBUG | G-92 | 05 | full |
| R-97 | The header reads "Degraded", with a tooltip that describes each reason in a fixed plain sentence (never the raw key), whenever `/health`'s `degraded_reasons` is non-empty, ranked under "Connection Lost" and "Not updating" | G-93 | 03, 04 | full |
| R-98 | A failed `POST /api/settings/flush-resync` answers with the exception type name only, never exception text | G-94 | 04 | full |
| R-91 | `/api/state` stops re-aggregating every minute-level `shadow_history` row on every poll (TA-13, the per-poll recompute) | G-1 | NB-17 | full |

## Not Building
Non-goals:
- NB-1: Any repository other than PlanetStopper (excludes R-33; approved by the PM, [PM-ASSUMED]).
- NB-2: A visual redesign, new layouts, new tokens or new primitives; the UX overhaul suite and Claude Design own visuals, and every new cue in this suite uses an existing primitive (excludes R-35; approved by the PM, [PM-ASSUMED]).
- NB-3: New data sources: no new Composer, Alpaca or Anthropic endpoint and no new engine producer; every refreshed datum is one the server already stores, except one append-only `FRONTRUNNER_BUILD_SUMMARY` row in the existing `advisor_observations` table per Frontrunner on-demand build (plan 04 AC-5), which follows the `RETIREMENT_RUN_SUMMARY` precedent and needs no schema change (PM amendment, [PM-ASSUMED]) (excludes R-36; approved by the PM, [PM-ASSUMED]).
- NB-4: Replacing server-sent events with WebSockets or with a faster poll; the event stream is fixed in plans 02 and 03 (R-37; approved by the PM, [PM-ASSUMED]).

Behaviour that stays exactly as it is:
- NB-5: The engine's exit-decision path: `math_engine.py` carries zero diff, and `alpha_bot_execution.py` changes only inside the close-snapshot builder (`alpha_bot_execution.py:1115-1163`) (excludes R-38; approved by the PM, [PM-ASSUMED]).
- NB-6: Trade, liquidation and order request payloads: `POST /api/sell_account`, the Cash Now sell, the Managed Sleeves order path and the engine's own orders send the same URL, method, headers and body as at the ref (excludes R-39; approved by the PM, [PM-ASSUMED]).
- NB-7: `LIVE_EXECUTION`, each symphony's `live_mode` and the settings write allowlist (excludes R-40; approved by the PM, [PM-ASSUMED]).
- NB-8: The engine's once-a-minute spawn at every `:00`, around the clock. At the ref the engine's own lock skips a minute only while the running engine is under 60 s old (`alpha_bot_execution.py:611-613`, `database.py:299`); plan 02 AC-5 adds the skip for runs longer than that, and every other minute still spawns (excludes R-41; approved by the PM, [PM-ASSUMED]).
- NB-9: No blocking I/O is added to the 1-minute execution path (excludes R-42; approved by the PM, [PM-ASSUMED]).
- NB-10: The value each metric computes, except that one computation replaces two inside one `/api/state` response (TA-6, plan 01 AC-2), that a frozen figure reads its account totals as of its snapshot (plan 01 AC-5, Q-1), and that a guard alpha whose `current_return` is absent is left absent instead of computed against 0.0 (plan 01 S-6.4); the last two go beyond G-57's single exception and are recorded as SD-12 for the operator's ruling (excludes R-43; approved by the PM, [PM-ASSUMED]).
- NB-17: The per-poll re-aggregation of minute-level `shadow_history` into daily series (3.02 s of a 3.36 s `/api/state` in the audit's profile, part of TA-13); plan 01 AC-2 removes the duplicate computation (1.69 s), plan 05 S-6.1 measures what remains on the droplet, and caching the daily series is its own performance plan (excludes R-91; approved by the PM, [PM-ASSUMED]).
- NB-11: The database schema, except additive changes (excludes R-44; approved by the PM, [PM-ASSUMED]).

Deferred:
- NB-12: Findings the audit marks covered stay with their plans and are not re-specified: TA-11 (the newest-request guard on `loadState`, ux-overhaul 05 C3 round 2, RED `eeba10be` and GREEN `c20265db` on `ux-overhaul/05-c3`), TA-43 to TA-50 (ux-overhaul 05 AC-1 to AC-7), TA-51 (ux-overhaul 04 AC-8) and TA-52 (ux-overhaul 03 AC-2) (excludes R-45's re-specification; approved by the PM, [PM-ASSUMED]).
- NB-15: Real defects the audit found that are not timing: a silent Chart.js CDN failure (its TC-27; ux-overhaul 05 NB-2 keeps the tag) and the Strategy Builder's "Chat about this" hand-off writing one storage key while the chat reads another (its TC-32, `static/ai_advisor.js:742` against `static/ai_advisor_chat.js:296`); each opens its own plan (approved by the PM, [PM-ASSUMED]).
- NB-16: Found during this spec's investigation, outside the audit: post-mortem stage 1 counts every dict-valued `bot_state` key, the close snapshot included, in `total_monitored` (`reporting.py:51-55`), and the EOD divergence loop logs a false "coverage gap" ERROR for the snapshot key every close (`alpha_bot_execution.py:1066-1075`); both sit outside the close-snapshot builder the PM allowed (G-52), so each is a follow-up (approved by the PM, [PM-ASSUMED]).

Protected surfaces (consumed, never changed):
- NB-13: Composer, Alpaca and Anthropic APIs (excludes R-46; approved by the PM, [PM-ASSUMED]).
- NB-14: The no-trade boundary: `POST /ai-advisor/proposal/approve` (the only route that reaches Composer symphony creation, which stays undeployed), `POST /api/sell_account` behind Emergency Liquidate, the Managed Sleeves arming ladder, `LIVE_EXECUTION` with each symphony's `live_mode`, the settings write allowlist, and the retirement approve, reject and run routes; their boundary tests (`tests/security/test_frontrunner_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`, `tests/app/test_sell_account_panic_confirm.py`, `tests/app/test_f003_liquidation_confirmation.py`, `tests/app/test_dashboard_no_order_path.py`) stay green on the assembled stack (approved by the PM, [PM-ASSUMED]).

## Plans and Dependency Graph
| # | Plan | Repos | Base | Contract exposed upward | Status | Depends on | Requirement items |
|---|---|---|---|---|---|---|---|
| 01 | [Server state truth](01-server-state-truth.md) | PlanetStopper | main | `/api/state` carries one portfolio computation, dated freshness fields (SD-1), the frozen `trading_day` and previous-session marker, and in the frozen branch a `symphonies` card list and each triggered entry's `guard_alpha`; the close snapshot holds no nested snapshot | planned | model-unpin merged into its tip before its PR; ux-overhaul 05 on `main` and merged into its tip before its AC-6, AC-8 and AC-9 cycles | R-1, R-2, R-4, R-5, R-7, R-9, R-13, R-15, R-25, R-26, R-31, R-34, R-38, R-43, R-45, R-47, R-48, R-49, R-50, R-51, R-52, R-53, R-54, R-83, R-84, R-88, R-89, R-92 |
| 02 | [Scheduler, cycle event and health](02-scheduler-events-health.md) | PlanetStopper | main | The `cycle-complete` payload (`cycle_id`, `completed_at`, `outcome`, `changed`, SD-3); `/health` with scheduler liveness and a degraded status; `/api/state` field `engine_phase` for the scheduled pauses | planned | model-unpin merged into its tip before its PR | R-1, R-3, R-4, R-6, R-7, R-9, R-12, R-14, R-15, R-17, R-18, R-19, R-28, R-29, R-37, R-41, R-42, R-45, R-55, R-56, R-57, R-58, R-59, R-60, R-83, R-84, R-88, R-89 |
| 03 | [Dashboard refresh](03-dashboard-refresh.md) | PlanetStopper | the assembled `ux-overhaul/05` tip | Every element of `/` follows every refresh in every market state, recovers after a restart or a sleep, and keeps the operator's selections | planned | ux-overhaul 05 (C1 to C3 with round 2); 02 merged into its tip before the AC-2, AC-3 and AC-4 cycles; 01 merged into its tip before the AC-5, AC-6 and AC-9 cycles | R-1, R-2, R-3, R-4, R-5, R-6, R-7, R-9, R-10, R-11, R-12, R-13, R-14, R-15, R-16, R-20, R-23, R-27, R-37, R-39, R-45, R-61, R-62, R-63, R-64, R-65, R-66, R-67, R-68, R-69, R-70, R-71, R-72, R-73, R-74, R-75, R-83, R-85, R-87, R-88, R-89, R-90, R-94, R-95, R-97 |
| 04 | [Pages and header](04-pages-and-header.md) | PlanetStopper | the ux-overhaul 04 tip | Every page's header follows the engine; every page's POST controls survive a restart without ever re-sending; `/history`, `/performance`, `/ai-advisor` and `/settings` refresh honestly | planned | ux-overhaul 04; 02 merged into its tip before its header cycle; 03 merged into its tip before its AC-2 cycle | R-1, R-3, R-4, R-6, R-7, R-10, R-11, R-13, R-14, R-15, R-16, R-21, R-22, R-23, R-24, R-27, R-34, R-39, R-40, R-45, R-63, R-76, R-77, R-78, R-79, R-80, R-83, R-86, R-87, R-88, R-89, R-93, R-97, R-98 |
| 05 | [Observability and transport](05-observability-transport.md) | PlanetStopper | main | Line-buffered daemon output; one log line per engine cycle, per Composer call and per request; gzip through Caddy; the section 8 measurement record | planned | model-unpin merged into its tip before its PR; 02 merged into its tip before its cycle-log cycle (SD-3) | R-1, R-7, R-8, R-30, R-32, R-45, R-81, R-82, R-83, R-84, R-88, R-89, R-96 |

## Stack and Merge Rules
- The model-unpin plan (`feature-plans/model-unpin.md`, committed on branch `model-unpin/fixtures` at 291454eb with its source, audit, review and approach) lands first. It edits `app.py` (including `run_scheduler`, where its C9 cycle adds a daily 04:15 warm-up slot, and the startup block), `templates/ai_advisor.html`, `static/ai_advisor.js` and other advisor files; it merges into each plan's tip before that plan's PR.
- Two roots start now. Plans 01, 02 and 05 branch from origin/main and run in parallel: they are server-side, but all three edit `app.py`, as ux-overhaul 05 C3 does around `/api/state` and `dashboard()`. 01 edits `get_state()`, `get_api_state_dict()`, `_compute_portfolio_strip()`, `dashboard()`, `/api/strip`, `/api/hero-chart` and the engine's close-snapshot builder; 02 edits `trigger_alpha_bot()`, `_notify_cycle_complete()`, `run_scheduler()`, `/health`, `/api/events`, the startup block, the live branch's date reads and `static/chrome.js`'s engine-status label; 05 edits the stdout setup, the request and Composer-call logging, `docs/DEPLOYMENT.md` and the cycle log line inside `trigger_alpha_bot()`. Overlaps are resolved by merge commits, never by rebasing. Further overlaps: 02's `static/chrome.js` `updateEngineStatus` edit meets ux-overhaul 04's rewrite of that file, resolved when plan 04's tip merges 02; 01's AC-8 (dead writers) and AC-9 (verdict label) edit `static/index.js` and `templates/index.html`, which ux-overhaul 05 and plan 03 also edit, so those two cycles start only after ux-overhaul 05 is on `main`; 03's AC-10 adds a pre-paint script to the head of every page template, which ux-overhaul 04 rewrites, resolved when plan 04's tip merges 03; and 03 edits `static/chrome.js` (the label precedence of SD-13, the NEXT countdown and the Stale check), which ux-overhaul 04 also rewrites, resolved the same way (PM amendment, [PM-ASSUMED]).
- Plan 03 stacks on the assembled `ux-overhaul/05` branch (C1 61707cd6, C2 d10040f7, C3 67cf9ca3, plus C3 round 2, whose RED `eeba10be` and GREEN `c20265db` on `ux-overhaul/05-c3` add the newest-request guard to every `loadState` caller); at the time of writing the three cycles sit on `ux-overhaul/05-c1`, `-c2` and `-c3` and the assembled branch does not exist yet. Plans 01 and 02 merge into 03's tip before the 03 cycles that consume their contracts (the frozen card list and guard alpha, the dated stamp, the event payload). Overlap: 03 and ux-overhaul 05 both edit `static/index.js` and `templates/index.html`.
- Plan 04 stacks after ux-overhaul plan 04 (the app shell), which rewrites every page header and the shared chrome. Plan 02 merges into 04's tip before 04's header cycle, because the header's scheduled-pause label (02 AC-6) lives in the same `static/chrome.js` function 04 drives on the other pages. Overlap: 04 and ux-overhaul 04 both edit `static/chrome.js` and every template's header.
- Plan 05 merges plan 02 into its tip before its cycle-log cycle, because the log line carries 02's cycle id and outcome (SD-3).
- Each plan lands as one PR to `main`. Each PR is verified against its own base: `ruff format --check`, `ruff check`, a scoped pytest run of the test directories its files touch (never `-n auto`, never more than 4 workers), CI on the exact SHA, and the plan's own live check. Every merge across the stack is a merge commit, never a squash; no branch is rebased or force-pushed.
- Under the project's merge rule (`.claude/CLAUDE.md`, Merge and PR Workflow), the PM merges each PR after its own `/review` and the PM's live functional check on the droplet, in this order: 02 before 05, because 05's tip carries 02's commits; 01 only after ux-overhaul 05 is on `main`, because 01's tip carries it; then 03, after 01 and 02; then 04 last. No PR may carry another plan's unmerged commits to `main`. This replaces the epic template's "the merge to main is the operator's call" ([PM-ASSUMED]; the operator may override). The Suite Gate runs once, at the last PR, against `main` plus that PR.

## Shared Decisions
Each is a default the operator may override at approval.

| ID | Decision | Rationale |
|---|---|---|
| SD-1 | Every data freshness stamp the server sends or a page shows reads `YYYY-MM-DD HH:MM ET`: the ET date and 24-hour time of the data, never of the request; a frozen snapshot from an earlier session than the most recent completed one adds ` · previous session`; machine fields stay ISO 8601 with an offset; two fields keep seconds, in the form `YYYY-MM-DD HH:MM:SS ET`: `account_basis_as_of` (its existing form) and `frozen_at` (the snapshot's capture moment, plan 01 AC-3) | The operator asked for a date on every stamp (G-22, G-36). ux-overhaul SD-2 already rules that a dated stamp adds the date to the dashboard's `HH:MM ET` form, and ux-overhaul 03 AC-2 writes "2026-10-21 23:45 ET" |
| SD-2 | A stamp with no known data moment reads `--`; no server path falls back to the request clock | Five fallbacks stamp `datetime.now(_ET)` over old or missing data (`app.py:2312`, `:2314`, `:2403`, `:3376`, `:3378`) |
| SD-3 | Each engine run has one `cycle_id` and one `outcome` from a closed vocabulary that plan 02's Contract Changes define; the event (02), `/health` (02) and the cycle log line (05) use the same values | Three plans describe the same run; one vocabulary keeps the event, the health probe and the log joinable |
| SD-4 | Plans 02, 03 and 05 are also Sensitive: 02 (money) decides when the engine is spawned or skipped and keeps the scheduler alive, and a dead scheduler stops every trailing stop (TA-15); 03 (money) changes Cash Now's enabled state on `/`; 05 (security) adds request and Composer log lines that must never carry a cookie, token, password or account id | Deviation from G-46 to G-48, which name only 01 and 04; the project's sensitive-marking rule covers anything that drives a real-money trade decision, and credentials are security |
| SD-5 | No POST is ever re-sent automatically on any page: a POST refused for a stale token leaves the action unsent, says so, and waits for a new click with the refreshed token | G-30 to G-32; Emergency Liquidate and Cash Now move money |
| SD-6 | Newest issued wins: for every request whose response writes the screen, a response older than the newest request issued for the same element is discarded; the rule is per tab, so two tabs never affect each other | G-33, G-34; the audit found no newest-wins guard anywhere but `loadState` (TA-11, TA-25, TA-38) |
| SD-7 | Each test fixture is one record per case, captured from the production state database copy the audit used or from a captured route response; a case no capture holds is derived from a captured record with only the named field changed and signed off by the PM as producer owner | The project's fixture rule; a fixture built with its parser proves only that the parser agrees with itself |
| SD-8 | Browser-only tests carry the `browser` pytest marker, are deselected by default and run at each merge gate, as in the UX overhaul suite | CI has no browser |
| SD-9 | TA-21's owner is plan 03, which refreshes the mode pill and hero legend on `/`; plan 04's header refresh carries the pill on the other four pages | Deviation from "exactly one plan" (G-62): those four pages have no refresh until plan 04 adds one, so plan 03 cannot reach them |
| SD-10 | The droplet clock is UTC. Daily slots keep their host-clock times (02:00, 03:00, 03:30 and 03:45, plus model-unpin's 04:15), as ux-overhaul SD-10 already labels the nightly retirement run "03:45 UTC"; every "today" a route computes means the ET trading date | Moving the slots would change the retirement and model-unpin schedules other plans already document (plan 02 Q-1) |
| SD-12 | Two value changes beyond G-57's single exception, raised for the operator's ruling: after the close the frozen Today, Cumulative, account value and all-time figures read the account totals of the snapshot's moment instead of the live cache (plan 01 AC-5), and a triggered card whose `current_return` is absent gets no guard alpha instead of one computed against 0.0 (plan 01 S-6.4) | Both are value changes, so G-57 does not cover them; the first stops figures drifting all evening under a 16:00 stamp (TA-8), the second stops a fabricated figure (the project's None-is-not-0 rule) |
| SD-13 | One precedence for the header's engine label on every page: "Connection Lost" (the last refresh failed) over "Not updating" (no data for 90 s by the page's own receipt time) over "Degraded" (`/health`'s `degraded_reasons` is non-empty; its tooltip gives the suite approach RC-1's fixed plain sentence for each reason and never the raw key) over "Closed" over "Rebalance pause" over "Opening" over "Stale" over "Live". Only `degraded_reasons` sets "Degraded"; `status`, `scheduler_last_tick_at`, `engine_running_since` and `last_cycle` change no label. "Stale" is judged by the age of the last cycle as the server reckons it, never by the browser clock against a server time. The mode pill has one writer per page, `updateModeIndicators(meta)` in `static/chrome.js`, called by `static/index.js` on `/` and by plan 04's header poller elsewhere (amended by the PM from `feature-plans/refresh-timing/approach/00-suite.md`, [PM-ASSUMED]) | Five rules across plans 02, 03 and 04 write one element (TA-17, TA-19, TA-10), and a degraded daemon must show on every page |
| SD-14 | The PM's amendments from the five Gate-2 approaches (`feature-plans/refresh-timing/approach/`) are recorded in each plan's Decisions as PM rows, [PM-ASSUMED] | The operator is not available; the project directive is to record assumptions and not stop |
| SD-11 | The section 8 production measurements are taken by the PM on the droplet after deploy, read-only, and recorded in the stack's review folder | They need the droplet, a session cookie and market hours; no CI job can take them |

## Shared Defect Classes
| DC-n | Class | Trigger | Sibling search (tool, query, scope across every plan's repos) |
|---|---|---|---|
| DC-1 | One element written by two refresh paths on different windows or bases | N5 | grep each element id and `data-field` written in `static/index.js`, `static/chrome.js`, `static/history.js`, `static/performance.js` and `static/ai_advisor.js` for more than one writer; PlanetStopper |
| DC-2 | A freshness stamp from the request clock, or without a date | N3 | grep `datetime.now(` within ten lines of `as_of`, `_at`, `stamp` or `data_as_of` in `app.py`, `analytics.py` and `reporting.py`, and `new Date(` used for display in `static/`; PlanetStopper |
| DC-3 | A host-local date where the ET trading date is meant | N1 | grep `datetime.now()` with no zone argument, `date.today()`, `_date.today()` and `.strftime("%Y-%m-%d")` in `app.py`, `analytics.py`, `database.py` and `reporting.py`; PlanetStopper |
| DC-4 | A response drawn without checking its status or shape | N6 | grep `return r.json()`, `return resp.json()` and `.json())` in `static/` and `templates/`; check each has an `ok` or shape check before rendering; PlanetStopper |
| DC-5 | A response applied without a newest-issued guard | N5 | grep `fetch(` in `static/` and `templates/`; each whose result writes the DOM needs SD-6's guard; PlanetStopper |
| DC-6 | A server-rendered element no refresh ever updates | B | read: every `data-testid` and `data-field` in `templates/index.html` and `templates/_chrome.html` against the writers in `static/`; PlanetStopper |
| DC-7 | A POST that can be sent twice or re-sent without a click | N5 | grep `method: 'POST'`, `method:'POST'` and `method: "POST"` in `static/` and `templates/`; each must have no retry path; PlanetStopper |
| DC-8 | A changed field, format or test id not carried to every consumer, tests pinning the old string included | B | grep each changed field name, the `%H:%M ET` format and each renamed test id in `static/`, `templates/`, `app.py` and `tests/`; PlanetStopper |
| DC-9 | A non-symphony `bot_state` key treated as a symphony | N1 | grep loops over `bot_state` or `state_data` that test only `isinstance(..., dict)` in `alpha_bot_execution.py`, `app.py`, `analytics.py` and `reporting.py`; PlanetStopper |

## Suite Gate
The items below run once, at the last plan's PR, against `main` (which by then holds the other four plans) plus that PR; each earlier PR has passed its own `/review` and live check:
1. `ruff format --check` and `ruff check` clean; a pytest run of the full tree in batches of at most 4 workers, never `-n auto`, zero failures and zero errors, quoting the head SHA; CI green on that exact SHA; the `browser` tests (SD-8) run once.
2. `git diff main...HEAD -- math_engine.py` is empty across the stack, `git diff` of `alpha_bot_execution.py` touches only the close-snapshot builder, and the NB-14 boundary tests pass.
3. The PM's live behavioural test on the droplet after deploy and `/review`, recorded in the review folder for the stack SHA: keep `/` open from before 09:30 through 16:10 ET and into the evening, with `/history`, `/performance`, `/ai-advisor` and `/settings` open in other tabs. Record at 09:29, 09:31, 12:00, 15:55, 16:01 and 16:10: the hero as-of text, the header label on every tab, each triggered card's verdict and the Today row. Pick 1Y and check the headline and Ann. Vol through three refreshes; open a detail panel through two cycles; restart the daemon once with every tab open and record the event stream's reconnect, the header recovery and one Settings save without a reload; put the laptop to sleep for five minutes and record the catch-up refresh; and confirm in DevTools that `/api/state` arrives with `Content-Encoding: gzip`.
4. The section 8 measurements (SD-11, plan 05 AC-6), each with its number and the read-only command that produced it.

## Assembly and Close
The suite is complete when every plans-table row reads merged, the Suite Gate's four items are recorded with the last PR's head SHA, and the operator has the live-test record. The evidence lives in the review folder named for that head SHA under `.claude/review/`, with the pytest and CI records, the live-test notes and the measurement record, and is summarised in the last PR's body. The PM performs each merge to main after that PR's `/review` and live functional test, as the project's merge rule requires ([PM-ASSUMED] under the standing autonomy directive; the operator may override); after it, the doc writer appends the `DECISIONS.md` entries and the `.claude/CLAUDE.md` Key Files rows each plan drafts.
