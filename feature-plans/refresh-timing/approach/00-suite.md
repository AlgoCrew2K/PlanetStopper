# Approach: Refresh timing suite, the contracts every plan shares
Spec: feature-plans/refresh-timing/00-overview.md
Ref: PlanetStopper@37c8e983b64c6980c28199a3f9318486b60f3864, PlanetStopper@a539c74fbc23d126806ad0f50eab0de4f65415cc (branch `ux-overhaul/05-c1`, read only), PlanetStopper@2294032a0f9e62fbb50620ed7f1701756e07d7fc (branch `ux-overhaul/05-c2`, read only), PlanetStopper@8d0aae39f6b6a50250a2c6a378f002ba816fda77 (branch `ux-overhaul/05-c3`, read only), PlanetStopper@0d771793fb1440bfd006f1eec901009c4452e402 (branch `model-unpin/c9`, read only)

## Architecture

This file holds each contract that more than one refresh-timing plan approach reads, once, with its owner (the plan cycle that builds it) and its consumers. A plan approach cites the contract by its RC id and never restates it; where a plan approach says something different, this file names the difference under Items Raised to the PM. Every `file:line` is at 37c8e983 unless it is prefixed `c1:`, `c2:` or `c3:` (the `ux-overhaul/05-c1`, `-c2` and `-c3` heads in the Ref line) or `c9:` (`model-unpin/c9`). Every function is also named, so a test-writer re-locates it after the merges move lines. The overview has no acceptance criteria, scenarios or rule matrix of its own; each plan threads its own criteria in its own approach. The ux-overhaul suite contracts SC-1 (the `/` readiness signals), SC-7 (the `browser` marker) and SC-8 (launcher modes) in `feature-plans/ux-overhaul/approach/00-suite.md` (branch `ux-overhaul/fixtures`) apply here unchanged and are cited, never restated.

### RC-1: the header engine label, one precedence on every page (owner 03 C2 and C3; contributors 02 C5; consumer 04 C1)

The ref has one writer, `updateEngineStatus(data)` (`static/chrome.js:208-240`), with three states (Closed, Stale, Live) and a Stale test on the browser clock (`static/chrome.js:226`, `Date.now() - new Date(cycleAt)`). `static/chrome.js` is the only script on every page (`templates/_chrome.html:218`), so the label code lives there.

The full order, highest first. The first state whose condition holds is shown:

| Rank | Label | Condition | Colour token | Countdown |
|---|---|---|---|---|
| 1 | Connection Lost | the newest refresh this page issued ended in a handled failure (non-2xx other than 401, a body that is not JSON or fails the page's shape check, a network error, the request timeout); a 401 navigates to `/login` instead | `--studio-neg` | hidden |
| 2 | Not updating | no refresh has succeeded for more than 90 000 ms, measured on `performance.now()` from the receipt time of the last success | `--studio-warn` | shown |
| 3 | Degraded | the last good body's `degraded_reasons` is a non-empty list (plan 02's `_health_status()`: `scheduler_stalled`, `engine_overrun`, `engine_failing`) | `--studio-neg` | shown |
| 4 | Closed | `market_state` is not `open` and `meta.system_online` is false (the ref's `isOpen` test, `static/chrome.js:215`) | `--studio-ink-dim` | hidden |
| 5 | Rebalance pause | `engine_phase` is `blackout` | `--studio-ink-dim` | shown |
| 6 | Opening | `engine_phase` is `opening` | `--studio-ink-dim` | shown |
| 7 | Stale | the server-reckoned cycle age exceeds 120 000 ms (the ref's two minutes) | `--studio-warn` | shown |
| 8 | Live | otherwise | `--studio-pos` | shown |

- Rebalance pause and Opening share one rank in overview SD-13; `engine_phase` holds one value at a time (plan 02 `_engine_phase`), so the order between them never decides anything, and it is stated so no implementation invents another.
- Degraded is the PM's ruling that the liveness fields feed SD-13 (PM note to this approach, 2026-10-22). It ranks under Connection Lost and Not updating, because a page with no current data cannot know whether the daemon is still degraded, and over Closed, because a stalled scheduler or a failing engine stops trailing stops and nightly jobs in every market state (overview SD-4). Its title is built from a fixed map `DEGRADED_REASON_TEXT` in `static/chrome.js`: `scheduler_stalled` "Scheduler stalled: no engine spawn for 2 minutes or more.", `engine_overrun` "An engine run has lasted over 3 minutes; minutes are being skipped.", `engine_failing` "The last 5 engine runs failed."; an unknown reason reads "The daemon reports a degraded state."; reasons join with a space, through `textContent` and `title` only. Only `degraded_reasons` changes the label. `status` repeats it, and `scheduler_last_tick_at`, `engine_running_since` and `last_cycle` change no label (Items Raised 1).
- Stale's age is `(serverNowMs at receipt minus last_successful_cycle_at)` plus `(performance.now() minus receivedAtMono)`, both server times from the same response, never `Date.now()` against a server time (overview SD-13). A response with no parseable server time keeps the previous Stale judgement. `last_successful_cycle_at` is read from the body's top level and falls back to `meta.last_cycle_at`, as at `static/chrome.js:225`.
- The countdown's visibility is written by the same function, from the table's last column. Hidden for Connection Lost (plan 04 S-1.6) and for Closed (plan 03 S-4.11, the ref's rule at `static/chrome.js:268-272`).

The functions, all in `static/chrome.js`, all exposed on `window` (names adopted from plan 03's approach, PM note):
- `renderEngineLabel()`: the one function that computes the precedence above and the only writer of `#engine-status-label`, `#engine-status-dot`, the `#engine-status` title and the countdown's visibility (`templates/_chrome.html:51-53`, `:57`). It reads only module state and takes no argument. It is called by the two `note` functions below, by `updateChromeTicker` and by the existing 1 s tick (`static/chrome.js:293`). No other function in any file computes a label: plan 04 builds no resolver of its own (its first draft's `resolveEngineLabel`), and `showConnectionLost` in `static/index.js` stops writing the label (plan 03 C2).
- `noteRefreshFailed()`: sets the failed flag and calls `renderEngineLabel()`.
- `noteDataReceived(timing)`: clears the failed flag, stores `timing.receivedAtMono` as the last success, and calls `renderEngineLabel()`.
- `updateChromeTicker(body, timing)`: stores the body's `market_state`, `meta.system_online`, `engine_phase`, `degraded_reasons` and `last_successful_cycle_at` with `timing.serverNowMs` and `timing.receivedAtMono`, calls `resyncNextCountdown(body, timing)` and `renderEngineLabel()`. Its signature gains `timing` (`static/chrome.js:242-246` at the ref).
- `resyncNextCountdown(body, timing)`: anchors `body.next_run_seconds` at `timing.sentAtMono`; the tick shows the anchored value minus the elapsed monotonic seconds, wrapped modulo 60, in the server render's form `NEXT MM:SS` (`templates/_chrome.html:57`). Plan 03 AC-4 owns the rule.
- `updateEngineStatus(data)`: kept as an alias (it is `window`-exposed at `static/chrome.js:240` and tests read it) that calls `updateChromeTicker(data, null)`. A `null` timing records no server time and no receipt time.
- `receiptTiming(sentAtMono, response)` (RC-3).

Build order. Plan 02 C5 adds "Rebalance pause" and "Opening" inside `updateEngineStatus` at main's chrome.js, between the Closed branch and the Stale check (02 approach, AC-6). Plan 03 C2 forks from a tip holding plan 02, moves the whole precedence into `renderEngineLabel()` carrying plan 02's two branches, and adds Connection Lost and `noteRefreshFailed`, `noteDataReceived` and `receiptTiming`. Plan 03 C3 adds Not updating, Degraded, the server-reckoned Stale and the countdown rules. Plan 04 C1 calls these functions on the four other pages and adds none.

Body fields the label reads, and who serves each:
- `/` reads `/api/state`: `market_state`, `meta`, `last_successful_cycle_at` (served at the ref, `app.py:3498`), `engine_phase` (plan 02 C5, `_additive`), and `degraded_reasons`, which plan 03 C3 adds to `get_state()`'s `_additive` dict (`app.py:2527-2532`, spread into every branch) as `_health_status()["degraded_reasons"]`, plan 02's helper with no database read. New: `/api/state` carries no liveness field at the ref, and plan 02's contract adds only `engine_phase` (Items Raised 1).
- The four other pages read plan 04's `GET /api/header-status`, whose body uses the same field names (04 approach, Architecture "Header on the four other pages").

### RC-2: one mode-pill writer per page (owner 03 C5; consumer 04 C1)

The pill is server-rendered only at the ref (`templates/_chrome.html:93-95`); the hero legend word is `templates/index.html:861`.
- `updateModeIndicators(meta)` is defined in `static/chrome.js` and exposed on `window`. The code shows why it cannot stay in `static/index.js`, where plan 03's approach places it: `index.js` is loaded only by `templates/index.html:1872`, so the four other pages cannot call it, while `chrome.js` loads on every page (`templates/_chrome.html:218`). The name is plan 03's (PM note). It writes `[data-testid="mode-pill"]`'s text, `data-live`, and its background and colour as the token references the server render uses (`var(--studio-neg)` with `var(--studio-white)` for LIVE, `var(--studio-chip-bg)` with `var(--studio-ink-dim)` otherwise). It writes the hero legend word `[data-field="legend-mode"]` only when that element exists, which is on `/` alone. An absent `meta.mode` writes nothing (plan 03 S-6.1).
- Call sites, one per page: on `/`, `static/index.js`'s renderer list calls `window.updateModeIndicators(data.meta)` for each applied `/api/state` body. On `/history`, `/performance`, `/ai-advisor` and `/settings`, plan 04's header poller calls it with `body.meta`. The poller runs only where `[data-header-poll="1"]` is present, and `templates/_chrome.html` sets that attribute only when `active_route != 'dashboard'` (04 approach), so `/` never has two writers.
- Test seam: plan 03 C5's node test must load `chrome.js` before `index.js`. The harness option that does this, `run_scenario(..., scripts=("static/chrome.js", "static/index.js"))`, moves from plan 03 C2 to plan 03 C1, so C5 does not wait on C2. C2 still adds headers, abort, `performance.now`, `location.assign` and the stream stub (Items Raised 2).

### RC-3: the receipt record (owner 03 C2; consumer 04 C1)

`timing` is one plain object, `{sentAtMono, receivedAtMono, serverNowMs}`:
- `sentAtMono`: `performance.now()` read immediately before the `fetch` call.
- `receivedAtMono`: `performance.now()` read when the response's headers arrive (the first `.then` of the fetch), before the body is parsed.
- `serverNowMs`: `Date.parse(response.headers.get('Date'))` when the header is present and parses, else `null`. Werkzeug's handler sets `Date` on every response (03 approach, finding 9), and `/api/header-status` is served by the same process, so both pages read the server's clock the same way. No body field is added for it.

The one creator is `receiptTiming(sentAtMono, response)` in `static/chrome.js`, which reads the two remaining values and returns the object. Callers:
- `/`: `loadState` in `static/index.js` passes the record to `noteDataReceived(timing)` and `updateChromeTicker(data, timing)` for the newest request's applied body, and calls `noteRefreshFailed()` for its handled failure (plan 03 C2).
- The other pages: plan 04's `PSRequest.getJson(url)` in `static/request_guard.js` takes `sentAtMono` and calls `window.receiptTiming` when the response arrives, resolving `{ok, status, body, timing}`. `request_guard.js` loads before `chrome.js` (04 approach), and the call happens at run time, after both scripts have loaded. The header poller then calls the same three functions, in the same order as `/`.

### RC-4: plan 02's seams (owner 02 C1 to C5; consumers 04 C1, 05 C4)

Names fixed by plan 02's approach, cited here so plans 04 and 05 read one table:

| Seam | Built in | Read by |
|---|---|---|
| `CycleRecord` (frozen dataclass: `cycle_id`, `started_at`, `completed_at`, `outcome`, `changed`, `exit_code`, `running_cycle_id`, `running_since`; `event_payload()` returns the five contract keys) | 02 C1 | 02's event and `/health` `last_cycle`; 05 C4's cycle line |
| `_publish_cycle(record)`: the one choke point every engine run and every skip passes through, called in `trigger_alpha_bot()`'s `finally` | 02 C1, extended in 02 C2 | 05 C4 emits its line from the same object, in the same `finally` |
| `_engine_run_state() -> EngineRun | None`, `_begin_engine_run(started_at)`, `_end_engine_run(cycle_id)` | 02 C2 | 05 C4 (the skipped form reads the running run) |
| `_now_et` (wall clock) and `_monotonic` (ages), module attributes always called at call time | `_now_et` 02 C1; `_monotonic` 02 C3 | every test that replaces a clock; 05 C4's `duration_s` and `running_s` |
| `_log_skipped_minute(record)`, called only from `_publish_cycle` for a "skipped" record | 02 C2 | replaced by 05 C4, whose cycle line is the one skip line; 05 C4's test-writer updates 02's S-5.1 test |
| `_engine_phase(now_et, market_state, last_cycle_at) -> str`, pure | 02 C5 | `get_state()`; 04 C1's `/api/header-status` |
| `_health_status() -> dict` (`status`, `degraded_reasons`, `scheduler_last_tick_at`, `engine_running_since`, `last_cycle`), no database read | 02 C2 | `/health`; 04 C1's `/api/header-status`; 03 C3's `degraded_reasons` on `/api/state` (RC-1) |

`cycle_id` cannot be joined with the engine's own cycle ids. The daemon mints `<started_at to the second, ISO with offset>#<n>` (02 `_mint_cycle_id`). The engine writes its own telemetry ids in two other formats: `current_et.strftime("%Y%m%d_%H%M")` at `alpha_bot_execution.py:946` and `current_et.isoformat()` at `:1741`, and `alpha_bot_execution.py` stays zero diff outside plan 01's builder (overview NB-5). The event, `/health` and 05's cycle line join on the daemon `cycle_id` only (overview SD-3). A reader who needs the engine's rows for a run selects them by the record's `started_at` to `completed_at` window, never by id equality. No plan adds a column, key or parser that equates the two.

### RC-5: `_format_data_stamp` (owner 01 C3; consumers 01 C3 to C5, 04 C3 and C4)

- `_format_data_stamp(moment, *, seconds=False, previous_session=False) -> str` in `app.py`, placed immediately before `_compute_portfolio_strip` (`app.py:2046`). Every caller is an `app.py` route builder, and `analytics.py` cannot import `app.py`. New: `git grep -n "%Y-%m-%d %H:%M ET" 37c8e983 -- '*.py' ':!tests'` finds no dated helper; the ref inlines `strftime("%H:%M ET")` at `app.py:2310`, `:2312`, `:2314`, `:2403`, `:2877`, `:3374`, `:3376`, `:3378`.
- `moment` is a timezone-aware `datetime`: the text is `moment.astimezone(_ET)` as `YYYY-MM-DD HH:MM ET`, or `YYYY-MM-DD HH:MM:SS ET` when `seconds` is true (overview SD-1's two exceptions, `account_basis_as_of` and `frozen_at`). Minutes and seconds are truncated, never rounded, as `strftime` does and as ux-overhaul SC-3 rules. `previous_session=True` appends ` · previous session` (SD-1).
- `moment` is a `datetime.time` (a legacy snapshot with a capture time and no date, plan 01 S-3.5): `HH:MM ET (date not recorded)`.
- `None`, a naive `datetime`, or anything else: `--` (SD-2). The helper never reads the request clock, never guesses a zone, and never raises.
- No other server code writes an SD-1 stamp. Plan 04's `/history` and `/performance` `data_as_of` build an ET-aware `datetime` and call this helper; its approach currently names ux-overhaul SC-3's `analytics.format_et_stamp` instead (Items Raised 4). SC-3 stays the formatter for stored advisor instants on `/ai-advisor`. The two helpers produce the same text for the same instant, and a parity test on plan 04's base, which holds both, pins that (04 C3).
- Clients print the server's string verbatim. Plan 03's `renderAsOf` prefixes `data as of ` and adds its cues; no client formats a server stamp.

### RC-6: `market_calendar.most_recent_completed_session` (owner 01 C3; consumer 01 C4)

- `most_recent_completed_session(now_et) -> date | None`: the newest date `d` on or before `now_et`'s ET date for which `is_trading_day(d)` (`market_calendar.py:41`) holds and `now_et` is at or after `d` at `session_close(d)` ET (`market_calendar.py:81-104`, half days included). It walks back at most 10 calendar days (`_COMPLETED_SESSION_LOOKBACK_DAYS = 10`, named with a source comment: longer than the longest XNYS closure run in the calendar) and returns `None` past that. An aware `now_et` is converted to ET first; a naive one is read as ET wall time, as `get_market_state` reads its argument's date and time (`market_calendar.py:123-127`). New: 01's search, `git grep -n -i "def .*\(prev\|previous\|last\|recent\)_\(trading\|session\|completed\)" 37c8e983`, finds none.
- Placed at the end of `market_calendar.py` (after `get_market_state`, `:107-136`), not beside `session_close`, because plan 02 C1 inserts `rebalance_blackout_start` and `post_mortem_cutoff` directly after `session_close` (02 approach), and two insertions at one point conflict at every merge.
- Its tests are 01 C3's new `tests/market_calendar/test_most_recent_completed_session.py`, on the real XNYS calendar, never a stub.
- Plan 02 needs neither RC-5 nor RC-6. Its ET work is today's ET date (`_et_trading_day_bounds`), a different question, and its stamps are ISO machine fields. Plan 03 needs neither: it prints stamps and compares them by string. If a later cycle needs either, it imports; no second implementation is written (Items Raised 5).

### RC-7: fixtures (owner FX; every plan extends)

- One shared folder, `tests/fixtures/refresh_timing/`, holds:
  - `capture.py`, the one capture script. It is stdlib only, run with `python -I`, and opens a database only as `sqlite3.connect("file:<path>?mode=ro", uri=True)`, so it can never write the source.
  - One subfolder per cycle, `tests/fixtures/refresh_timing/<NN>-c<K>/` (for example `01-c1/`, `02-c4/`, `03-c2/`, `04-c9/`), each with a `PROVENANCE.md`.
  - A root `PROVENANCE.md` indexing the subfolders.
  - `.gitignore` entries `*.sqlite`, `*.db` and `*.db-*`, so a database copy dropped there is never staged.
- Every plan extends the script without editing the shared file. `capture.py` holds the read-only opener, the record writer and an HTTP GET recorder (`get --base-url <launcher> --path <path>`, which saves the status, `Content-Type`, `Date` and the body verbatim, for launcher captures). It discovers `_cases_<NN>.py` modules beside it, and each plan's first capturing cycle adds its own module of subcommands, named `<NN>-c<K>-<case>`. Two plans' cases never touch one file, so the merges of RC-9 do not conflict on it. The ref's precedent is `tests/fixtures/prod_droplet/capture_from_droplet.py:1-30`.
- Sources, in this order:
  1. The PM's read-only droplet run. The PM copies `capture.py` and the needed case module to the droplet's `/tmp`, and runs them as the service user against the live file, read-only: `runuser -u planetstopper -- /opt/planetstopper/.venv/bin/python -I /tmp/rt_capture/capture.py <case> --db /opt/planetstopper/alphabot_state.db --out /tmp/rt_capture/out`. The service user avoids a root-owned `-shm` file (05 approach, C6 risk row). The PM then copies `out/` to `.claude/review/refresh-timing-captures/<UTC stamp>/`, an untracked, durable path outside the scratchpad, and names that path in the cycle's brief.
  2. The tracked 2026-07-09 droplet captures in `tests/fixtures/prod_droplet/` (provenance at `tests/fixtures/prod_droplet/capture_from_droplet.py:1-30`), reused by path. A cycle copies a record out only to keep one record per case, and its `PROVENANCE.md` then names the source file and the record kept.
  3. Launcher GETs (ux-overhaul SC-8 Mode A), recorded by `capture.py get`. Their `PROVENANCE.md` row names the served SHA and the launcher's snapshot time.
  4. Producer output at test time: the real route through the Flask test client, or a real `database` writer, over records from 1 to 3. These are generated by the test, never committed as files.
- Never committed: any database file, and in particular the production database copies in the session scratchpad (`prof.sqlite` and the launcher's copy). They are temporary, so a launcher capture a later cycle needs is taken while the launcher is up and parked on the fixtures branch (below).
- A derived record changes only the named fields of one real record. Its `PROVENANCE.md` row names the source record, each changed field, the reason, the scenario, and the PM's sign-off as producer owner (overview SD-7). Until that sign-off is recorded, the scenario's row is held in the coverage record, never filled with a typed value. Tests derive expected values from the record or recompute them, and never assert a value copied from a capture.
- The branch `refresh-timing/fixtures` is forked from `main` at 37c8e983 and holds only `tests/fixtures/refresh_timing/**`. It exists for captures needed before their owning cycle starts: plan 03's launcher GETs (FX-STRIP-*, FX-GAS-*, FX-STATE-FROZEN-LAUNCHER) and the droplet row plan 01 C1 asks for if the PM takes it early. One `test-writer` dispatch (FX in Dispatch Ordering) writes `capture.py`, the root files and the `.gitignore`, and commits the PM's handed-off captures with their `PROVENANCE.md` rows. Each later capture batch is a fresh `test-writer` dispatch on the same branch. A cycle that reads a parked capture merges `refresh-timing/fixtures` into its branch by a merge commit (RC-9). A cycle whose captures are taken at its own RED time commits them in its own RED and never touches the fixtures branch (plan 01 C1's stated route).

### RC-8: branch names and the stack (PM ruling)

- Cycle branches are `refresh-timing/<NN>-c<K>` (for example `refresh-timing/02-c3`); each plan assembles into `refresh-timing/<NN>`; fixtures live on `refresh-timing/fixtures`. A worktree is named for its branch under `.claude/worktrees/`, with `.env` copied in, and `git rev-parse --show-toplevel` and `git rev-parse --abbrev-ref HEAD` are checked before the first edit (project worktree rule).
- Merge order to `main`, each a PR from `refresh-timing/<NN>`, after its own `/review` and the PM's live check:
  1. model-unpin first.
  2. 02, then 05: 05's tip carries 02's commits.
  3. 01, only once ux-overhaul 05 is on `main`: 01's later cycles carry it.
  4. 03, once 01 and 02 are on `main`: 03 stacks on the assembled `ux-overhaul/05`.
  5. 04 last, once 03 and ux-overhaul 04 are on `main`: 04 stacks on ux-overhaul 04's tip.

  01 and 02 have no order between them beyond these rules. No PR carries another plan's unmerged commits: before a PR opens, every consumed lower plan is on `main`, and `main` is merged into the tip.
- Every PR merges with a merge commit (`gh pr merge --merge`), never a squash. The repository's usual squash (`37c8e983`, "(#145)") would leave a lower plan's original commits in every upper tip, which would then re-apply them.
- No rebase, no force push. A consumed tip or head always enters by a merge commit.

### RC-9: stack integration merges: test-writer and implementer tasks, never the PM by hand

Who runs which merge:
- A base merge: a cycle whose Branch base names more than one input (for example 02 C2: C1's head with C3's head and model-unpin; 03 C2: C1's head with plan 02's tip; 05 C4: 05 C1's head with plan 02's head; 01 C5: the plan tip with `main` holding ux-overhaul 05). The cycle's own pair performs it as the cycle's first commits, before RED. The PM creates the cycle branch at the first input's recorded SHA. The `implementer` runs `git merge --no-ff --no-commit <input>` for each further input and resolves production files. The `test-writer`, in the same worktree, resolves test files. The `implementer` commits the merge, naming every input SHA in the message. The `test-writer` then runs the named test sets of every file either side touched in the conflicts, in slices (RC-11). A red test whose cause is a test pin goes to the `test-writer`, and one whose cause is production code goes to the `implementer`. The RED commit follows only after these slices pass.
- An integration task: a merge into an assembly branch `refresh-timing/<NN>`. This means each passed cycle head, a lower plan's tip, `main` holding the lower plans and model-unpin, `refresh-timing/fixtures`, and a lower plan's later review fix. It runs as a fresh `test-writer` and `implementer` pair dispatched for that merge. The brief names the receiving branch and its SHA, the inputs and their SHAs, and the test sets to rerun, with the same split of work as a base merge. One integration task may enter several inputs. It sends one integration-complete to `main` naming the merge commit SHA and the slices it ran.
- The PM's part is limited to these: create each assembly branch `refresh-timing/<NN>` at its base SHA (a ref, no content); record every SHA; dispatch the pairs. The PM never runs `git merge` and never resolves a conflict.
- An integration head that resolved a conflict in production code is reviewed by one `qa` and one `code-reviewer` at that head before the next cycle forks from it. A clean merge is judged by the PR's own gates.

### RC-10: the launcher restart protocol (provided by the PM)

Scenarios that need a daemon restart against real data run on ux-overhaul SC-8 Mode A: a copy of production data, scheduler off, outbound network refused, operator POSTs refused. These are plan 03 S-3.6's launcher half and the header recovery rows of plans 03 and 04. Plan 02 C5's capturer run needs no restart.
- Because the scheduler is off, there is no `cycle-complete` event. The browser module fakes the stream with Playwright `page.route('**/api/events')`, fulfilling a canned `event: cycle-complete` body from plan 02's producer (FX-EVENT-CHANGED), or a status (502 for a proxy-refused reconnect, 401 for a lapsed session). It runs retry delays with `page.clock`. If `page.route` cannot fulfil an `EventSource` request in the pinned Firefox (Risk Register), the module falls back to the in-process server `tests/ui/_browser_server.py` (ux-overhaul SC-7) with the test publishing a real `CycleRecord` through `_publish_cycle`.
- The restart is scheduled, never requested mid-task. Before dispatching the capturer, the PM arms a scheduled stop and start of the launcher process as a background command it starts itself: stop at a UTC time T, start again on the same port, served SHA and database copy at T plus 30 s. The capturer brief carries T, the port and the served SHA. The capturer opens every tab the scenario names before T and records the pre-restart state. Through the down window it waits on the page's own signals: the label reaching Connection Lost, then the next `body[data-refresh-count]` increment after T plus 30 s (ux-overhaul SC-1). It records the values, header label and stream recovery with no reload. Nothing is sent to the PM mid-task, so the step depends on no remembered action.
- The capture record adds, to SC-8's fields, the stop and start UTC times as the launcher logged them and `daemon_started_at` before and after. A restart changes it, because `_DAEMON_STARTED_AT` is set at import (`app.py:500`), so plan 04's token-refresh trigger (`noteDaemonStart`) fires. Without Caddy, the browser sees a refused connection, not a 502. Caddy's 502 close is proven by the `page.route` 502 test and by the PM's droplet check (03 approach).
- A Mode A restart never serves a pair's worktree as it stands, which carries the real `.env` (ux-overhaul SC-8 Mode B item 4).

### RC-11: the test-run governor

A worktree is blocked after two test runs of 300 s or more within one hour. Every cycle and integration task therefore follows these rules:
- Each test set is a named slice expected to finish under 240 s. A slice whose measured wall time reaches 240 s is split by file or class before it runs again, so a run of 300 s or more is never repeated within the hour.
- Every run is a background Bash call (`run_in_background: true`) with output redirected to a file. The agent is notified when it exits and reads only the tail or failure summary. A run is never detached (`nohup`, a trailing `&`, `setsid`, `start /b`), because a detached run cannot notify, outlives the agent, and still counts against the governor.
- At most 4 workers, never `-n auto`, under the conftest memory cap (`tests/_mem_cap.py`). `-m browser` modules run `-n 0`, one module per invocation, one browser run at a time on the PM host (ux-overhaul SC-7).
- The merge-gate scoped run of each plan is itself a list of slices, each quoted with the head SHA from `git rev-parse` in the same turn.

Plans 01, 02 and 05 fork from `main`, which has no `browser` marker until ux-overhaul 05 lands (the marker is at `c1:pyproject.toml:85`, `:94`, absent at `37c8e983:pyproject.toml:84`). Their UI evidence is therefore the capturer's screenshots, and they add no `-m browser` module before that.

### RC-12: earlier criteria that cross plans

- ux-overhaul 05 S-4.7 (the poll leaves the windowed headline, vs-rows and $-saved panel as at the ref) and NB-4 (the stream and panel exactly as at the ref) are superseded by plan 03, AC-1 and AC-3 (03 plan, Earlier criteria). They stay true on `ux-overhaul/05` and at its PR. The supersession lands on `refresh-timing/03`. Plan 03 C1 rewrites the tests that pin S-4.7 (`c1:tests/dashboard/test_dashboard_charts_refresh.py:448`, `tests/dashboard/test_closed_market_bounce_js.py:160-210`) and plan 03 C2 rewrites those that pin NB-4's stream (`tests/realtime_push/test_client_sse_wiring.py:61-90`), each with the reason in the commit message. ux-overhaul 05 is not reopened.
- ux-overhaul 05 C2's `writeVerdict` writes nothing for an absent `guard_alpha` (`c2:static/index.js:378-387`). That becomes the dash in plan 03 C4 (03 plan, S-5.10), and only on a tip holding plan 01. At the ref the frozen body carries no `guard_alpha` (03 approach, finding 2), so on a tip without plan 01 the dash would flash over every triggered card's server-painted verdict after the close. 03 C4's Branch base therefore includes plan 01's tip. C4 rewrites ux-overhaul 05's two "write nothing" tests to plan 01's frozen body, and ux-overhaul 05 S-2.10 (three polls never flash the dash) must still pass on the merged tip.
- Two newest-request counters survive the ux-overhaul 05 assembly: `_heroRequestSeq` (`c1:static/index.js:17`) and `_stateRequestSeq` (`c3:static/index.js:1495`). Neither replaces the other. `_refreshCount` (`c1:static/index.js:19`) increments for the newest request's handled outcome only, whether a rendered body or a handled failure, and never for a superseded one (overview R-94, G-88). At C1 it increments on every settle (`c1:static/index.js:1734-1736`), and C3's `loadState` has no count. If the assembled `ux-overhaul/05` tip does not already gate the increment on `_stateRequestSeq`, plan 03 C2 does (03 AC-2). Plan 03's new counters (`_stripRequestSeq`, `_summaryRequestSeq`, `_chartRequestSeq[id]`, `_detailRequestSeq`) are one per element, per overview SD-6. Plan 04's `PSRequest.issue(key)` serves the four other pages and `/` controls' POSTs, and replaces no counter on `/`.
- Plan 02 S-6.x's node tests drive `updateEngineStatus` with the browser clock at plan 02's head. Plan 03 C3's test-writer rewrites them to pass a receipt record (RC-3) once Stale moves to server time, with the reason.
- ux-overhaul SC-3's note that the dashboard's `HH:MM ET` stamps are unchanged (ux-overhaul NB-11) is superseded on `/api/state` by plan 01 C3 (overview G-36, R-25). The date is added through RC-5.
- ux-overhaul 04 AC-4 S-4.6 and NB-5 (the pill's look and its `_build_meta` source) are kept; RC-2 adds only the refresh. ux-overhaul 04 rewrites `static/chrome.js` and every page head. Plan 04's tip merges plans 02 and 03 and resolves both, by RC-9 integration tasks.
- `feature-plans/dashboard-realtime-push.md` AC-3 and AC-5 and DE-SSE-PUSH-001 ("native reconnect") are contradicted by plan 03 AC-3 (03 plan, Earlier criteria).

## Criterion Thread

The overview has no acceptance criteria, so no row names one. Each row threads a shared contract to the requirement items it serves, its design element at the ref, the seam that pins it, and the Dispatch Ordering row that delivers it.

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| none (RC-1 engine label) | R-6, R-13, R-14, R-16, R-56, R-62, R-76 | `static/chrome.js:208-240` (`updateEngineStatus`, the ref's only label writer), `:226` (browser-clock Stale removed), `:242-246`, `:293`; `templates/_chrome.html:51-53`, `:57` | 03 C2 and C3's `tests/dashboard/test_dashboard_poll_errors.py` and `test_dashboard_liveness.py` on `c1:tests/dashboard/_index_js_harness.py` loading `chrome.js` then `index.js`; 04 C1's `tests/ui/test_header_refresh_client.py` on `tests/ui/_page_js_harness.py`; bodies from the real routes (producer output over RC-7 records); every label row of RC-1 a case, Degraded with each reason and an unknown one | 03 |
| none (RC-2 mode pill) | R-63 | `templates/_chrome.html:93-95` (pill), `templates/index.html:861` (legend), `templates/index.html:1872` (`index.js` on `/` only), `templates/_chrome.html:218` (`chrome.js` on every page) | 03 C5 `tests/dashboard/test_dashboard_render_follow.py` (S-6.1) with `chrome.js` loaded; 04 C1 `tests/ui/test_header_refresh_client.py` (S-1.4); FX-STATE-LIVE-MODE, producer output with `LIVE_EXECUTION` patched (`tests/ui/test_cycle_1_foundation.py:292-330` pattern) | 03 |
| none (RC-3 receipt record) | R-6, R-62, R-70 | new: `receiptTiming` in `static/chrome.js`; no receipt record exists (`grep -n "performance.now\|headers.get" static/*.js` at the ref finds none) | 03 C2 node runs with the harness's fake `performance.now` and response `headers.get`; 04 C1 node runs of `PSRequest.getJson`; the `Date` header from the real Flask response | 03 |
| none (RC-4 plan 02 seams) | R-18, R-28, R-29, R-30 | `app.py:726` (`_notify_cycle_complete`), `:776` (`trigger_alpha_bot`), `:393` (`health`); engine ids `alpha_bot_execution.py:946`, `:1741` | `tests/realtime_push/test_cycle_completion_event.py:26`, `:38` (02 C1); new `tests/app/test_scheduler_single_flight.py` (02 C2); new `tests/realtime_push/test_cycle_log_line.py` (05 C4) asserting the event and the line from one record | 02 |
| none (RC-5 data stamp) | R-5, R-13, R-25, R-52, R-77, R-78 | new: `_format_data_stamp` before `app.py:2046`; no dated helper (`git grep -n "%Y-%m-%d %H:%M ET" 37c8e983 -- '*.py' ':!tests'` empty); replaces `app.py:2310`, `:2312`, `:2314`, `:2403`, `:2877`, `:3374`, `:3376`, `:3378` | 01 C3 unit cases in `tests/realtime_push/test_data_freshness_visibility.py:111`: aware, naive, `None`, `time`, `seconds`, `previous_session`, a DST pair; 04 C3 parity test against `analytics.format_et_stamp` over the same instants | 01 |
| none (RC-6 completed session) | R-13, R-48, R-49 | new: `most_recent_completed_session` at the end of `market_calendar.py` (after `:107-136`); uses `:41`, `:81-104` | 01 C3 new `tests/market_calendar/test_most_recent_completed_session.py` on the real XNYS calendar: weekday evening, Monday pre-market, a holiday, a half day before and after 13:00, the lookback bound | 01 |
| none (RC-7 fixtures) | R-1, R-89 | new: `tests/fixtures/refresh_timing/capture.py`, pattern `tests/fixtures/prod_droplet/capture_from_droplet.py:1-30` | each subfolder's `PROVENANCE.md`, read by `qa`; `plan-trace check-coverage` per cycle; a derived record's row held until the sign-off is recorded | FX |
| none (RC-8, RC-9 stack) | R-83, R-84, R-85, R-86, R-87 | `feature-plans/refresh-timing/00-overview.md:242-249` (Stack and Merge Rules) | at each PR: `git merge-base --is-ancestor <model-unpin merge> <tip>` exits 0; `git log --merges` shows each input entering by a merge commit; no forced update on the branch | PM |
| none (RC-10 restart) | R-6, R-11, R-14, R-20 | new: PM host procedure; no launcher is tracked (ux-overhaul SC-8) | 03 C2 `tests/ui/test_dashboard_recovery_browser.py` (S-3.6), 04 C1 `tests/ui/test_header_refresh_browser.py`; the capture record's stop, start and `daemon_started_at` fields | PM |
| none (RC-11 governor) | R-1 | `pyproject.toml:84` (addopts); `tests/_mem_cap.py` | every cycle's slices quoted with time and head SHA in its coverage map | PM |
| none (RC-12 earlier criteria) | R-45, R-75, R-88 | `c2:static/index.js:378-387`, `c1:static/index.js:17`, `:1734-1736`, `c3:static/index.js:1495` | 03 C1, C2, C4 rewrites named in RC-12 with reasons; ux-overhaul 05 S-2.10 rerun on 03's C4 head | 03 |

## Execution Model

This file dispatches no feature team. Each plan approach sizes its own pairs and reviewers. The suite adds only the following:
- One `test-writer` for the fixtures branch (FX: `capture.py`, the root files, the PM's parked captures). It is tests-only and dispatched first, because the scratchpad database copies behind the launcher are temporary.
- One fresh `test-writer` and `implementer` pair per plan for the integration task before that plan's PR (RC-9); plan 01 sizes its own (I-1), so this file counts the pairs of plans 02 to 05. Base merges at a cycle's start are done by that cycle's own pair and add no agent. A later re-merge of a lower plan's review fix, or of `main`, is a further fresh pair when it happens.
- A conflicted integration head adds one `qa` and one `code-reviewer` (RC-9), counted when it happens.

The PM provides the RC-10 launcher and its armed restarts, the RC-7 droplet captures, the assembly branch refs, and the PRs. No worker runs a launcher, reaches the droplet or opens a PR.

Concurrency: plans 01, 02 and 05 start from `main` at once. Plan 03 starts on the assembled `ux-overhaul/05` tip. Plan 04 starts when the `ux-overhaul/04-app-shell` tip exists and its C1 when plan 03's C3 and C5 heads are on `refresh-timing/03`. Tests run per RC-11.

Team size: 9 = 1 test-writer (FX) + 4 test-writer + 4 implementer (the pre-PR integration pairs of plans 02, 03, 04 and 05; plan 01 counts its own I-1 pair and FX-01 test-writer); each plan sizes its own team (01 39, 02 30, 03 29, 04 50, 05 21), 169 in all, so the suite with these 9 is 178

## Dispatch Ordering

At suite level a row is a plan's assembly branch, the fixtures branch, the PM's work or model-unpin, not a cycle; each plan's own Dispatch Ordering orders its cycles inside its branch.

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| MU | | `main` at 37c8e983; model-unpin's cycles (`model-unpin/c1` to `c10`) land on `main` first |
| FX | | `main` at 37c8e983, branch `refresh-timing/fixtures` |
| PM | | no branch: the RC-10 launcher, RC-7 droplet captures, assembly refs and PRs |
| 01 | MU: model-unpin merged on `main` (before the PR); FX: `refresh-timing/fixtures` when C1 reads a parked capture | `main` at 37c8e983, branch `refresh-timing/01`; C5 forks after `main` holding ux-overhaul 05 is merged into C5's branch (RC-9 base merge); C6 from `main` at the ux-overhaul 05 merge commit |
| 02 | MU: model-unpin C9's 04:15 slot, merged into 02 C2's branch (`main` if landed, else `model-unpin/c9` at 0d771793) | `main` at 37c8e983, branch `refresh-timing/02` |
| 05 | 02: `CycleRecord`, `_publish_cycle`, `_engine_run_state`, `_begin_engine_run`, `_end_engine_run`, `_now_et`, `_monotonic` and `_log_skipped_minute`, from 02 C2's passed head (which carries 02 C1 and C3) | `main` at 37c8e983, branch `refresh-timing/05`; C4 forks from 05 C1's head with 02 C2's passed head merged in; the PR waits for 02 on `main` |
| 03 | 02: the `cycle-complete` payload, `engine_phase`, `_health_status` and 02 C5's chrome.js branches (02 tip, for 03 C2); 01: frozen `symphonies`, `guard_alpha`, the RC-5 stamp (01 tip, for 03 C4 and C5); FX: the launcher captures parked on `refresh-timing/fixtures` | the assembled `ux-overhaul/05` tip, branch `refresh-timing/03` |
| 04 | 03: `renderEngineLabel`, `noteDataReceived`, `noteRefreshFailed`, `updateChromeTicker`, `resyncNextCountdown`, `receiptTiming` and `updateModeIndicators` in `static/chrome.js`, and `degraded_reasons` on `/api/state` (03 C2, C3 and C5 heads, for 04 C1); 02: `_engine_phase`, `_health_status` (02 tip, for 04 C1); 01: `_format_data_stamp`, through 03's tip (for 04 C3 and C4) | the `ux-overhaul/04-app-shell` tip, branch `refresh-timing/04`, with plan 02's tip and the `refresh-timing/03` tip holding 03 C2, C3 and C5 merged in by an RC-9 integration task before C1 forks |

## Per-Cycle Detail

- 03 C2 (RC-1, RC-3): `static/chrome.js` gains `renderEngineLabel`, `noteRefreshFailed`, `noteDataReceived`, `receiptTiming`; `updateEngineStatus` and `updateChromeTicker` become the entry points of RC-1, carrying plan 02 C5's two branches. `static/index.js` `loadState` creates the receipt record and stops writing the label in `showConnectionLost`.
- 03 C3 (RC-1): Not updating, Degraded with `DEGRADED_REASON_TEXT`, the server-reckoned Stale, the countdown rules; `app.py` `get_state()` adds `degraded_reasons` to `_additive` (`app.py:2527-2532`) from plan 02's `_health_status()`.
- 03 C1 (RC-2 seam): `c1:tests/dashboard/_index_js_harness.py` gains the `scripts=` option loading named files in page order.
- 03 C5 (RC-2): `updateModeIndicators(meta)` in `static/chrome.js`; `static/index.js` calls it in its renderer list; `templates/index.html:861` gains `data-field="legend-mode"`.
- 02 C1 to C5 (RC-4): the seams of the RC-4 table, per 02's approach.
- 05 C4 (RC-4): the cycle line from the record; `_log_skipped_minute` replaced.
- 01 C3 (RC-5, RC-6): `_format_data_stamp` in `app.py`; `most_recent_completed_session` in `market_calendar.py`; first production use the next `/api/state` poll after deploy.
- 04 C1 (RC-1, RC-2, RC-3, RC-4): `GET /api/header-status` with plan 02's helpers; the header poller calling `receiptTiming` through `PSRequest.getJson`, then `noteDataReceived`, `updateChromeTicker` and `updateModeIndicators`; no label, countdown or pill code of its own.
- 04 C3 and C4 (RC-5): `/history` and `/performance` `data_as_of` through `_format_data_stamp`.
- FX (RC-7): `tests/fixtures/refresh_timing/capture.py`, `PROVENANCE.md`, `.gitignore`, the parked captures.
- PM (RC-8 to RC-11): assembly refs, launcher and its armed restarts, droplet captures, PRs in RC-8's order.

## Per-Cycle Test Plan

The overview has no Rule-by-Entry-Point Matrix. The entry points through which the shared contracts are exercised are listed here and raised (Items Raised 7); each plan's census stays in its approach.

| Entry point | Rules |
|---|---|
| GET /api/state (`app.py:2514`) | RC-1, RC-2, RC-3, RC-5 |
| GET /api/header-status (new, plan 04 C1) | RC-1, RC-2, RC-3, RC-4 |
| GET /api/events (`app.py:2478`) | RC-4, RC-10 |
| GET /health (`app.py:393`) | RC-4 |
| Daemon start (`app.py:9013-9016`) | RC-10 |

### 03 (RC-1, RC-2, RC-3)

- RC-1 family, each a test, on `/` and on one other page: every row of the label table alone; each adjacent pair at once, so the higher one shows (Connection Lost with stale data, Not updating in the blackout as 03 S-4.14 states, Degraded while closed, Closed with a Stale age, Rebalance pause with a Stale age as 04 S-1.10 states, Opening with a Stale age); Degraded with each reason, two reasons, and an unknown reason; a response with no `Date` header keeping the previous Stale judgement; the countdown hidden exactly in Connection Lost and Closed; the browser clock moved 180 s with the label unchanged (03 S-4.10, S-4.12).
- RC-2 family: LIVE, DRY RUN, absent `mode` (writes nothing); the legend span present on `/` and absent elsewhere; a page with the header poller and `index.js` never both writing.
- RC-3 family: `Date` present, absent, unparseable; `sentAtMono` before the fetch and `receivedAtMono` at headers, checked by the order of the harness's fake `performance.now` calls.

### 01 (RC-5, RC-6)

- RC-5 family: aware ET, aware UTC, naive (`--`), `None` (`--`), `time` (date not recorded), `seconds=True`, `previous_session=True`, an instant on each side of the 2026-11-01 DST change; output never contains the request time, checked by freezing the clock far from the moment.
- RC-6 family: as in the Criterion Thread row.

### 02 and 05 (RC-4)

- 05 C4 asserts the queued event payload and the cycle line from one run field by field (05 S-2.5), and that each skip writes exactly one line; 02's S-5.1 test is rewritten by 05 C4's test-writer to read that line.

### FX (RC-7)

- `capture.py` refuses a database path it cannot open read-only (a `mode=ro` connection that writes raises), and every case module's records carry a `PROVENANCE.md` row; the suite has no fixture tests of its own beyond those each plan's cycles write.

### Screenshot and restart gates

- Captures follow ux-overhaul SC-8 Mode A and SC-1; restarts follow RC-10, with its record fields. The capturer never clicks an operator-action control.

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 03, 04 | Plans 03 and 04 each build a label resolver, countdown and pill writer in `static/chrome.js` (03 approach `renderEngineLabel`; 04's first draft `resolveEngineLabel`), giving two implementations or a conflict | High without RC-1 | Medium | RC-1 and RC-2 name one function each; 04 C1 forks after 03 C2, C3 and C5; Cycle-Close search for a second writer |
| 03 | `updateModeIndicators` placed in `static/index.js` cannot run on the other pages: `index.js` loads only at `templates/index.html:1872` | Certain | Medium | RC-2 places it in `chrome.js` (`templates/_chrome.html:218`) |
| 03 | Moving Stale off the browser clock breaks 02's node tests that drive `updateEngineStatus` with `Date.now()` (02 S-6.x) and `tests/execution/test_cycleat_fix.py:470` | High | Low | RC-12: 03 C3 rewrites them with the receipt record |
| 03, 04 | The Degraded label has no scenario in plan 03 or 04, and SD-13 names no such state, so a test-writer has no S id to carry it | Certain | Medium | Items Raised 1: the overview's SD-13 and the two plans gain the state and its scenarios before 03 C3's RED |
| 04 | Friday evening's EOD run includes the autotuner, so `engine_overrun` holds for its whole length (02 approach, accepted PM-2), and every page reads Degraded over Closed then | High | Low | True report under the ruling; the title names the reason |
| 01, 04 | Two formatters for one stamp text: RC-5 and ux-overhaul SC-3's `analytics.format_et_stamp`, which ux-overhaul 02 adds after refresh 01 forks | Medium | Low | RC-5 is the only SD-1 server writer; 04 C3's parity test pins equal text on a base holding both |
| 01, 02 | Both edit `market_calendar.py` beside `session_close` (`market_calendar.py:81-104`) | High at merge | Low | RC-6 places 01's helper at the end of the file |
| All | A database copy (`prof.sqlite`, the launcher's copy) is committed with a fixture; `.gitignore:40` covers `*.db` but not `*.sqlite` | Medium | High | RC-7 folder `.gitignore` and the Cycle-Close size and name check |
| All | The scratchpad copy the launcher and the audit used is gone before a cycle needs its captures | High | Medium | RC-7: launcher captures parked on `refresh-timing/fixtures` at once; droplet captures by the PM's read-only run |
| All | A PR merged by squash, the repository's habit (`37c8e983` "(#145)"), leaves its commits in every upper tip | High | High | RC-8: `gh pr merge --merge` only |
| All | The PM resolves a merge conflict by hand, a code edit outside the pair (03 approach Execution Model: "The PM keeps the assembly branch ... and enters ... by merge commits") | Medium | Medium | RC-9: every merge is a pair task |
| 03, 04 | Playwright's `page.route` may not fulfil an `EventSource` request in Firefox (unverified) | Medium | Medium | RC-10 fallback to `tests/ui/_browser_server.py` and a real `_publish_cycle` |
| 03, 04 | A capturer waits for a restart nobody performs, or the PM must remember it mid-task | Medium | Medium | RC-10: the restart is armed by the PM as a scheduled command before the dispatch |
| All | A slice of 300 s or more run twice in an hour blocks the worktree; a detached run loses its notification | Medium | Medium | RC-11 |
| 05 | A reader joins 05's line or the event to the engine's telemetry rows by `cycle_id` and silently matches nothing (formats differ, `alpha_bot_execution.py:946`, `:1741`) | Low | Medium | RC-4 join rule; DC-4 of plan 05 greps `cycle_id` across the event, `/health` and the line |

## Cycle-Close Criteria

These hold for every cycle of every plan, in addition to its own approach's list, and never gate another cycle's start:
- The cycle's coverage record `records/coverage.json` under `.claude/review/<HEAD-sha12>/` has a test or a held row with its reason for every scenario the cycle owns, and `python3 ~/.claude/scripts/plan-trace check-coverage <record> --plan <plan> --approach <approach> --repo . --head <sha>` exits 0.
- No second implementation of a shared contract was added, checked on the cycle's diff: no write to `#engine-status-label`, `#engine-status-dot`, the `#engine-status` title, `#next-run-countdown`'s visibility or `[data-testid="mode-pill"]` outside `renderEngineLabel` and `updateModeIndicators`; no `Date.now()` or `new Date(` in a Stale or age computation in `static/`; no `strftime("%H:%M ET")` or `"%Y-%m-%d %H:%M ET"` outside `_format_data_stamp`; no previous-session computation outside `market_calendar.most_recent_completed_session`; no `cycle_id` comparison against an engine telemetry id.
- Fixtures: every new file under `tests/fixtures/refresh_timing/` sits in a `<NN>-c<K>/` folder with a `PROVENANCE.md` row; `git diff --name-only` adds no `*.sqlite`, `*.db` or `*.db-*` file anywhere and no fixture file over 1 MB except a gzipped record its `PROVENANCE.md` justifies; no derived record without a recorded sign-off.
- The cycle's slices each ran under 300 s as background, non-detached runs (RC-11), quoted with the head SHA.
- Every capture behind a UI verdict carries ux-overhaul SC-8's record and, for a restart, RC-10's fields.
- A base merge (RC-9) was committed before RED and its slices passed.

## Merge Criteria

Per plan PR, on the exact head being merged, in addition to the plan's own list:
- The PR runs from `refresh-timing/<NN>` into `main` in RC-8's order; every lower plan it consumed and model-unpin are on `main`, and `main` is merged into the tip (`git merge-base --is-ancestor` for each); every input entered by a merge commit, made by an RC-9 pair; no rebase, no force push.
- `ruff format --check` and `ruff check` clean; the plan's scoped pytest in RC-11 slices, at most 4 workers, quoting the head SHA; CI green on that SHA; for plans 03 and 04, every `-m browser` module on the PM host against the Mode A launcher, results under `.claude/review/<head-sha12>/`.
- `git diff main...HEAD -- math_engine.py` is empty; `alpha_bot_execution.py` changes only inside the close-snapshot builder (plan 01) or not at all; the overview's NB-14 boundary tests pass.
- `/review` run on the PR and its findings resolved or accepted in writing; then the PM's live functional check on the droplet against the real daemon, database and APIs, because a mocked-green suite has repeatedly diverged from live behaviour here. For plans 03 and 04 it includes the header label on every open page at 15:56 and 16:01 ET and after one daemon restart.
- The PR merges with a merge commit (`gh pr merge --merge`), never a squash.
- At the last PR, the overview's Suite Gate runs once against `main` plus that PR.

## Items Raised to the PM

1. The Degraded label (RC-1) follows the PM's ruling that the liveness fields feed SD-13. It changes plan text that the plans own:
   - The overview's SD-13 names seven states and needs the eighth, at rank 3.
   - Plan 03 AC-4 and plan 04 AC-1 have no scenario for it, so their test-writers have no S id to carry it.
   - `/api/state` gains `degraded_reasons`, a field neither plan's Contract Changes lists. This design assigns it to 03 C3 from plan 02's `_health_status()`.

   Until the amendments land, 03 C3 and 04 C1 hold Degraded as a design element with no scenario row. Settle: amend SD-13, 03 AC-4 and 04 AC-1, or drop the state. Only `degraded_reasons` changes the label. `status`, `scheduler_last_tick_at`, `engine_running_since` and `last_cycle` change none (plan 04's Items Raised 5 on unread fields stands).
2. `updateModeIndicators` keeps plan 03's name and moves from `static/index.js` to `static/chrome.js`, because `index.js` loads only on `/` (`templates/index.html:1872`). 03's approach (Architecture "Mode", C5 Per-Cycle Detail) needs that edit. Its C5 node test loads `chrome.js`, so the harness's `scripts=` loader moves from 03 C2 to 03 C1.
3. The receipt record has one creator, `receiptTiming(sentAtMono, response)` in `static/chrome.js` (03 C2). Plan 04's `PSRequest.getJson` calls it rather than building its own. The server time is the `Date` header on both pages, so `/api/header-status` needs no `server_time` field (04's first draft had one).
4. Plan 04's approach (Architecture "Pages" and "Adopted, not invented") formats the `/history` and `/performance` `data_as_of` with ux-overhaul SC-3's `analytics.format_et_stamp`. By RC-5 and the brief, it calls `_format_data_stamp`, and 04 C3 adds the parity test.
5. The brief says plans 02, 03 and 04 reuse `_format_data_stamp` and `most_recent_completed_session`. At the approaches' current text only plan 04 needs one of them (`_format_data_stamp`). Plan 02 writes ISO machine fields and today's ET date. Plan 03 prints server strings. Recorded so no plan adds a call it does not need.
6. Fixture paths differ from RC-7:
   - Plan 03 uses `tests/fixtures/dashboard/refresh_timing/<topic>/` (C1 to C5 Per-Cycle Detail).
   - Plan 01 uses a flat `tests/fixtures/refresh_timing/` with one `PROVENANCE.md`.
   - Plan 04 uses `tests/fixtures/refresh_timing/post_requests_at_base.json`.

   Under RC-7 these become `tests/fixtures/refresh_timing/03-c<K>/`, `01-c1/` (and `01-c2/` for R6, R8 and the goldens), and `04-c9/`. Plan 02's `02-c<K>/` already matches.
7. The census entry points of this file, GET /api/state (`app.py:2514`), GET /api/header-status (new, plan 04 C1), GET /api/events (`app.py:2478`), GET /health (`app.py:393`) and Daemon start (`app.py:9013-9016`), are not columns of a matrix, because the overview has none. Each is carried by the plan approaches named in its RC.
8. Plan 03's Execution Model says "The PM keeps the assembly branch `refresh-timing/03` ... and enters plan 01, plan 02 and each passed cycle head by merge commits". Under RC-9, the PM creates the ref, and every merge is a test-writer and implementer task. Plans 01, 02, 04 and 05 do not name who merges. RC-9 applies to all five.
9. Plan 04 C1 waits on plan 03's C2, C3 and C5 heads, a dependency beyond plan 04's Depends on (plan 03 before the AC-2 cycle only). The PM ruled this already for C1; RC-2 adds C5, because the pill writer is built there.
10. RC-10 needs a launcher that can stop and restart itself at an armed time on the same port, served SHA and database copy. The launcher is a PM host tool outside the repository (ux-overhaul SC-8), so that option is the PM's to provide before the first restart capture (plan 03 C2).
11. Plan 03's approach cites `ux-overhaul/05-c1@2b7719ea` and `-c2@f7741490`. The local heads at this writing are `a539c74f` and `2294032a`, and `origin` shows `8c35a72f` and `59a1e9dd`. This document cites the local heads. The assembled `ux-overhaul/05` tip, recorded by the PM at assembly, is the one every plan 03 cycle re-derives from.
