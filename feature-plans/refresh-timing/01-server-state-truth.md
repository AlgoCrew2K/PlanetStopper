# Feature: Server state truth: one dated, consistent `/api/state` and a flat close snapshot
Status: planned
Story: none: operator chat request on 2026-10-22, saved verbatim in feature-plans/refresh-timing/00-overview.source.md
Type: fix
Suite: refresh-timing 01
Repos: PlanetStopper
Base: main
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864
Sensitive: money (AC-1)
Depends on: the model-unpin plan (branch `model-unpin/fixtures`, 291454eb) merged into this plan's tip before its PR, because both edit `app.py`; ux-overhaul plan 05 merged to `main` and `main` merged into this plan's tip before the AC-6, AC-8 and AC-9 cycles, because AC-8 and AC-9 edit `static/index.js` and `templates/index.html`, which ux-overhaul 05 rewrites (AC-9 changes the verdict text its AC-2 writes), and AC-6 builds on its server-render guard-alpha injection and card harness; this plan's PR merges only after ux-overhaul 05 is on `main`

## Trigger
The operator wrote "I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll" (G-1, saved in `feature-plans/refresh-timing/00-overview.source.md`). The refresh-timing audit at the ref (a review artifact outside the tracked tree, dated 2026-10-22, measured on a copy of the production state database taken at 14:53 ET and served after 16:00, so in the frozen branch) assigned this plan the server-side causes. Each was re-read at the ref for this spec:

1. TA-1: the EOD step's key list is every dict-valued `bot_state` key (`alpha_bot_execution.py:1066`), which includes `last_market_close_snapshot` itself; the builder appends it to the account map under "Unknown" as `{"id": "last_market_close_snapshot", ...}` (`alpha_bot_execution.py:1120-1126`) and stores that map in the new snapshot (`:1153-1163`). Each close therefore nests every earlier snapshot. Measured: 86 snapshots nested (2026-06-22 to 2026-10-21), a 2,039,413 B `bot_state` row, a 5,666,593 B after-close `/api/state` body carrying the chain three times (`accounts_map`, `state`, `bot_state`), growing about 23 KB per trading day per copy. The reserved-key sets that would exclude it exist and are unused here (`database.py:380`, `alpha_bot_execution.py:2128`). The frozen branch's state builder takes every account-map entry that has an `id` (`app.py:2645-2648`), so the phantom reaches the client, which requests `/api/chart/last_market_close_snapshot` on every after-hours poll.
2. TA-6: `get_state()` calls `get_api_state_dict()` (`app.py:2520`), which computes `_compute_portfolio_strip` from live state (`app.py:2462`); `meta` is built from that first computation (`app.py:3089` frozen, `:3506` live) while the top-level `portfolio_strip` is computed again (`app.py:3351`) or, after the close, recomputed from the snapshot's entries with the live account totals (`app.py:2831`; the snapshot's stored `portfolio_strip` is never served). The client reads the hero, its as-of and Ann. Vol from `meta.portfolio` and the Today, Cumulative and Max DD rows and the headline from `portfolio_strip`. Measured in one after-close response: Today bot -0.0073 against -3.4986, Cumulative 41.364 against 34.403, as-of "16:00 ET" against "14:53 ET". The live-only computation cost 1.69 s of a 3.36 s request.
3. TA-7 and TA-18: the frozen branch serves whatever snapshot exists with no trading-day check (`app.py:2629-2632`); every stamp is `%H:%M ET` with no date (`app.py:2310`, `:2877`, `:3374`); five paths fall back to the request clock (`app.py:2312`, `:2314`, `:2403`, `:3376`, `:3378`); and the engine stamps `last_successful_cycle_at` even when every Composer fetch failed (`alpha_bot_execution.py:190-204`, `:2007`).
4. TA-8: after the close the frozen figures are wrapped with the live account-totals cache read at request time (`app.py:2892-2910`), refreshed twice a minute all night, and `/api/strip/{window}` and `/api/hero-chart/{window}` have no frozen branch (`app.py:3639-3670`, `:3565`). Measured: 0.638 from the strip route against 0.680 from the frozen poll.
5. TA-3 and TA-9 (server halves): guard alpha is injected only in the live branch (`app.py:3407-3421`); the frozen response carries no `symphonies` list (`app.py:3072-3093`), while it computes per-symphony Today, Cumulative and Max DD for every snapshot entry (`app.py:2748-2791`) that no client reads; `dashboard()` renders cards from live state with today's ET date (`app.py:1695`, `:1710`). Measured: 12 symphonies, 6 triggered, `guard_alpha` on none.
6. TA-14: the engine commits `shadow_history` in its data phase (`alpha_bot_execution.py:953`) and `bot_state` at the end (`:2048`); `/api/state` opens a fresh connection per `load_state` over about 3.4 s, so a mid-cycle poll pairs this minute's Bot leg with last minute's Held leg.
7. TA-23: the verdict figure is the latest exit row's `at_return` minus the symphony's `current_return` (`app.py:3416-3418`), where `current_return` is the live if-held value, so it moves every cycle, while the template comment and the label say it is fixed "at exit" (`templates/index.html:1240-1252`).
8. TA-40: `updateStatusStrip`, `updateMarketDot` and the `#hero-ticker` writer target ids no template has (`static/index.js:1396-1399`, `:1424-1446`); `meta.triggers_today` is computed on every poll (`app.py:1886-1898`) and read only by the dead `updateStatusStrip`; the comment at `static/index.js:1591` says the poll floor is 15 s while `POLL_INTERVAL_MS` is 30000 (`static/index.js:11`).

Found during this spec's investigation: the live branch injects guard alpha with `current_return or 0.0` (`app.py:3416`), so a symphony whose `current_return` is absent gets a guard alpha computed against 0.0; a test pins `alpha_bot_execution.py`'s content hash (`tests/test_mdd_window_truth_zero_diff_guard.py:69`), which AC-1's builder change breaks by design.

## Summary
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

## Requirements as Given
[G-1] I expect any and all timing issues to be resolved as well, I'm tired of the wonky updates and / or no updates to data on the poll. 00-overview.source.md:6
[G-12] PM Step-2 answers ([PM-ASSUMED]; the operator is not available for questions; project directive: record assumptions, do not stop) 00-overview.source.md:18
[G-13] (Building 1) Every number, chart, label and status on every page shows the newest data the engine has produced. 00-overview.source.md:21
[G-14] (Building 1) It updates without a reload, never reverts or flickers when no new data arrived, and never shows stale or wrong-day data without a dated, honest cue. 00-overview.source.md:21
[G-16] (Building 2) Users: the operator, through `/`, `/history`, `/performance`, `/ai-advisor`, `/settings` and the shared header. 00-overview.source.md:22
[G-17] (Building 2) The server side is the minute scheduler, the engine's close snapshot, `/api/state`, `/api/events`, `/health` and the other GET routes the pages poll. 00-overview.source.md:22
[G-19] (Building 3) Flows: keep `/` open through a whole trading day: pre-market, the 09:30 open, intraday, the 15:53-15:59 blackout, the 16:00 close, the evening and overnight. 00-overview.source.md:23
[G-22] (Building 3) Expected: values change within one cycle of new data; the operator's selections persist through polls; every freshness stamp carries a date and its true age; after a restart, live updates, actions and the header recover without a reload. 00-overview.source.md:26
[G-36] (Building 5) `/api/state` freshness fields carry a date and time, and the frozen branch states its snapshot date. 00-overview.source.md:38
[G-37] (Building 5) One portfolio computation per response. 00-overview.source.md:38
[G-42] (Building 5) The stored `bot_state.last_market_close_snapshot` holds one snapshot with no nested earlier snapshot. 00-overview.source.md:43
[G-43] (Building 5) A one-time repair of the stored state keeps the latest close's values exactly. 00-overview.source.md:43
[G-47] (Building 7) Plan 01 changes the engine file's close-snapshot builder. 00-overview.source.md:47
[G-52] (Not building 2) Must stay exactly as it is: the engine's exit-decision path (`math_engine.py` zero diff; `alpha_bot_execution.py` changes only in the close-snapshot builder); 00-overview.source.md:52-53
[G-57] (Not building 2) the value each metric computes, except where the audit shows two computations disagreeing inside one response (TA-6), where one computation replaces two; 00-overview.source.md:58
[G-59] (Not building 3) Items the audit marks COVERED stay with their plans (ux-overhaul 05, C3 round 2, 04 AC-8/AC-9, 03 AC-2). 00-overview.source.md:60
[G-63] 01-server-state-truth. TA-1 (engine close-snapshot nesting plus the one-time stored-state repair), TA-3 server half (the after-close branch carries the per-card guard alpha the verdict needs), TA-6, TA-7, TA-8, TA-9 server half, TA-14, TA-18 server stamps, TA-23 (verdict "at exit" moving each cycle: propose a default in Open Questions, either truthful labelling or freezing the exit value), TA-40. 00-overview.source.md:64

[G-83] (PM ruling at gate-1) REVISION (plan 01, Q-3 / AC-1): The default is approved: the repair runs at daemon start, before the scheduler thread starts. 00-overview.source.md:85
[G-84] (PM ruling at gate-1) Add one requirement and scenario: before the one-time repair writes, the daemon saves a copy of the pre-repair state row, so the repair can be reversed by hand. 00-overview.source.md:86
[G-85] (PM ruling at gate-1) The copy goes to a file beside the state DB or an additive table; that choice is Gate 2. 00-overview.source.md:87
[G-86] (PM ruling at gate-1) A failed backup write means no repair this start, plus a logged reason with the exception type name only, and a retry at the next start. 00-overview.source.md:88

## Requirement Inventory
These are the overview's R ids. Overview items R-15 (the workflow line), R-83 and R-84 (the Base and Depends on headers) and R-88 (the Earlier criteria section) are met by those parts of this plan.

| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | Every timing issue the refresh-timing audit found is resolved | G-1 | AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8, AC-9 | narrowed: this plan's part is the server findings G-63 assigns it |
| R-2 | No shown value changes, reverts or flickers when no new data arrived | G-1, G-14 | AC-2, AC-5, AC-7, AC-9 | narrowed: this plan's part is the server's values; the client writers are plan 03's |
| R-4 | Every number, chart, label and status on every page shows the newest data the engine has produced | G-13 | AC-3, AC-4, AC-6 | narrowed: this plan's part is `/api/state` and the server render of `/` |
| R-5 | Stale or wrong-day data is shown only with a dated, honest cue | G-14, G-22 | AC-3, AC-4 | narrowed: the server's stamps; the client's age cue is plan 03's |
| R-7 | The suite covers the pages, the scheduler, the close snapshot, `/api/state` and the polled GET routes | G-16, G-17 | AC-1, AC-2, AC-5 | narrowed: this plan's surfaces are the close snapshot, `/api/state`, the server render of `/`, `/api/strip/{window}` and `/api/hero-chart/{window}` |
| R-9 | A whole trading day on `/` shows correct, current values | G-19 | AC-3, AC-4, AC-5, AC-6, AC-7 | narrowed: the server's part; the 09:30 and 15:53 header labels are plan 02's |
| R-13 | Every freshness stamp carries a date and its true age | G-22, G-36 | AC-3, AC-4 | narrowed: the date and the data moment of the server's stamps; "true age" on screen is plan 03's age cue |
| R-25 | `/api/state` freshness fields carry a date and a time, and the frozen branch states its snapshot date | G-36 | AC-3, AC-4 | full |
| R-26 | One portfolio computation per `/api/state` response, so the server render and the first poll agree | G-37, G-57, G-63 | AC-2 | full |
| R-31 | The stored close snapshot holds one snapshot with no nested earlier snapshot, and a one-time repair keeps the latest close's values exactly | G-42, G-43, G-63 | AC-1 | full |
| R-43 | The value each metric computes stays, except where two computations disagree inside one response | G-57 | AC-2, NB-4 | full |
| R-45 | Findings the audit marks covered stay with their plans | G-59 | NB-5 | full |
| R-47 | After the close the frozen state carries each triggered card's guard alpha | G-63 | AC-6 | full |
| R-48 | The frozen branch checks and states its snapshot's trading day | G-63 | AC-3 | full |
| R-49 | After the close the frozen figures do not drift with live account totals, and the window routes agree with the frozen state | G-63 | AC-5 | full |
| R-50 | The frozen branch carries every card's state and values, and the server render after the close uses them | G-63 | AC-6 | full |
| R-51 | A poll that lands mid-cycle never pairs two cycles' values | G-63 | AC-7 | full |
| R-52 | Server freshness stamps name the data's moment, never the request time or a cycle that fetched nothing | G-63 | AC-4 | full |
| R-53 | The verdict figure's label tells the truth about whether it moves | G-63 | AC-9 | full |
| R-54 | No renderer writes to a missing element, and the server stops computing what no client reads | G-63 | AC-8, NB-2 | narrowed: the unread but documented `/api/state` fields (`html`, `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time`) stay (Q-4) |
| R-92 | Before the one-time repair writes, the daemon saves a copy of the pre-repair state row so the repair can be reversed by hand; a failed copy means no repair that start, a logged reason with the exception type name only, and a retry at the next start | G-83, G-84, G-85, G-86 | AC-1 | full |
| R-34 | Plan 01, which changes the engine file's close-snapshot builder, is marked Sensitive | G-47 | AC-1 | full |
| R-38 | `math_engine.py` carries zero diff and `alpha_bot_execution.py` changes only in the close-snapshot builder | G-52 | NB-1 | full |
| R-89 | Open questions are answered by recorded defaults, never by stopping | G-12 | AC-1, AC-5, AC-8, AC-9 | full |

## User Tasks
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

## Earlier criteria on the same surface
Searched every `feature-plans/*.md` (with the `.completed.md` and `.obsolete.md` families and the suite folders, the UX overhaul suite read from branch `ux-overhaul/fixtures` at 340b9d03), `DECISIONS.md`, `docs/generated/app.md` and `docs/audit/` for `last_market_close_snapshot`, `closed_frozen`, `data_as_of`, `frozen_at`, `portfolio_strip`, `guard_alpha` and `triggers_today`.

| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/dm-dashboard-market-mode.completed.md` AC-DM.2.1 (the snapshot schema: `trading_day`, `captured_at_et`, `data_as_of`, `portfolio_strip`, `shadow_divergence`, `accounts_map`) | Close snapshot | changed | The schema keeps every field; `accounts_map` holds symphonies only (AC-1) |
| `feature-plans/dm-dashboard-market-mode.completed.md` AC-DM.2.2 (captured once per trading day, never overwritten after hours) | Close snapshot | kept | The trading-day guard at `alpha_bot_execution.py:1115-1118` stays (S-1.9) |
| `feature-plans/dm-dashboard-market-mode.completed.md` AC-DM.3.1 (`frozen_at` an ISO timestamp sourced from `captured_at_et` and `trading_day`) | `/api/state` frozen | changed | It shipped as "16:00:01 ET" with no date; AC-3 makes it dated, keeping its seconds (overview SD-1's `frozen_at` form), not ISO |
| `feature-plans/dm-dashboard-market-mode.completed.md` AC-DM.3.3 (the frozen `data_as_of` shows the snapshot's `captured_at_et`) | `/api/state` frozen | changed | Kept as the time; AC-3 adds the date and the previous-session marker |
| `feature-plans/dm-dashboard-market-mode.completed.md` AC-DM.3.4 (no snapshot yet: `notice` and an empty state) | `/api/state` frozen | kept | S-3.6 |
| `feature-plans/dm-dashboard-market-mode.completed.md` edge case "daemon down across market close: banner shows the old `frozen_at`, a clear signal" | `/api/state` frozen | changed | No banner shows it at the ref (ux-overhaul 05 NB-8 keeps the market-state banner hidden); AC-3 puts the previous-session marker in the as-of instead |
| `feature-plans/eod-today-change-account-basis.completed.md` AC-1, AC-2 (the frozen strip wrapped on account basis by the live helpers) and DE-EOD-BASIS-001 (read-time wrap with the in-memory account totals) | `/api/state` frozen | changed | The wrap and its helpers stay; AC-5 fixes which account totals it reads to those of the snapshot's moment, so the figures stop drifting all evening (Q-1) |
| `feature-plans/eod-today-change-account-basis.completed.md` AC-3 (Tier 1 last-good with `account_basis_stale`, Tier 2 `basis: "value_weighted"`) and AC-9 (pre-market parity) | `/api/state` frozen | kept | S-5.4, S-5.5, S-5.8 |
| `feature-plans/eod-today-change-account-basis.completed.md` AC-8 (no new render-path I/O in the frozen wrap) | `/api/state` frozen | kept | AC-5 reads only in-memory totals the scheduler already fetched |
| `feature-plans/audit-bl4-account-basis-honesty-render.md` AC-1 to AC-5 (the basis chip and `#account-basis-as-of`, backend lines unchanged) and DE-AUDIT-BL4-001 | Account-basis chip | kept | `account_basis_as_of` keeps its own dated form (SD-1, S-4.8); the chip's backend markers keep their meaning |
| `feature-plans/dashboard-realtime-push.md` AC-4 and DE-SSE-PUSH-001 "AC-7 `data_as_of` scope" (the stamp derives from `last_successful_cycle_at`; the exception path keeps the render clock, ruled out of scope) | `data_as_of` | contradicted | SD-2: no path falls back to the request clock; the exception path reads `--` (S-4.5), and the stamp names the newest recorded observation, not a cycle that fetched nothing (S-4.2) |
| `feature-plans/fix-ops-cluster.md` AC-2 and DE-OPS-CLUSTER-001 F-1 (one shared read-only connection threaded through the strip helpers) | `/api/state` cost | kept | AC-2 removes a whole duplicate computation; AC-7's single-cycle read keeps the shared-connection pattern's purpose |
| `feature-plans/held-basis-convergence.md` (Today-row if-held from the raw trajectory for a triggered symphony; DE-HELD-BASIS-001) | Today row, cards | kept | S-2.6 and S-6.6 pin the values the live strip computes |
| `feature-plans/mdd-window-truth.md` AC-7 (`alpha_bot_execution.py` and `math_engine.py` zero diff, hash-pinned by `tests/test_mdd_window_truth_zero_diff_guard.py`) | Engine file | changed | AC-1 changes the builder only; the pin is re-taken at this plan's GREEN with the reason, and `math_engine.py`'s pin stays |
| ux-overhaul plan 05 (`feature-plans/ux-overhaul/05-dashboard-charts.md` on branch `ux-overhaul/fixtures`) NB-3 (trimming the `/api/state` payload deferred) | `/api/state` payload | changed | AC-1 removes the nested chain at its source; trimming other fields stays deferred (NB-2, Q-4) |
| ux-overhaul plan 05 PM-7 (after hours the client hides other accounts' cards; the server's frozen branch is unchanged) | `/api/state` frozen | changed | The frozen branch now carries `symphonies` and guard alpha (AC-6); it still ignores `?account=`, so PM-7's client narrowing keeps working (S-6.7) |
| ux-overhaul plan 05 AC-2 and its DEC-1 (both render paths write the server's verdict text with the "at exit" suffix) and S-2.10 (three polls never change the verdict text) | Card verdict | contradicted | The figure is not fixed at exit; AC-9 replaces "at exit" with a truthful label (Q-2); S-2.10 still holds across polls with no new cycle (S-9.6) |
| ux-overhaul plan 05 AC-6 (the account filter on `/api/state`) | `/api/state` live | kept | AC-2 keeps the filter's effect on the cards and the strip's all-accounts basis |
| ux-overhaul plan 05 NB-5 (every value `/api/state`, `/api/chart` and `/api/hero-chart` compute stays) | `/api/state` values | changed | AC-2 drops the duplicate computation, AC-5 fixes the frozen figures' account totals, and S-6.4 leaves an absent-input guard alpha absent (overview SD-12) |
| ux-overhaul plan 05 NB-7 (`alpha_bot_execution.py` is a protected surface) | Engine file | changed | AC-1 changes the close-snapshot builder only, as the PM allowed (G-47, G-52) |
| `feature-plans/eod-today-change-account-basis.completed.md` AC-5 (the engine and its EOD snapshot writer byte-unchanged) | Engine file | changed | AC-1 changes that writer's account map, and only it |
| `DECISIONS.md` DE-CLOSED-BOUNCE-001 (the closed branch computes its own default-window `guard_alpha` and `window`, so the client never falls back to a windowed strip that bounced the lifetime row) | `/api/state` frozen | changed | The frozen default-window `guard_alpha` stays (S-2.2 requires `meta` and `portfolio_strip` to agree on it); AC-5 now also fixes its inputs to the snapshot's moment; the client leg is plan 03's |
| `feature-plans/dm-dashboard-market-mode.completed.md` AC-DM.5.1 (the engine runs every minute around the clock) | Engine cadence | kept | No cadence change (NB-1) |

## Acceptance Criteria (Building)
### AC-1: The close snapshot holds only symphonies, and the stored chain is repaired once
Requirement items: R-1, R-7, R-31, R-34, R-89, R-92
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

Negative coverage: N1 S-1.4, S-1.5, S-1.11 · N2 n/a: the EOD step and the repair refuse no input · N3 S-1.6 · N4 n/a: engine and daemon-start paths with no caller identity · N5 S-1.7, S-1.8 · N6 S-1.10, S-1.13 · N7 S-1.9, S-1.14, S-1.15, S-1.16 · N8 n/a: no edit is held open
Questions: Q-3

### AC-2: One portfolio computation per response, and the server render equals the first poll
Requirement items: R-1, R-2, R-7, R-26, R-43
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

Negative coverage: N1 n/a: the route takes no input that changes the computation besides `account`, whose effect is ux-overhaul 05 AC-6's and is unchanged · N2 n/a: the route refuses nothing · N3 S-2.5 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-2.9 · N6 S-2.8 · N7 S-2.6 · N8 n/a: no edit state
Questions: none

### AC-3: The frozen branch states its trading day and flags an earlier session
Requirement items: R-1, R-4, R-5, R-9, R-13, R-25, R-48
Rule: When `/api/state` is served in `closed_frozen` or `pre_market`, the response shall carry the snapshot's `trading_day`, its `frozen_at` and `data_as_of` shall name that date (SD-1), and if the snapshot's trading day is earlier than the most recent completed session, then `data_as_of` shall end with " · previous session" and the response shall carry `snapshot_is_previous_session: true`.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | 18:00 ET on trading day D, the snapshot for D captured at 16:00:42 ET | `GET /api/state`, then `/` renders it | `trading_day` is D; `frozen_at` reads "D 16:00:42 ET"; `data_as_of` reads "D 16:00 ET"; `snapshot_is_previous_session` is false; `#hero-data-as-of` reads "data as of D 16:00 ET" |
| S-3.2 | N1 | 16:00:20 ET on D, before the EOD step commits, the snapshot for D-1 | `GET /api/state` | `trading_day` is D-1; `data_as_of` reads "D-1 16:00 ET · previous session"; `snapshot_is_previous_session` is true |
| S-3.3 | B | 08:00 ET on a Monday, Friday's snapshot | `GET /api/state` | `trading_day` is Friday's date; no previous-session marker, because Friday is the most recent completed session |
| S-3.4 | N1 | a weekday market holiday evening, the snapshot from the session before it | `GET /api/state` | no previous-session marker |
| S-3.5 | N3 | a legacy snapshot with no `trading_day` | `GET /api/state` | `trading_day` is null and `data_as_of` reads "16:00 ET (date not recorded)" |
| S-3.6 | N3 | no snapshot exists | `GET /api/state` after the close | the waiting response with its `notice` returns as at the ref (AC-DM.3.4) |
| S-3.7 | N6 | the daemon was down from 15:59 to 16:06, so no snapshot for D was written | `/api/state` is polled through the evening | every response carries D-1's figures with the previous-session marker |
| S-3.8 | N1 | a half-day whose session closes at 13:00 | `GET /api/state` at 13:00:20 before the EOD step commits | the D-1 snapshot carries the previous-session marker |
| S-3.9 | N7 | any frozen response | it is served | the row, headline and card figures are the ones AC-5 and AC-6 fix; only the date fields and the marker are added |
| S-3.10 | N5 | two polls at 18:00 and 18:10 with no new snapshot | both are served | the date fields are identical |

Negative coverage: N1 S-3.2, S-3.4, S-3.8 · N2 n/a: the route refuses nothing · N3 S-3.5, S-3.6 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-3.10 · N6 S-3.7 · N7 S-3.9 · N8 n/a: no edit state
Questions: none

### AC-4: Every freshness stamp names the data's moment, never the request's
Requirement items: R-1, R-4, R-5, R-9, R-13, R-25, R-52
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

Negative coverage: N1 S-4.6, S-4.7 · N2 n/a: the route refuses nothing · N3 S-4.3, S-4.4 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-4.9 · N6 S-4.2, S-4.5 · N7 S-4.8 · N8 S-4.12
Questions: none

### AC-5: After the close, the figures stay fixed on the snapshot and the window routes agree
Requirement items: R-1, R-2, R-7, R-9, R-49, R-89
Rule: While `/api/state` serves one close snapshot, its Today and Cumulative figures, account value and "Account · all-time" figure (`account_all_time_cr`) shall be identical on every poll, computed with the account totals of the snapshot's moment (Q-1), and `/api/strip/{window}` and `/api/hero-chart/{window}` requested in `closed_frozen` or `pre_market` shall compute from the same snapshot state, so the 30d strip's guard alpha equals the frozen poll's.

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

Negative coverage: N1 S-5.8, S-5.10 · N2 n/a: the routes refuse only unknown tokens, unchanged (S-5.10) · N3 S-5.5 · N4 n/a: the routes are behind the global auth hook, unchanged · N5 S-5.9 · N6 S-5.6 · N7 S-5.7 · N8 n/a: no edit state
Questions: Q-1

### AC-6: The frozen branch carries every card's state, values and verdict
Requirement items: R-1, R-4, R-9, R-47, R-50
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

Negative coverage: N1 S-6.5 · N2 n/a: the route refuses nothing · N3 S-6.3, S-6.4 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-6.9 · N6 S-6.8 · N7 S-6.6, S-6.7 · N8 n/a: no edit state
Questions: none

### AC-7: No response pairs two engine cycles
Requirement items: R-1, R-2, R-9, R-51
Rule: If `/api/state` is served while an engine cycle has committed its `shadow_history` rows but not its `bot_state`, then every card's Bot and Held legs, its alpha badges, the pills and the hero series' last point shall come from the same, completed cycle.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | the shadow rows for minute M are committed and `bot_state` for M is not | `GET /api/state` | every card's Bot and Held legs and the hero's last point come from minute M-1 |
| S-7.2 | B | minute M's `bot_state` is committed | `GET /api/state` | they come from minute M |
| S-7.3 | N3 | a symphony with no shadow row before minute M | `GET /api/state` mid-cycle | its Bot leg is absent and renders the dash, as at the ref |
| S-7.4 | N7 | the poll the cycle event triggers, after the engine exits | it is served | the values equal the ref's for the same committed state |
| S-7.5 | N6 | the state read or the request's committed-read connection fails | the route answers | 500 with its error body, never a partial mix; a read that fails inside one analytics helper still renders that leg as the dash, as at the ref (`analytics.py:618-659`), and never mixes cycles |
| S-7.6 | N5 | a cycle event and a timer poll arrive within the same cycle | both are served | each response's legs come from one cycle |

Negative coverage: N1 n/a: the route takes no input bearing on the cycle · N2 n/a: the route refuses nothing · N3 S-7.3 · N4 n/a: the route is behind the global auth hook, unchanged · N5 S-7.6 · N6 S-7.5 · N7 S-7.4 · N8 n/a: no edit state
Questions: none

### AC-8: Dead client writers and unread server fields are removed
Requirement items: R-1, R-54, R-89
Rule: When `/` refreshes, the dashboard script shall write only elements the dashboard template contains, and when `/api/state` is served, it shall compute no field that no client reads, apart from the documented fields Q-4 keeps.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | `static/index.js` and `templates/index.html` | a scan lists every element id and selector the script writes | each exists in the template; `updateStatusStrip`, `updateMarketDot` and the `#hero-ticker` writer are gone |
| S-8.2 | B | any market state | `GET /api/state` | `meta.triggers_today` is absent and its query is not run |
| S-8.3 | B | the frozen branch | `GET /api/state` | the per-symphony Today, Cumulative and Max DD values it computes are read by the `symphonies` list (AC-6) |
| S-8.4 | N7 | every other `meta` and top-level field | `GET /api/state` | present with the same meaning as at the ref, the Q-4 fields included |
| S-8.5 | B | `static/index.js` | it is read | the comment beside the poll interval states 30 s, the value `POLL_INTERVAL_MS` holds |

Negative coverage: N1 n/a: no input · N2 n/a: nothing is refused · N3 n/a: the removed field had no reader, so no absent state is rendered · N4 n/a: no access change · N5 n/a: removing a computation has no concurrent path · N6 n/a: no dependency is added · N7 S-8.4 · N8 n/a: no edit state
Questions: Q-4

### AC-9: The verdict label says whether its figure moves
Requirement items: R-1, R-2, R-53, R-89
Rule: When a triggered card shows its guard-alpha verdict, the label beside the figure shall read "vs held now" while the market is open, because the figure is the exit return minus the live held return and moves each cycle, and "at close" in the frozen branch, and never "at exit"; the figure's computation stays as at the ref.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-9.1 | B | the market is open and a triggered symphony's held return moves between two cycles | `GET /` and polls across both cycles | the figure changes and the label reads "vs held now" on the render and on every poll |
| S-9.2 | B | after the close | `GET /` and three polls | the label reads "at close" and the figure is identical on each |
| S-9.3 | N1 | the figure crosses zero between cycles | the next poll lands | the text switches between "Early exit · gave up" and "Good call · saved" and the label still reads "vs held now" |
| S-9.4 | N3 | no guard alpha | the card renders | the verdict reads the dash with no label |
| S-9.5 | N7 | any triggered symphony | the figure is computed | it equals the latest exit's `at_return` minus `current_return`, as at the ref (`app.py:3416-3418`) |
| S-9.6 | N5 | the market is open and three polls land with no engine cycle between them | they land | the verdict text is identical on each |
| S-9.7 | N1 | a guard alpha of exactly 0 | the card renders | "Early exit · gave up 0.0%α" with the label, on both paths |

Negative coverage: N1 S-9.3, S-9.7 · N2 n/a: display only · N3 S-9.4 · N4 n/a: `/` is behind the global auth hook, unchanged · N5 S-9.6 · N6 n/a: a failed poll leaves the verdict as it was (plan 03 AC-2) · N7 S-9.5 · N8 n/a: the card holds no edit state
Questions: Q-2

## Rule-by-Entry-Point Matrix
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

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | A non-symphony `bot_state` key treated as a symphony (overview DC-9) | N1 | grep `isinstance(.*, dict)` loops over `bot_state`, `state_data` and `accounts_map` in `alpha_bot_execution.py`, `app.py`, `analytics.py`; each must also require a `name` or skip the reserved keys | S-1.1, S-1.3, S-1.5 | - |
| DC-2 | A freshness stamp from the request clock or without a date (overview DC-2) | N3 | grep `datetime.now(` within ten lines of `data_as_of`, `frozen_at`, `as_of` in `app.py` and `analytics.py` | S-4.3, S-4.5, S-3.5 | - |
| DC-3 | Two computations of one figure inside one response | B | grep every call of `_compute_portfolio_strip`, `get_api_state_dict` and `compute_windowed_portfolio_strip` reached from `get_state()` and `dashboard()` | S-2.1, S-2.7 | - |
| DC-4 | A frozen figure reading a live source | N7 | grep `_account_totals_cache`, `_account_totals_last_good` and `database.load_state()` inside the frozen branch of `get_state()`, `/api/strip/{window}` and `/api/hero-chart/{window}` | S-5.1, S-5.3 | - |
| DC-5 | A changed format or field not carried to every consumer, tests pinning the old one included (overview DC-8) | B | grep `%H:%M ET`, `data_as_of`, `frozen_at`, `triggers_today`, `guard-alpha-at-exit` and "at exit" in `static/`, `templates/`, `app.py` and `tests/`; the 55 test files that mention `data_as_of` or the format at the ref are checked one by one | S-3.1, S-8.2, S-9.1 | - |
| DC-6 | An absent input coerced to 0.0 before a subtraction | N3 | grep `or 0.0` and `or 0` feeding arithmetic in `get_state()`, `dashboard()` and the frozen per-symphony loop in `app.py` | S-6.4 | - |
| DC-7 | A content-hash pin on a file this plan legitimately changes | B | grep `sha256` and `_PINNED_HASHES` in `tests/` for `alpha_bot_execution.py` | S-1.9 | - |

## Worked Example
At 18:00 ET on 2026-10-21 the stored snapshot (captured from production, 86 snapshots deep, 2,039,413 B in its row) is repaired: its account map loses its "Unknown" entry holding the chain, every symphony entry and its strip stay byte-equal, and the next after-close `/api/state` carries 12 entries in `bot_state`, `trading_day` "2026-10-21", `frozen_at` "2026-10-21 16:00:42 ET", `data_as_of` "2026-10-21 16:00 ET", a `symphonies` list of 12 and a `guard_alpha` on each of the 6 triggered entries; `meta.portfolio` and `portfolio_strip` carry one set of figures, and the same figures are served at 18:10 after an account refresh (S-1.2, S-1.3, S-3.1, S-6.1, S-2.2, S-5.1).

Failure case: the daemon is down from 15:59 to 16:06 on 2026-10-22, so no snapshot for that day exists. Every evening poll serves 2026-10-21's figures with `data_as_of` "2026-10-21 16:00 ET · previous session" and `snapshot_is_previous_session` true, so `#hero-data-as-of` tells the operator the figures are from the session before (S-3.7).

## Contract Changes and Consumers
- `/api/state`, every branch: `data_as_of`, `meta.portfolio.data_as_of` and `portfolio_strip.data_as_of` change from `HH:MM ET` to `YYYY-MM-DD HH:MM ET`, or `--` (SD-1, SD-2). Not backward compatible for string matchers. Consumers: `static/index.js:1328-1330` writes the string verbatim and keys row updates on the field's presence only (`static/index.js:997`, `:1018`), so it needs no change; the tests that pin the old format (DC-5) are updated.
- `/api/state` frozen branch: adds `trading_day`, `snapshot_is_previous_session`, a `symphonies` list in the live branch's shape and `guard_alpha` on triggered entries; `frozen_at` gains the date. Additive apart from `frozen_at`'s format; no static script reads `frozen_at` (grep). Consumer: plan 03 renders the frozen cards and verdicts.
- `/api/state`, every branch: `meta.triggers_today` removed; its only reader, `updateStatusStrip`, is removed with it. The tests and fixture that carry it (`tests/app/test_phase2b_portfolio_display.py`, `tests/analytics/test_portfolio_vol_computation.py`, `tests/telemetry/test_exit_triggers_schema.py` and `tests/fixtures/composer/v3-audit/dashboard_api_state.json`) are rewritten with the reason.
- `/api/strip/{window}` and `/api/hero-chart/{window}` in `closed_frozen` and `pre_market`: computed from the snapshot's entries (AC-5). Same shape. Consumer: `static/index.js` picker and plan 03.
- The live branch's `guard_alpha` is absent, not computed against 0.0, when `current_return` is absent (S-6.4).
- The verdict's label text changes from "at exit" to "vs held now" or "at close"; the span's test id `guard-alpha-at-exit` is renamed `guard-alpha-basis` with every consumer updated (DC-5).
- The stored `bot_state.last_market_close_snapshot.accounts_map` loses its non-symphony entries (AC-1). Consumers: the frozen branch (`app.py:2645-2648`, `:2731-2791`) and plan 03.

## Breaking Changes
- `tests/test_mdd_window_truth_zero_diff_guard.py:69` pins `alpha_bot_execution.py`'s content hash; AC-1 changes the file inside the builder, so the pin is re-taken at this plan's GREEN, with a comment naming this plan and the builder-only diff, and the `math_engine.py` pin stays.
- String matchers of the old `HH:MM ET` stamp and of the "at exit" label break (Contract Changes).

## Security Considerations
- Input validation and injection: the routes take no new input; the dated stamp is built server-side from stored times.
- Authorization and access control: no route changes guard; every route stays behind the global auth hook.
- Data exposure: the frozen `symphonies` list and `guard_alpha` carry only what the live branch already sends; the repaired snapshot removes duplicated data.
- Client-side rendering: the label and stamp render through `textContent` and Jinja autoescape, never `| safe`.
- Abuse or rate limits: the after-close body shrinks from 5.67 MB; one strip computation replaces two per request.
- Money (Sensitive): the builder runs inside the live engine's EOD step and the repair writes the state row the engine trades from; S-1.8 and S-1.9 pin that no engine-written field is lost and no exit decision changes, and the change touches no order or liquidation path (NB-7).

## Design-System Mapping
| Component | Element | Required primitive or token | Notes |
|---|---|---|---|
| Hero legend | `#hero-data-as-of` dated text | existing `.legend-as-of` | text only; longer string |
| Card verdict | basis label span | the existing `guard-alpha-at-exit` span style (`--studio-ink-faint`, 9 px) | renamed test id, same style |

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10, S-1.11, S-1.12, S-1.13, S-1.14, S-1.15, S-1.16 | engine EOD step against a temporary state database seeded from the captured snapshot; the repair against the same; the frozen route with the Flask test client | the engine's EOD step run with `--force` on a weekend date against a copy of the production database, then `GET /api/state` |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9 | route tests per market state with a counting seam on the strip computation; server render plus first poll compared | `GET /` then `GET /api/state` on the live daemon in each market state |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10 | route tests with a frozen clock and captured snapshots | the PM's 16:01 and 18:00 reads on the droplet |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8, S-4.9, S-4.10, S-4.11, S-4.12 | route tests with seeded shadow rows and cycle stamps | `GET /api/state` on the droplet at 09:29 and 09:31 |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7, S-5.8, S-5.9, S-5.10, S-5.11, S-5.12 | route tests with a stubbed account refresh between polls | two droplet polls ten minutes apart after the close, and a 30d click |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8, S-6.9 | route and server-render tests on a captured snapshot | `GET /` and a poll on the droplet after the close |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6 | route test with shadow rows committed ahead of `bot_state` | a timer poll during a droplet cycle |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5 | a template and script scan test; route test | `GET /api/state` |
| AC-9 | S-9.1, S-9.2, S-9.3, S-9.4, S-9.5, S-9.6, S-9.7 | server render and `updateVerdicts` under node with captured payloads | `/` on the droplet across two market-hours cycles and after the close |

Gates: `ruff format --check`; `ruff check`; a scoped pytest run of `tests/app`, `tests/dashboard`, `tests/realtime_push`, `tests/execution`, `tests/engine`, `tests/database`, `tests/analytics`, `tests/ui`, `tests/portmode`, `tests/telemetry`, `tests/security` and `tests/test_mdd_window_truth_zero_diff_guard.py`, at most 4 workers, never `-n auto`; CI green on the exact SHA; `/review`; then the PM's live functional check on the droplet (the AC rows' real-entrypoint column), because tests-green is necessary and never sufficient.
Fixture provenance (SD-7): the nested snapshot, the frozen and live `/api/state` payloads and the shadow rows are captured from the production state database copy the audit used, one record per case; the previous-session, legacy no-`trading_day` and absent-`current_return` cases are derived from a captured record with only that field changed, signed off by the PM.

## Known-Unconfirmed and Standing Risk
- What Composer's `todays_percent_change` means after the close and after midnight is unconfirmed, so whether AC-5's first-read-after-capture totals equal the close's figures is unconfirmed. Read-only check: two `GET /portfolio/accounts/{account}/total-stats` reads from the droplet, at 16:05 and 00:30 ET, compared.
- How often the EOD step misses its window in production is unconfirmed (the 86-snapshot chain shows no miss through 2026-10-21). Read-only check: `sqlite3 -readonly /opt/planetstopper/alphabot_state.db "select json_extract(data,'$.last_market_close_snapshot.trading_day'), json_extract(data,'$.post_mortem_run') from bot_state where id=1"` after 16:10 ET on several days.
- How much the verdict figure moves per cycle during market hours is unmeasured. Read-only check: two `/api/state` payloads one cycle apart during market hours, comparing a triggered entry's `guard_alpha`.
- The engine cycle duration, and therefore how often a timer poll lands mid-cycle (AC-7), is unmeasured until plan 05's cycle log exists.
- Whether the newest `shadow_history` row is recorded every cycle in which the fetch succeeded, for every symphony, is read from the code (`alpha_bot_execution.py:953`) and not measured; AC-4's stamp depends on it.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | After the close, which account totals should the frozen Today and Cumulative figures use, so they stop drifting all evening? | AC-5 | The first successful account-totals read at or after the snapshot's capture, kept for that snapshot; until it exists, the last-good totals with the stale chip; a daemon restart re-reads once | default approved by the PM, [PM-ASSUMED] |
| Q-2 | The verdict figure moves every cycle while its label says "at exit". Label it truthfully, or freeze the figure at the moment of exit? | AC-9 | Label it truthfully: "vs held now" while the market is open and "at close" after it. Freezing at exit would read 0 for every card, because the exit row's `at_return` is the symphony's `current_return` at that moment (`alpha_bot_execution.py:1949`) | default approved by the PM, [PM-ASSUMED] |
| Q-3 | When does the one-time repair run? | AC-1 | At daemon start, before the scheduler thread starts, so no engine cycle runs during it; a failed write leaves the state unchanged and retries at the next start | default approved by the PM, with a pre-repair backup added (G-83 to G-86, S-1.12, S-1.13), [PM-ASSUMED] |
| Q-4 | Should `/api/state` stop sending the fields no static script reads (`html`, `accounts_map`, `shadow_divergence`, `port_state`, `exit_authority`, `execution_start_time`)? | AC-8 | No: keep them; they are the route's documented contract and are pinned by tests, and their cost falls with AC-1 and plan 05's compression | default approved by the PM, [PM-ASSUMED] |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| DEC-1 | The stamp names the newest recorded observation, not `last_successful_cycle_at` | The engine stamps `last_successful_cycle_at` even when every fetch failed (`alpha_bot_execution.py:2007`), and the engine file may change only in the builder; the header's engine-liveness check keeps reading `last_successful_cycle_at` |
| DEC-2 | The figures the rows already show win when two computations disagree | The rows read `portfolio_strip`, which the operator compares against Composer; keeping it changes the fewest rendered values (S-2.6) |
| PM-1 | The AC-1 matrix row and the job table cover six write paths: the engine's EOD builder, the daemon-start repair, the engine's per-cycle save, the flush-resync reset, the startup seed and the account-totals refresh job | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-2 | AC-6's cycle also waits for ux-overhaul 05 on `main` | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-3 | S-7.5: a failed state read or committed-read connection answers 500; a failure inside one analytics helper still shows that leg as the dash, as at the ref | PM amendment from the Gate-2 approach, [PM-ASSUMED] |
| PM-4 | The Gates line adds `tests/analytics`, `tests/ui`, `tests/portmode`, `tests/telemetry` and `tests/security` | PM amendment from the Gate-2 approach, [PM-ASSUMED] |

## Docs to Update
- `docs/generated/app.md` (`get_state()`, `get_api_state_dict()`, `dashboard()`, `/api/strip`, `/api/hero-chart`), `docs/generated/alpha_bot_execution.md` (the close-snapshot builder), `docs/generated/static_index_js.md`, `docs/generated/INDEX.md`.
- `README.md` where it describes the after-hours dashboard.
- A `DECISIONS.md` entry appended at ship by the doc writer.
- A drafted `.claude/CLAUDE.md` Key Files row update for `app.py` and `alpha_bot_execution.py`, for PM approval.

## Follow-Ups
- NB-6: the `total_monitored` count and the false divergence ERROR, each its own plan.
- NB-2: trimming the unread `/api/state` fields, if Q-4 is answered yes.
