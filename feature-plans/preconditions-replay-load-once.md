# Feature: The preconditions route reads its replay cache file once
Status: ready
Story: none: the PM's timing finding of 2026-11-15, saved verbatim with the intake answers in feature-plans/preconditions-replay-load-once.source.md
Type: perf
Suite: none
Repos: PlanetStopper
Base: main at aafe476d (branch feat/preconditions-replay-load-once)
Ref: PlanetStopper@aafe476d62401f041aeae67c563742f884d8b4af
Sensitive: none: an advisory, read-only route with no trade, credential or money surface (G-20)
Depends on: none (G-19)

## Trigger
The PM's post-deploy timing check on the droplet (2026-11-15, at aafe476d) measured `GET /api/guard-alpha-preconditions` at about 22 s, with 93% of py-spy samples inside the JSON decode of the synthetic-history cache file, and found that a concurrent `/api/performance` went from about 1.2 s alone to about 18 s. The finding, the operator's standing deploy rule and the PM's intake answers are saved verbatim in `feature-plans/preconditions-replay-load-once.source.md` and quoted in Requirements as Given (G-1 to G-31).

Measurements from the PM (G-3 to G-12): the droplet's cache directory holds 19 files totalling 1.1 GB, recent files are 36 to 147 MB and the newest is 71 MB; in the droplet browser `/api/performance?scope=aggregate&days=60d` completed at 20 to 21 s and the preconditions request at 24 to 25 s, against 1.16 to 1.30 s for `/api/performance` alone; a local server on a smaller database copy answered the preconditions route in 2.7 s.

Known facts at the ref:
1. The route reads the bot state once (`app.py:6556-6560`), then for each held symphony (`app.py:6606-6608`) calls `autotuner.build_if_held_replay_series(sym_id)` (`app.py:6611`) and `analytics.get_shadow_current_return_daily_series` (`app.py:6627`), and returns `{"symphonies": ...}` (`app.py:6652`). A replay-seam exception degrades that symphony's replay row (`app.py:6612-6624`); any other per-symphony exception drops that entry (`app.py:6643-6650`). The degraded row is `{rho: null, rho_ci: null, sharpe_daily: null, n_obs: 0, verdict: "INSUFFICIENT_DATA", sample_source}` (`app.py:6587-6598`).
2. `build_if_held_replay_series` reads the bot state again (`autotuner.py:1589`), resolves the ET date (`autotuner.py:1601`), calls `synthetic_history.get_cached_synthetic_history_only` (`autotuner.py:1603`) and returns the symphony's series or None (`autotuner.py:1604-1608`); the series is `ticks[-1]["return"]` for every date of that symphony in the decoded history (`autotuner.py:1539-1553`). The route is its only production caller (`app.py:6611`).
3. The cache key hashes every bot-state entry's `current_holdings` into one portfolio-wide file name under the relative `cache` directory, `synthetic_history_v4_` plus the date and the hash plus `.json` (`synthetic_history.py:487-536`), so all held symphonies resolve the same file: 11 symphonies decode the same file 11 times per request.
4. The reader walks back from the current ET date through `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS` = 10 NYSE trading days (`synthetic_history.py:545`, `synthetic_history.py:626`), re-deriving the key for the current holdings at each candidate date (`synthetic_history.py:628`); a missing candidate is collected (`synthetic_history.py:630-634`), an existing one is decoded by `load_cached_history` (`synthetic_history.py:640`), and one INFO miss summary is logged per call (`synthetic_history.py:642`, `synthetic_history.py:651`, wording at `synthetic_history.py:566-574`). It never fetches.
5. `load_cached_history` opens the file and decodes it whole with `json.load`; on failure it logs `WARNING "synthetic_history: corrupt or unreadable cache file %s: %s"` and returns None (`synthetic_history.py:264-281`).
6. The writer, `generate_synthetic_history`, writes the file with `open(cache_file, "w")` and `json.dump` in place, not atomically (`synthetic_history.py:841-851`); the autotune calls it (`autotuner.py:2440`).
7. `static/performance.js` fetches the route after the symphony list loads and then every 60000 ms together with `refresh()` (which requests `/api/performance`) and the exit-turnover panel (`static/performance.js:914-924`, `static/performance.js:1079-1098`).
8. The daemon serves requests with `app.run` (`app.py:13316`) on Flask 3.1 (`requirements.txt:2`), whose server is threaded by default, in the same process as the scheduler thread (`app.py:13310`).
9. Existing code that holds a computed result between requests, for a reviewer to compare against: `analytics._HISTORY_CACHE` keyed on the newest post-mortem modification time (`analytics.py:91-92`, `analytics.py:1650-1680`); `app._post_mortem_signature`, a file's existence, `st_mtime_ns` and size (`app.py:1246-1251`); `app._ClosedHold`, a bounded holder that builds each identity once under a per-key lock (`app.py:4583-4632`). No existing helper holds the replay series.
10. The cache key md5-hashes the JSON of every symphony's full `current_holdings` list, each entry carrying its `allocation` float (`synthetic_history.py:506-521`; a live droplet entry reads `{'ticker': 'DOG', 'allocation': 0.490653255}`, PM read). The engine rewrites `current_holdings` from Composer's allocations on each cycle and at the close (`alpha_bot_execution.py:920-927`, `alpha_bot_execution.py:1040-1048`, `alpha_bot_execution.py:1682-1686`). So during market hours the key changes every cycle, every candidate in the backward probe misses, no file is decoded, the route is already fast, and the replay rows degrade, at base and after this change alike. The slow path runs only while the current holdings still equal the holdings the weekly autotune hashed when it wrote the file: from the Friday autotune (about 19:50 ET) through the weekend until Monday's first cycle.
11. Correction of the source, by the PM's ruling on this evidence (fact 10): the source's clause that the route "competes with the daemon's minute scheduler thread in the same process during market hours" (G-6) does not hold, because no file is decoded during market hours. The saved source is left verbatim; no criterion or scope row rests on that clause (R-35 removed). The harm that stands is out of hours: on weekends and after the Friday autotune, every Performance page load and every 60 s poll costs about 22 s and holds up `/api/performance` and the other polls.
12. `POST /api/force_eod` runs `autotuner.run_autotuner`, and so `generate_synthetic_history`, on a thread inside the daemon process (`app.py:8570-8622`), the process that serves this route; the scheduled autotune runs in the engine subprocess.

Terms used below: a decode is one read and JSON parse of a cache file's content, whether it succeeds or raises; a file identity is a file's path, size and modification time; the held identity is the one file identity whose derived results (each symphony's daily return list) the route keeps, the last one decoded successfully since the daemon started; a remembered failure is a file identity whose decode failed with an error base classifies as corrupt or unreadable (`synthetic_history.py:274`), kept without any of its content; a warm request is one whose backward probe passes only missing candidates or remembered failures and resolves the held identity; a cold request is any other request that reaches an existing file. Only one identity's results are held, so a return to an earlier file after another was decoded (A, then B, then A) is cold (S-2.16). Base is the route at aafe476d (Definition of Done).

## Summary
Out of hours, when a replay cache file resolves (weekends and after the Friday autotune), the Performance page's stop-justification preconditions panel answers from at most one decode of that file per request, and from none when the file has not changed, with the same JSON as today, so the operator's headline figures and other dashboard requests are no longer held up behind it.
- Each cache file is decoded at most once per request (AC-1).
- A repeat request reuses an unchanged file's derived series and never a changed one's, and a corrupt file is read and logged once per identity (AC-2).
- The route's answer is byte-identical to base for the same state and cache file (AC-3).
- Concurrent requests share one decode (AC-4).
- A measured latency bound holds on a production-sized fixture (AC-5).
- Concurrent `/api/performance`, `/api/header-status` and `/api/candidate-alert` requests are not held up (AC-6).
- Memory stays bounded between and during requests (AC-7).
- The cache-hit-only contract, the probe window, the log lines, auth and the protected files stay as they are (AC-8).

## Requirements as Given
[G-1] Part A, the operator's standing deploy rule, as recorded in the PM's handoff (verbatim): "DEPLOY GATE: droplet updated only after exhaustive UI/UX review AND proof of no timing/refresh issues (closed-market figures never move; morning refresh starts). preconditions-replay-load-once.source.md:2
[G-2] No deploy may force a re-login (no generation bump, SECRET_KEY stable)." preconditions-replay-load-once.source.md:2
[G-3] Part B, the PM's measured finding and request (verbatim): "Problem (measured on the droplet 2026-11-15 at aafe476d): GET /api/guard-alpha-preconditions takes ~22 s. preconditions-replay-load-once.source.md:3
[G-4] py-spy: 93% of samples in json.load inside synthetic_history.load_cached_history, reached through autotuner.build_if_held_replay_series -> synthetic_history.get_cached_synthetic_history_only. preconditions-replay-load-once.source.md:3
[G-5] The route (app.py guard_alpha_preconditions, per-symphony loop at app.py:6611) calls build_if_held_replay_series once per symphony, and each call reloads bot_state and re-parses the SAME portfolio-wide cache file (~71 MB JSON; one file per date+holdings hash, e.g. cache/synthetic_history_v4_2026-11-13_&lt;hash&gt;.json) — 11 symphonies = 11 full parses. preconditions-replay-load-once.source.md:3
[G-6] Because json decoding holds the GIL, a concurrent /api/performance request goes from 1.2 s alone to 18 s, the Performance headline shows '--' for ~20 s, and other requests (header-status, candidate-alert) wait ~30 s; static/performance.js re-polls the route every 60 s while the page is open, so this repeats every minute and competes with the daemon's minute scheduler thread in the same process during market hours. preconditions-replay-load-once.source.md:3
[G-7] WHAT is wanted: the route's answer unchanged (byte-identical JSON for the same state + cache file), the cache file parsed at most once per request, and a repeat request with an unchanged cache file and unchanged state not re-parsing it at all; memory bounded on the 2-core / MemoryMax 3 GiB droplet (do not keep the parsed ~71 MB history resident between requests); cache-hit-only contract unchanged (never a live fetch, Architecture Constraint 5, bill protection); cache-miss/backward-probe/log behaviour of get_cached_synthetic_history_only unchanged; no engine (alpha_bot_execution.py, math_engine.py) diff. preconditions-replay-load-once.source.md:3
[G-8] Acceptance must include a measured latency bound on a production-sized fixture and a proof that a concurrent /api/performance is no longer held up." preconditions-replay-load-once.source.md:3
[G-9] PM supporting measurements (verbatim): "Supporting measurements, which you may summarise in the plan with their numbers: droplet cache directory = 19 files totalling 1.1 GB, recent files 36-147 MB, latest 71 MB. preconditions-replay-load-once.source.md:5
[G-10] Local old-build capture server: the route takes 2.7 s on a smaller DB copy. preconditions-replay-load-once.source.md:5
[G-11] In the browser on the droplet, /api/performance?scope=aggregate&days=60d completed at 20-21 s and the preconditions request at 24-25 s. preconditions-replay-load-once.source.md:5
[G-12] Alone, /api/performance takes 1.16-1.30 s." preconditions-replay-load-once.source.md:5
[G-13] (Intake, Building 1) "Make GET /api/guard-alpha-preconditions answer quickly on production-sized data without holding up other dashboard requests; triggered by the post-deploy timing check on 2026-11-15." preconditions-replay-load-once.source.md:8
[G-14] (Intake, Building 2) "The operator, on the Performance page's Stop-justification preconditions panel; route GET /api/guard-alpha-preconditions, fetched once after the symphony list loads and again every 60 s by static/performance.js." preconditions-replay-load-once.source.md:9
[G-15] (Intake, Building 3) "Open /performance: the headline figures (guard effect, Sharpe, Max DD, Sortino) and chart fill as fast as /api/performance alone allows (~1-2 s on the droplet), the preconditions panel fills with the same rows and verdicts as today, and each 60 s refresh changes nothing visible when nothing changed. preconditions-replay-load-once.source.md:10
[G-16] No workflow file exists for this screen (.claude/workflows/ is absent)." preconditions-replay-load-once.source.md:10
[G-17] (Intake, Building 4, negatives) "cold cache (no file in the backward window) -> the same degraded replay row as today; corrupt or unreadable file -> the same WARNING log and degraded row as today; holdings change -> a different cache key, so the answer is recomputed from the right file; the weekly autotune writes a new file -> the next request uses it; load_state fails -> the same answer as today; two requests at once -> each returns the correct answer (whether the second waits for the first or parses on its own is an open question); a symphony added or removed -> rows follow bot_state as today; nothing the route returns changes." preconditions-replay-load-once.source.md:11
[G-18] (Intake, Building 5) "No wire, schema, migration or config change." preconditions-replay-load-once.source.md:12
[G-19] (Intake, Building 6) "One repo (PlanetStopper); nothing must land first." preconditions-replay-load-once.source.md:13
[G-20] (Intake, Building 7) "Not sensitive: an advisory, read-only route with no trade, credential or money surface." preconditions-replay-load-once.source.md:14
[G-21] (Intake, Not building 1) "No change to the preconditions statistics, verdicts, thresholds (N_MIN_OBS) or the shadow sample; no change to the 60 s poll in static/performance.js; no speed-up of /api/performance itself; no change to the cache file format or its writer (generate_synthetic_history); no change to the autotuner replay." preconditions-replay-load-once.source.md:16
[G-22] (Intake, Not building 2) "Must stay exactly: the route's JSON for the same state and cache file; cache-hit-only (never fetch_bars / never a live Alpaca fetch); the backward probe up to AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS; the cache-miss INFO summary and corrupt-file WARNING; the degraded-row copy." preconditions-replay-load-once.source.md:17
[G-23] (Intake, Not building 3) "Deferred: GIL contention from any other slow route, tracked in the NB-14 backlog." preconditions-replay-load-once.source.md:18
[G-24] (Intake, Not building 4) "Consumes, must not change: synthetic_history cache files and generate_synthetic_history, database.load_state, guard_preconditions.py, analytics.get_shadow_current_return_daily_series." preconditions-replay-load-once.source.md:19
[G-25] (Intake, open question Q1) "May two concurrent requests both parse the file?" Default: no. preconditions-replay-load-once.source.md:21
[G-26] Concurrent misses share one computation; a second request waits for the first instead of parsing again. preconditions-replay-load-once.source.md:21
[G-27] (Intake, open question Q2) "What identifies an unchanged cache file?" Default: the resolved file path plus its size and modification time, re-checked on every request with no time-based expiry. preconditions-replay-load-once.source.md:22
[G-28] (Intake, open question Q3) "Latency bound?" Default: on a production-sized fixture (at least 11 symphonies and a cache file of at least 70 MB), a cold request parses the file at most once, and a warm request parses it zero times and takes under 0.5 s. preconditions-replay-load-once.source.md:23
[G-29] A concurrent /api/performance during a warm preconditions request is not delayed by more than 0.5 s against running alone. preconditions-replay-load-once.source.md:23
[G-30] Leave the cold-request wall-clock bound to the architect to measure, stated as a ratio (at most 1/5 of the current per-symphony cost). preconditions-replay-load-once.source.md:23
[G-31] (Intake, open question Q4) "Memory?" Default: between requests keep only the derived per-symphony daily return lists (small), never the parsed history dict. preconditions-replay-load-once.source.md:24

## Requirement Inventory
| R-n | Requirement item | Source (G ids) | Maps to (AC ids, or NB-n) | Scope (full, or narrowed: what is left out) |
|---|---|---|---|---|
| R-1 | The droplet is updated only after exhaustive UI/UX review and proof of no timing or refresh issues: closed-market figures never move and the morning refresh starts | G-1 | AC-3, NB-8 | narrowed: the review and the deploy are the PM's gate (NB-8); this plan asserts only that its route's closed-market answer does not move (S-3.12) and that it changes no poll or refresh path (NB-2) |
| R-2 | No deploy may force a re-login: no logout generation bump, SECRET_KEY stable | G-2 | NB-15 | full |
| R-3 | The preconditions route's ~22 s, dominated by decoding the same portfolio-wide cache file once per held symphony, is removed | G-3, G-4, G-5 | AC-1, AC-5 | full |
| R-4 | The cache file is parsed at most once per request | G-5, G-7, G-28 | AC-1 | full |
| R-5 | A repeat request with an unchanged cache file (same path, size and modification time, re-checked every request, no time-based expiry) and unchanged state does not parse it at all | G-7, G-27, G-28 | AC-2 | full |
| R-6 | The route's answer is unchanged: byte-identical JSON for the same state and cache file | G-7, G-17, G-22 | AC-3 | full |
| R-7 | A concurrent /api/performance is no longer held up: during a warm preconditions request it is delayed by at most 0.5 s against running alone | G-6, G-8, G-29 | AC-6 | full |
| R-8 | Other dashboard requests (header-status, candidate-alert) are not held up | G-6, G-13 | AC-6 | narrowed: within 0.5 s of their alone time during a warm preconditions request; during a cold one (after each restart, a new autotune file, a holdings change, or the first read of a corrupt file) they may be delayed by the decode time that request spends (S-6.4, S-6.8, S-6.10, S-6.11) |
| R-9 | A measured latency bound on a production-sized fixture (at least 11 symphonies, a cache file of at least 70 MB): a warm request under 0.5 s with zero parses, a cold request with at most one parse and a wall-clock time stated as a ratio against today's | G-8, G-9, G-10, G-11, G-12, G-28, G-30 | AC-5 | narrowed: decode counts are the primary assertion (cold at most 1, warm 0); the cold wall-clock bound is one decode of the fixture file plus 1.0 s, looser than the source's "1/5 of the current per-symphony cost" (one fifth of one decode), which no cold path that decodes the whole file once at today's decode speed can meet (Q-3) |
| R-10 | Memory bounded on the 2-core, MemoryMax 3 GiB droplet: the parsed history is never kept between requests, only the derived per-symphony daily return lists | G-7, G-31 | AC-7 | full |
| R-11 | The cache-hit-only contract is unchanged: never a live fetch | G-7, G-22 | AC-8 | full |
| R-12 | The cache-miss, backward-probe and log behaviour of get_cached_synthetic_history_only is unchanged | G-7, G-22 | AC-8 | narrowed: the probe window, the miss handling and both log wordings are unchanged; the route logs one reader pass's lines per request instead of one per held symphony, and repeat WARNING lines for the same unchanged corrupt file are not re-logged (PM ruling, S-8.3) |
| R-13 | No diff in alpha_bot_execution.py or math_engine.py | G-7 | AC-8, NB-13 | full |
| R-14 | Two requests at once each return the correct answer, and concurrent misses share one computation: the second waits for the first instead of parsing again | G-17, G-25, G-26 | AC-4 | full |
| R-15 | A cold cache (no file in the backward window) gives the same degraded replay row as today | G-17 | AC-1, AC-3 | full |
| R-16 | A corrupt or unreadable file gives the same WARNING log and degraded row as today | G-17 | AC-1, AC-2, AC-8 | narrowed: the replay rows are as at base and the first WARNING's wording is byte-identical to base; repeat WARNING lines for the same unchanged corrupt file are not re-logged, where base logs one per held symphony on every poll (PM ruling, S-2.18, S-8.3) |
| R-17 | A holdings change resolves a different cache key, and the answer is recomputed from the right file | G-17 | AC-2 | full |
| R-18 | When the weekly autotune writes a new file, the next request uses it | G-17 | AC-2 | full |
| R-19 | A load_state failure gives the same answer as today | G-17 | AC-3 | full |
| R-20 | A symphony added or removed: rows follow bot_state as today | G-17 | AC-2, AC-3 | full |
| R-21 | The operator reads the result on the Performance page's preconditions panel, which fetches the route once after the symphony list loads and every 60 s | G-14 | AC-2, AC-3 | full |
| R-22 | On /performance the headline figures (guard effect, Sharpe, Max DD, Sortino) and chart fill as fast as /api/performance alone allows (~1-2 s on the droplet), the panel shows the same rows and verdicts as today, and each 60 s refresh changes nothing visible when nothing changed | G-15 | AC-3, AC-6 | narrowed: the headline bound holds while the preconditions request is warm (S-6.5); on the first page load after a restart or a file change the headline may also wait for one decode (S-6.4) |
| R-23 | No workflow file exists for /performance | G-16 | NB-7 | full |
| R-24 | No wire, schema, migration or config change | G-18 | AC-3, NB-6 | full |
| R-25 | One repo; nothing must land first | G-19 | NB-16 | full |
| R-26 | The route stays an advisory, read-only route with no trade, credential or money surface | G-20 | AC-8 | full |
| R-27 | No change to the preconditions statistics, verdicts, thresholds (N_MIN_OBS) or the shadow sample | G-21 | NB-1 | full |
| R-28 | No change to the 60 s poll in static/performance.js | G-21 | NB-2 | full |
| R-29 | No speed-up of /api/performance itself | G-21 | NB-3 | full |
| R-30 | No change to the cache file format or its writer (generate_synthetic_history) | G-21 | NB-4 | full |
| R-31 | No change to the autotuner replay | G-21 | NB-5 | full |
| R-32 | Stays exactly: the backward probe up to AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS, the cache-miss INFO summary, the corrupt-file WARNING and the degraded-row copy | G-22 | AC-3, AC-8 | narrowed: repeat corrupt-file WARNING lines for the same unchanged file are not re-logged (PM ruling, S-8.3); the probe window, the INFO summary, the first WARNING's wording and the degraded-row copy are unchanged |
| R-33 | GIL contention from any other slow route is deferred to the backlog | G-23 | NB-12 | full |
| R-34 | Consumed and unchanged: the synthetic_history cache files and generate_synthetic_history, database.load_state, guard_preconditions.py, analytics.get_shadow_current_return_daily_series | G-24 | AC-8, NB-14 | full |
| R-35 | The route no longer competes with the daemon's minute scheduler thread in the same process during market hours | G-6 | NB-17 | narrowed: nothing is built for it; the PM's ruling corrects the clause (Trigger facts 10 and 11, D-1): no file is decoded during market hours, so no criterion rests on it, and the weekday unavailability it reveals is deferred (NB-17) |
| R-36 | The Performance headline no longer shows '--' for ~20 s while the preconditions request runs | G-6, G-15 | AC-6 | narrowed: during a warm preconditions request the '--' lasts no longer than `/api/performance` alone plus 0.5 s (S-6.5); during a cold one, up to one decode longer (S-6.4) |
| R-37 | The route answers quickly on production-sized data without holding up other dashboard requests | G-13 | AC-5, AC-6 | full |

## User Tasks
Workflow (/performance): no workflow file: `.claude/workflows/` is absent at the ref (G-16)

### T-1: Read the Performance page while it refreshes
| Step | User action | Input | Expected result | Scenario (S id) |
|---|---|---|---|---|
| 1 | Open `/performance` | none | the guard effect, Sharpe, Max DD and Sortino headline figures and the chart show values within the time `/api/performance` alone takes plus 0.5 s | S-6.5 |
| 2 | Read the Stop-justification preconditions panel | none | the same rows and verdicts as at base for the same state and cache file | S-3.1, S-6.5 |
| 3 | Leave the page open across two 60 s refreshes with nothing changed | none | no text in the headline or the panel changes | S-6.6, S-2.2 |

## Earlier criteria on the same surface
| Earlier criterion (plan and AC) | Surface | Disposition (kept, changed or contradicted) | Why |
|---|---|---|---|
| `feature-plans/guard-alpha-preconditions.md` AC-5 (replay sample is `ticks[-1]["return"]` for every date) | replay sample | kept | the series is derived the same way; AC-3 pins the body byte-identical |
| `feature-plans/guard-alpha-preconditions.md` AC-6 (read-only GET, global auth, per-symphony degradation, never a 500) | `GET /api/guard-alpha-preconditions` | kept | AC-3 and AC-8 (S-8.5, S-8.6, S-8.9) keep them |
| `feature-plans/guard-alpha-preconditions.md` AC-7, AC-8 (panel, empty state, both samples shown) | Performance preconditions panel | kept | the body is unchanged and `static/performance.js` is protected (NB-14) |
| `DE-GUARD-ALPHA-PRECONDITIONS-001` cache-hit-only contract and the R2 backward probe (a decision record, not a plan AC) | replay cache read | kept | AC-8 (S-8.1) and AC-2 (S-2.4, S-2.5) keep them |
| `feature-plans/preconditions-phantom-keys-and-cache-log-noise.md` AC-1 to AC-4 (structural symphony filter, metadata-only empty state) | route rows | kept | S-1.3 and S-3.11 keep them |
| `feature-plans/preconditions-phantom-keys-and-cache-log-noise.md` AC-5 (at most one INFO miss line per walk-back; corrupt keeps the per-file WARNING) | reader logs | changed | the INFO miss line stays at most one per walk-back and the first corrupt-file WARNING keeps its wording, but a WARNING for an unchanged corrupt file is no longer repeated on later polls (PM ruling D-3; S-8.3, S-2.18, Q-6) |
| `feature-plans/preconditions-phantom-keys-and-cache-log-noise.md` AC-6 (zero change to cache lookup semantics, return values, walk-back depth) | reader | kept | AC-2 and AC-8 keep the window and the results; only the route's number of reader passes per request changes |
| `feature-plans/ux-overhaul/01-data-truth.md` AC-3 S-3.9 (one panel note for cold replay samples) and S-3.14 (a failed request keeps the last rendered state) | panel | kept | client code unchanged (NB-14); the body is unchanged |
| `feature-plans/ux-overhaul/01-data-truth.md` AC-5 S-5.2 (rows labelled by display name) | panel | kept | client code unchanged |
| `feature-plans/ux-overhaul/07-final-gate-fixes.md` AC-8 (no-longer-held symphonies excluded, and the unavailable count follows) | route rows | kept | S-1.8, S-2.13 and S-3.11 keep it |
| `feature-plans/refresh-timing/04-pages-and-header.md` AC-1 (the header status read answers in under 1 s on `/performance`) | `/api/header-status` | kept | measured broken by the contention (G-6); S-6.2 restores it during a warm preconditions request |
| `feature-plans/refresh-timing/04-pages-and-header.md` AC-4 (each Performance panel renders independently) | Performance page | kept | no client change |
| `feature-plans/page-explainers-and-density.md` AC-6 S-6.13 (a 500 from the route leaves no empty rectangle) | Performance page | kept | no client or layout change |

## Acceptance Criteria (Building)
### AC-1: Each cache file is decoded at most once per request
Requirement items: R-3, R-4, R-15, R-16
Rule: When `GET /api/guard-alpha-preconditions` resolves its replay samples, the route shall decode each cache file at most once for the whole request, however many held symphonies it reports.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-1.1 | B | the fixture's 12 held symphonies with holdings; one valid cache file for those holdings dated inside the backward window; no request since the daemon started | `GET /api/guard-alpha-preconditions` | 200; that file is decoded exactly once; the body holds 12 entries, each with a `replay` and a `shadow` object |
| S-1.2 | N1 | one held symphony; a valid file for its holdings | GET | the file is decoded exactly once; the body holds 1 entry |
| S-1.3 | N1 | the bot state holds only the five metadata keys and no symphony | GET | 200 `{"symphonies": {}}`; no cache file is decoded |
| S-1.4 | N1 | held symphonies, none carrying `current_holdings` (no cache key resolves) | GET | no cache file is decoded; every entry's `replay` is the degraded row |
| S-1.5 | N3 | held symphonies with holdings; no file for those holdings anywhere in the window | GET | no cache file is decoded; every `replay` is the degraded row; every `shadow` is as at base |
| S-1.6 | N6 | the newest file in the window is corrupt (exists, invalid JSON); an older valid file is in the window | GET | the corrupt file is read once and the older file decoded once for the whole request; every `replay` comes from the older file |
| S-1.7 | N6 | the only file in the window is corrupt | GET | the corrupt file is read once for the whole request; every `replay` is the degraded row |
| S-1.8 | N7 | the fixture's 12 symphonies, one of them no longer held (its `shadow_history` rows stop before the second most recent completed session) | GET | the file is decoded exactly once; the no-longer-held symphony has no entry |
| S-1.9 | N5 | as S-1.1, with the page left open | three 60 s polls of the same GET | each poll decodes F at most once (none, once S-1.1 has decoded it unchanged) |
| S-1.10 | N5 | as S-1.1 | two GETs start together | no request decodes F more than once (once in total, AC-4) |

Negative coverage: N1 S-1.2, S-1.3, S-1.4 · N2 n/a: the route takes no parameters and refuses nothing · N3 S-1.5 · N4 covered by AC-8 (S-8.5) · N5 S-1.9, S-1.10 · N6 S-1.6, S-1.7 · N7 S-1.8 · N8 covered by AC-2 (S-2.12)
Questions: Q-6

### AC-2: A repeat request reuses an unchanged file and never a changed one
Requirement items: R-5, R-16, R-17, R-18, R-20, R-21
Rule: When a request's backward probe passes only missing candidates or remembered failures and resolves the held identity, the route shall answer without reading any cache file; if the file it reaches differs in path, size or modification time from both the held identity and every remembered failure, or no file resolves, then the route shall answer from the newly reached file or the degraded row, never from series derived from the held identity.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-2.1 | B | a request has decoded file F; F is unchanged; an engine cycle has since rewritten the bot state's per-symphony return and value fields without changing any holdings; `shadow_history` is unchanged | a second GET | no decode; the body is byte-identical to the first |
| S-2.2 | N5 | as S-2.1 | three more GETs 60 s apart (the page poll) | no decode across the three; each body is byte-identical to the first |
| S-2.3 | N1 | F is inside the window; the ET date rolls to the next trading day and F is still inside it | GET | no decode; the replay rows are unchanged |
| S-2.4 | N1 | F is exactly `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS` trading days back | GET | no decode; the replay rows come from F |
| S-2.5 | N1 | the date rolls so F is one trading day beyond the window; no newer file | GET | F is not decoded or used; every `replay` is the degraded row; one INFO miss summary is logged in the existing wording (base logs one per held symphony) |
| S-2.6 | N1 | the autotune has written F2 for the same holdings at a newer date inside the window | GET | F2 is decoded exactly once; the replay rows come from F2 |
| S-2.7 | N1 | F is rewritten in place with a different size | GET | F is decoded once again; the replay rows come from the new content |
| S-2.8 | N1 | F is rewritten in place with the same size and a different modification time | GET | F is decoded once again; the replay rows come from the new content |
| S-2.9 | N1 | a held symphony's holdings change, so the current holdings resolve a different key and no file exists for it | GET | F is not decoded or used; every `replay` is the degraded row |
| S-2.10 | N3 | F is deleted and no other file is in the window | GET | no decode; every `replay` is the degraded row; one INFO miss summary; `synthetic_history.fetch_bars` and `generate_synthetic_history` are not called |
| S-2.11 | N6 | F is replaced in place by corrupt content (its size or modification time changed); an older valid file G is in the window | GET | one corrupt-file WARNING naming F; G is decoded once; the replay rows come from G |
| S-2.12 | N8 | the autotune is part-way through writing F2 (newer date, same holdings) | GET | the body equals base's for a corrupt F2 (replay rows from F or the degraded row) and one WARNING names F2; the next GET after the write completes decodes F2 once and answers from it (the completed file's size and modification time differ from the remembered partial one) |
| S-2.13 | N7 | as S-2.1, and one symphony has become no longer held between the two requests | a second GET | no decode; that symphony's entry is gone; every other entry is byte-identical to the first body's |
| S-2.14 | N1 | the daemon restarted after S-2.1 | GET | F is decoded exactly once (nothing derived from F survives a restart, and nothing derived is written to disk) |
| S-2.15 | N5 | as S-2.1 | two GETs start together | no decode; both bodies are byte-identical to the first |
| S-2.16 | N1 | F was decoded; the holdings then changed so F2 resolved and was decoded; the holdings then changed back, so F resolves again, unchanged | GET | F is decoded once again (the held identity is F2's, so the request is cold; warmth is not claimed across a return to an earlier file); the replay rows come from F |
| S-2.17 | N1 | F2 (newer date, same holdings) was decoded after F; F2 is then deleted, so F resolves again | GET | F is decoded once again; the replay rows come from F |
| S-2.18 | N6 | a request read corrupt F (newer) and logged its WARNING, then decoded the older valid G; F and G are unchanged | the next GET, and three 60 s polls after it | no cache file is read; no WARNING is logged; every body is byte-identical to the first request's (replay rows from G) |
| S-2.19 | N6 | as S-2.18, then F is rewritten with valid content (its size or modification time changes) | GET | F is decoded once; the replay rows come from F |
| S-2.20 | N6 | the only file in the window, F, is corrupt; a request has read it and logged its WARNING; F is unchanged | the next GET | F is not read; no WARNING; every `replay` is the degraded row, as at base |
| S-2.21 | N6 | the newest file F in the window cannot be opened (an OSError such as permission denied), and an older valid file E is in the window | GET, then F is made readable without changing its size or modification time, then GET | the first GET's replay rows come from E and it logs base's WARNING for F once; the second GET decodes F once and its replay rows come from F |
| S-2.22 | N6 | F cannot be opened (an OSError), F is unchanged, and no other file is in the window | two successive GETs | each GET attempts F once and logs base's WARNING once (once per request, never per symphony); neither GET remembers the failure; both bodies are the degraded-replay body |

Negative coverage: N1 S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.14, S-2.16, S-2.17 · N2 n/a: the route takes no parameters and refuses nothing · N3 S-2.10 · N4 covered by AC-8 (S-8.5) · N5 S-2.2, S-2.15 · N6 S-2.11, S-2.18, S-2.19, S-2.20, S-2.21, S-2.22 · N7 S-2.13 · N8 S-2.12
Questions: Q-2, Q-5, Q-6

### AC-3: The answer is byte-identical to base
Requirement items: R-1, R-6, R-15, R-19, R-20, R-21, R-22, R-24, R-32
Rule: When `GET /api/guard-alpha-preconditions` is served for a given bot state, `shadow_history` and cache directory, the route shall return the status and JSON body bytes that base returns for them, on a cold request and on a warm one.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-3.1 | B | the fixture's 12 held symphonies; the excerpt's cache file holding all 12; `shadow_history` rows for each | a cold GET, then a warm GET | both bodies are byte-equal to the base body captured for the same state |
| S-3.2 | N3 | no file in the window | GET | the body is byte-equal to base: every `replay` is the degraded row |
| S-3.3 | N3 | the file lacks one held symphony | GET | that entry's `replay` is the degraded row; the body is byte-equal to base |
| S-3.4 | N3 | the file holds a symphony whose every date has an empty tick list | GET | that entry's `replay` is the degraded row; byte-equal to base |
| S-3.5 | N1 | a symphony's replay returns are all equal and exactly representable (for example all 0.0), so the variance sum is exactly zero (`guard_preconditions.py:74-86`) | GET | that entry's `rho` and `sharpe_daily` are null and its `rho_ci` is the number 1.96 over the square root of `n_obs` (`guard_preconditions.py:98`), as at base; byte-equal to base |
| S-3.6 | N1 | the file holds a symphony that is not in the bot state | GET | no entry for it; byte-equal to base |
| S-3.7 | N3 | a held symphony has no `shadow_history` rows | GET | its `shadow` is the degraded row and its `replay` is real; byte-equal to base |
| S-3.8 | N6 | `database.load_state` raises | GET | 200 `{"symphonies": {}}`; no file is decoded; byte-equal to base |
| S-3.9 | N1 | `database.load_state` returns `{}` | GET | 200 `{"symphonies": {}}`; byte-equal to base |
| S-3.10 | N6 | the shadow series read raises for one symphony | GET | that entry's `shadow` is the degraded row; every other entry is real; byte-equal to base |
| S-3.11 | N7 | the five metadata keys and a no-longer-held symphony are in the bot state | GET | neither appears; byte-equal to base |
| S-3.12 | N7 | the market is closed and nothing is written between two GETs 60 s apart | two GETs | the two bodies are byte-identical to each other and to base |
| S-3.13 | N1 | a symphony with holdings is added to the bot state (so the key changes) and a file for the new holdings is in the window | GET | the entries follow the bot state and the new symphony's `replay` comes from that file; byte-equal to base |
| S-3.14 | N5 | the S-3.1 state, no earlier decode | two GETs start together | both bodies are byte-equal to the base body for that state |

Negative coverage: N1 S-3.5, S-3.6, S-3.9, S-3.13 · N2 n/a: the route takes no parameters and refuses nothing · N3 S-3.2, S-3.3, S-3.4, S-3.7 · N4 covered by AC-8 (S-8.5) · N5 S-3.14 · N6 S-3.8, S-3.10 · N7 S-3.11, S-3.12 · N8 covered by AC-2 (S-2.12)
Questions: none

### AC-4: Concurrent requests share one decode
Requirement items: R-14
Rule: While one request is decoding a cache file, a second request that reaches the same file identity shall wait for that decode and answer from its result instead of decoding the file again; if that decode fails, then the waiting request shall not read the file again and shall get base's answer for that file unreadable: on an error `load_cached_history` catches (`OSError`, `JSONDecodeError`, `ValueError`, `UnicodeDecodeError`, `synthetic_history.py:274`) the probe moves on to the next older candidate, with the degraded replay row when none is left; on any other exception the replay row is the degraded row, as the route gives a raising replay seam (`app.py:6610-6618`).

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-4.1 | B | no earlier decode of F | two GETs start together | F is decoded exactly once in total; both bodies are byte-equal to the single-request body of S-3.1 |
| S-4.2 | N5 | as S-4.1 | three GETs start while the first is decoding | F is decoded once in total; all bodies are byte-equal |
| S-4.3 | N6 | F is corrupt; an older valid file G is in the window | two GETs start together | both 200; F is read once in total (the waiting request does not re-read it) and one WARNING names it; both probes move on to G, which is decoded once in total; both bodies equal base's for that state (replay rows from G); neither request waits after the first finishes; a later GET does not read F while its identity is unchanged (S-2.18) |
| S-4.4 | N6 | request A's decode of valid F raises once with an error other than a decode error (a simulated transient fault); no older file | A and B start together, B waiting on A's decode | both 200; neither hangs; B does not read F; every `replay` in both bodies is the degraded row, the route's answer for a raising replay seam (`app.py:6610-6618`); the failure is not remembered (it is not an error `load_cached_history` catches), so the next GET decodes F once and answers from it. At base the same fault reaches only the symphony call that hit it; the shared decode applies that per-symphony rule to every symphony of the requests it serves (Known-Unconfirmed item 5) |
| S-4.5 | N1 | request A is decoding F; F is then rewritten with a different size | request B starts | B does not answer from A's result for the old F; B's replay rows come from the new content |
| S-4.6 | N7 | a cold preconditions GET is decoding | a concurrent `GET /api/performance?scope=aggregate&days=60d` completes | its body equals its body when run alone |
| S-4.7 | N3 | no file in the window | two GETs start together | no decode; both bodies are the degraded-replay body, byte-equal to base; no fetch is made |
| S-4.8 | N6 | request A's decode of F ends in an exception that is not an Exception subclass (a BaseException) while request B waits on it | B resumes | B's replay rows are degraded, as for any other non-decode failure; nothing is remembered; the next GET decodes F again |

Negative coverage: N1 S-4.5 · N2 n/a: the route takes no parameters and refuses nothing · N3 S-4.7 · N4 covered by AC-8 (S-8.5) · N5 S-4.1, S-4.2 · N6 S-4.3, S-4.4, S-4.8 · N7 S-4.6 · N8 covered by AC-2 (S-2.12)
Questions: Q-1

### AC-5: A measured latency bound holds on a production-sized fixture
Requirement items: R-3, R-9, R-37
Rule: When the route runs on a production-sized fixture (at least 11 held symphonies and a cache file of at least 70 MB), a warm request shall decode nothing and complete in under 0.5 s, and a cold request shall decode at most one file and complete within the time of one decode of that fixture file, measured in the same run, plus 1.0 s.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-5.1 | B | the fixture; one cold GET already served | a warm GET, timed as the median of 5 | no decode; under 0.5 s |
| S-5.2 | N1 | the fixture; no earlier decode | a cold GET, timed | exactly one decode; within one decode of the fixture file (timed alone in the same run) plus 1.0 s |
| S-5.3 | N1 | the fixture with a 147 MB cache file (the largest recent droplet file, G-9) | a warm GET, timed | no decode; under 0.5 s |
| S-5.4 | N3 | the fixture with no file in the window | a GET, timed | no decode; under 0.5 s |
| S-5.5 | N5 | the fixture, warm | 5 warm GETs back to back | no decode; each under 0.5 s |
| S-5.6 | N6 | the fixture with a corrupt newest file F and a valid older file G, no earlier read | a GET, timed | F read once and G decoded once; within one read of F plus one decode of G (each timed alone in the same run) plus 1.0 s |
| S-5.7 | N6 | as S-5.6 after its GET: G held, F's failure remembered, both unchanged (for example a failed write left F truncated, `synthetic_history.py:841-851`) | a GET, timed | no file read; under 0.5 s |

Negative coverage: N1 S-5.2, S-5.3 · N2 n/a: the route takes no parameters and refuses nothing · N3 S-5.4 · N4 n/a: a 401 is answered by the global hook before the route runs, unchanged (S-8.5) · N5 S-5.5 · N6 S-5.6, S-5.7 · N7 n/a: a timing criterion; what must not change is AC-3 and AC-8 · N8 n/a: no edit is held open; a write in progress is S-2.12
Questions: Q-3, Q-6, Q-7

### AC-6: Other requests are not held up
Requirement items: R-7, R-8, R-22, R-36, R-37
Rule: While a warm `GET /api/guard-alpha-preconditions` is in flight, a concurrent `GET /api/performance`, `/api/header-status` or `/api/candidate-alert` shall complete no more than 0.5 s later than the same request run alone.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-6.1 | B | the AC-5 fixture, warm, served by the real threaded server | `GET /api/performance?scope=aggregate&days=60d` starts together with a warm preconditions GET (median of 5) | it completes within its alone time, measured in the same run, plus 0.5 s |
| S-6.2 | N5 | as S-6.1 | `GET /api/header-status` starts together with a warm preconditions GET | it completes within its alone time plus 0.5 s |
| S-6.3 | N5 | as S-6.1 | `GET /api/candidate-alert` starts together with a warm preconditions GET | it completes within its alone time plus 0.5 s |
| S-6.4 | N1 | the fixture, cold | `GET /api/performance?scope=aggregate&days=60d` starts together with the cold preconditions GET | it completes within its alone time plus the measured time of one decode of the fixture file plus 0.5 s |
| S-6.5 | B | the fixture, warm; `/performance` opened in a browser | the page loads | the guard effect, Sharpe, Max DD and Sortino headline figures and the chart show values, not "--", within the `/api/performance` alone time plus 0.5 s after the symphony list loads; the preconditions panel shows the same rows and verdicts as at base |
| S-6.6 | N7 | `/performance` open; nothing written between polls | two 60 s polls run | no text in the headline or the preconditions panel changes |
| S-6.7 | N3 | the fixture with no file in the window | `/api/performance` starts together with a preconditions GET | it completes within its alone time plus 0.5 s |
| S-6.8 | N6 | the fixture with a corrupt newest file and a valid older file, cold | `/api/performance` starts together with the preconditions GET | it completes within its alone time plus the measured time of one read of the corrupt file and one decode of the older file plus 0.5 s |
| S-6.9 | N6 | the state of S-5.7 (G held, F's failure remembered) | `/api/performance` starts together with the preconditions GET | it completes within its alone time plus 0.5 s |
| S-6.10 | N1 | the fixture, cold | `GET /api/header-status` starts together with the cold preconditions GET | it completes within its alone time plus the measured time of one decode of the fixture file plus 0.5 s |
| S-6.11 | N1 | the fixture, cold | `GET /api/candidate-alert` starts together with the cold preconditions GET | it completes within its alone time plus the measured time of one decode of the fixture file plus 0.5 s |

Negative coverage: N1 S-6.4, S-6.10, S-6.11 · N2 n/a: the routes take no new input and refuse nothing new · N3 S-6.7 · N4 n/a: auth is the unchanged global hook (S-8.5) · N5 S-6.2, S-6.3 · N6 S-6.8, S-6.9 · N7 S-6.6 · N8 n/a: no edit is held open on these pages
Questions: Q-3, Q-6, Q-7

### AC-7: Memory stays bounded
Requirement items: R-10
Rule: While no preconditions request is in flight, the daemon shall hold none of a cache file's decoded content for this route, only per-symphony daily return lists for the held identity and the identities (path, size, modification time) of remembered failures.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-7.1 | B | a production-shaped state and file | a cold GET completes and garbage is collected | the traced memory retained against before the request is under 5% of the traced peak during the request |
| S-7.2 | N1 | three different files decoded by successive requests (F replaced twice) | the third request completes and garbage is collected | the retained memory is still under 5% of one request's traced peak |
| S-7.3 | N5 | no earlier decode | two cold GETs run together, and the first request's decode is held until the second request has reached the shared decode | one decode in total; the traced peak during the pair is at most 1.25 times the traced peak of one cold GET alone |
| S-7.4 | N3 | no file in the window | a GET completes and garbage is collected | the retained memory is under the S-7.1 bound |
| S-7.5 | N6 | the decode fails (corrupt file) | a GET completes and garbage is collected | nothing from the failed decode is retained beyond the file's identity (the S-7.1 bound holds) |
| S-7.6 | N1 | warm | 20 warm GETs complete and garbage is collected | the retained memory does not grow beyond the S-7.1 bound across the 20 |
| S-7.7 | N7 | any of the states above | a GET completes | the cache directory and the repository hold no new file (nothing derived is written to disk) |

Negative coverage: N1 S-7.2, S-7.6 · N2 n/a: the route takes no parameters and refuses nothing · N3 S-7.4 · N4 n/a: a refused request never reaches the route · N5 S-7.3 · N6 S-7.5 · N7 S-7.7 · N8 n/a: no edit is held open
Questions: Q-4

### AC-8: Contracts and protected surfaces stay unchanged
Requirement items: R-11, R-12, R-13, R-16, R-26, R-32, R-34
Rule: When the route resolves its replay samples, it shall read only the existing cache files, probe the same `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS` window, log the existing INFO miss summary wording at most once per request and the existing corrupt-file WARNING wording once per corrupt file identity, keep its auth and read-only behaviour, and leave the protected files unchanged.

| ID | Kind | Given | When | Then |
|---|---|---|---|---|
| S-8.1 | B | no file in the window; `synthetic_history.fetch_bars` and `generate_synthetic_history` instrumented | GET | neither is called; no network request leaves the process; every `replay` is the degraded row |
| S-8.2 | N3 | all 11 window candidates are missing | GET | exactly one INFO record per request with the existing "cache miss for 11 candidate date(s)" wording (`synthetic_history.py:566-574`); no WARNING |
| S-8.3 | N6 | a corrupt file in the window, not read before | GET, then two 60 s polls with the file unchanged | the first GET logs exactly one WARNING, byte-identical in wording to base's "synthetic_history: corrupt or unreadable cache file %s: %s" line naming that file (`synthetic_history.py:276-280`), and the walk continues to older dates as at base; the polls log no repeat WARNING for that unchanged file (narrowed: base logs one per held symphony on every poll) |
| S-8.4 | N1 | the current date's candidate is a valid file | GET | no log record from the probe |
| S-8.5 | N4 | auth on, no session, an XHR request | GET | 401 JSON, as at base |
| S-8.6 | N2 | any state | `POST /api/guard-alpha-preconditions` | 405, as at base (the route is GET only) |
| S-8.7 | N7 | the slice head against aafe476d | diff | `alpha_bot_execution.py`, `math_engine.py`, `guard_preconditions.py`, `static/performance.js`, `templates/performance.html` and `migrations/` are unchanged; `generate_synthetic_history`, `database.load_state` and `analytics.get_shadow_current_return_daily_series` are unchanged |
| S-8.8 | N7 | an existing valid file for the autotune's state | `generate_synthetic_history` is called | it returns the decoded dict and makes no fetch, as at base |
| S-8.9 | N7 | the route's source | read | no SQL write and no `LIVE_EXECUTION` reference (the existing read-only tests stay green) |
| S-8.10 | N5 | all window candidates missing | two GETs run together | at most one INFO miss summary per request |
| S-8.11 | N3 | a request has decoded F; F is then deleted and no other file is in the window; fetches instrumented as in S-8.1 | the next 60 s poll GET | `synthetic_history.fetch_bars` and `generate_synthetic_history` are not called; every `replay` is the degraded row |
| S-8.12 | N5 | no file in the window; fetches instrumented as in S-8.1 | two GETs start together | neither function is called by either request |
| S-8.13 | N1 | a request has decoded F; the date then rolls so every window candidate is missing | the next 60 s poll GET | exactly one INFO record with the existing miss wording; no WARNING |

Negative coverage: N1 S-8.4, S-8.13 · N2 S-8.6 · N3 S-8.2, S-8.11 · N4 S-8.5 · N5 S-8.10, S-8.12 · N6 S-8.3 · N7 S-8.7, S-8.8, S-8.9 · N8 covered by AC-2 (S-2.12)
Questions: Q-6

## Rule-by-Entry-Point Matrix
| Rule | Page load (first GET after the symphony list) | 60 s poll (repeat GET) | Concurrent GETs (two tabs, or a load overlapping a poll) | Autotune run (`generate_synthetic_history` on the same file) |
|---|---|---|---|---|
| AC-1 at most one decode per request | S-1.1 | S-1.9 | S-1.10 | n/a: the autotune decodes its own file once per run, unchanged (AC-8) |
| AC-2 reuse an unchanged file, never a changed one | S-2.14 | S-2.1, S-2.6 | S-2.15 | n/a: the autotune reads its file per run, unchanged (AC-8) |
| AC-3 byte-identical answer | S-3.1 | S-3.12 | S-3.14 | n/a: the autotune returns no route answer |
| AC-4 concurrent requests share one decode | n/a: a lone page load has no concurrent request | n/a: a lone poll has no concurrent request | S-4.1 | n/a: an autotune is not a route request: the scheduled one runs in the engine subprocess and the `POST /api/force_eod` one on a daemon thread (`app.py:8570-8622`), and neither waits on or shares a route decode; its write in progress is S-2.12 and its memory is Known-Unconfirmed item 12 |
| AC-8 cache-hit-only | S-8.1 | S-8.11 | S-8.12 | n/a: the autotune fetches on a miss by design (`synthetic_history.py:664-712`; NB-4), unchanged (S-8.8) |
| AC-8 log wording (INFO once per request, WARNING once per corrupt identity) | S-8.2 | S-8.13 | S-8.10 | n/a: its own lines are unchanged (S-8.8) |

## Not Building
Non-goals:
- NB-1: No change to the preconditions statistics, verdicts, thresholds (`N_MIN_OBS`) or the shadow sample (R-27; approved [PM-ASSUMED]).
- NB-2: No change to the 60 s poll in `static/performance.js` (`static/performance.js:1093-1097`) (R-28; approved [PM-ASSUMED]).
- NB-3: No speed-up of `/api/performance` itself (R-29; approved [PM-ASSUMED]).
- NB-4: No change to the cache file format or its writer `generate_synthetic_history`, including its fetch on a miss (R-30; approved [PM-ASSUMED]).
- NB-5: No change to the autotuner's walk-forward replay (R-31; approved [PM-ASSUMED]).
- NB-6: No wire, schema, migration or config change; no new environment key (R-24; approved [PM-ASSUMED]).
- NB-7: No workflow file is created for `/performance` (R-23; approved [PM-ASSUMED]).
- NB-8: The exhaustive UI/UX review and the droplet deploy are the PM's deploy gate, not criteria of this plan (narrows R-1; approved [PM-ASSUMED]).

Behavior that stays exactly as it is:
- NB-9: The route's JSON for the same state and cache file, its cache-hit-only read, the backward probe up to `AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS`, the INFO miss summary and corrupt-file WARNING wording, and the degraded-row copy (tested by AC-3 and AC-8; approved [PM-ASSUMED]).
- NB-10: The structural symphony filter (`app.py:6499-6515`) and the shared no-longer-held rule (`app.py:6604`) (tested by S-1.3, S-1.8, S-3.11; approved [PM-ASSUMED]).
- NB-11: The Performance panel's rendering, copy, display names and cold-replay note (`static/performance.js:874-924`) (approved [PM-ASSUMED]).

Deferred:
- NB-12: GIL contention from any other slow route (R-33; approved [PM-ASSUMED]). The source names "the NB-14 backlog"; no entry at the ref names it (Known-Unconfirmed item 9), so the follow-up line is recorded at ship (Q-8).
- NB-17: The replay sample is unavailable on weekdays because the cache key hashes the drifting `allocation` floats (Trigger fact 10); the key derivation and that weekday unavailability are not changed here, a pre-existing gap deferred to the NB-14 backlog alongside NB-12 (Q-8) (excludes R-35, which rested on the corrected market-hours clause; approved [PM-ASSUMED]).

Protected surfaces this consumes but does not change:
- NB-13: `alpha_bot_execution.py` and `math_engine.py`, the 1-minute engine (R-13; approved [PM-ASSUMED]; S-8.7).
- NB-14: The synthetic_history cache files and `generate_synthetic_history`, `database.load_state`, `guard_preconditions.py`, `analytics.get_shadow_current_return_daily_series`, `static/performance.js` and `templates/performance.html` (R-34; approved [PM-ASSUMED]; S-8.7).
- NB-15: Dashboard sessions: no logout generation bump, `SECRET_KEY` stable, migrations 040/041 and the session lifetime untouched, so no deploy of this change forces a re-login (R-2; approved [PM-ASSUMED]).
- NB-16: No other repository changes and nothing must land first (R-25; approved [PM-ASSUMED]).

## Defect Classes
| DC-n | Class (what goes wrong) | Trigger (B or N1-N8) | Sibling search (tool, query, scope) | Scenarios | Raised in review as |
|---|---|---|---|---|---|
| DC-1 | Dependency change answered with stale data: series derived from an earlier file served after the file changed, aged out of the window or stopped resolving | N1 | grep: every read of the held series and every value it is compared against; `st_mtime`, `getmtime`, `st_size`, `os.stat` in `app.py`, `autotuner.py`, `synthetic_history.py` | S-2.5, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.16, S-2.19, S-4.5 | - |
| DC-2 | A second cache-key derivation drifting from `synthetic_history._resolve_history_cache_key`, so the route resolves a different file than the reader would | N1 | grep: `synthetic_history_v4_`, `hashlib.md5`, `holdings_hash`, `current_holdings` in every `*.py` outside `tests/` | S-2.6, S-2.9, S-3.13 | - |
| DC-3 | Check and decode outside one lock: two concurrent requests both decode, or two copies of the parsed history live at once | N5 | grep: `threading.Lock`, `get_or_build`, `_building` in `app.py`, `autotuner.py`, `synthetic_history.py` | S-4.1, S-4.2, S-7.3 | - |
| DC-4 | Failure handling drifts: a waiting request hangs or gets anything other than base's unreadable-file answer; a corrupt file's remembered failure outlives a change of its identity; or a transient non-decode error is remembered | N6 | read: every wait on another request's decode, every write and read of the remembered failures, and the error classification they key on (`synthetic_history.py:274`) | S-2.18, S-2.19, S-2.20, S-4.3, S-4.4 | - |
| DC-5 | Absent or not-computed value shown as a default: a reused series or zeros where base gives the degraded row | N3 | grep: `_degraded_row`, `INSUFFICIENT_DATA` in `app.py`; every return of None from the replay path | S-1.4, S-1.5, S-2.10, S-3.2, S-3.3, S-3.4 | - |
| DC-6 | Narrow assertion: the decode count or byte equality tested for one state only, or on a fixture whose series are all degenerate | N1 | read: each new test against the state families S-1.1 to S-1.10 and S-3.1 to S-3.14, and the fixture's rows (at least one real stats row per test that claims one) | S-1.1 to S-1.10, S-3.1 to S-3.14 | - |
| DC-7 | Contract change not carried to every consumer: existing tests patch `autotuner.build_if_held_replay_series` per symphony and pass vacuously if the route stops calling it that way; the sites at the ref are `tests/app/test_guard_alpha_preconditions_route.py:82`, `:150`, `:297`, `:351`, `:543`, `:589`, `:637`, `:665`, `tests/app/test_preconditions_leave_out_no_longer_held.py:27` and `tests/ui/test_performance_page_data_truth.py:654` (signature `lambda sid, *a, **k`) | B | grep: `build_if_held_replay_series`, `get_cached_synthetic_history_only`, `load_cached_history` in `tests/` | S-3.1, S-8.1 | - |
| DC-8 | Data that must stay unchanged altered: the INFO or WARNING wording or level drifts, the INFO line repeats within a request, or the first WARNING for a corrupt identity is lost | N7 | grep: `corrupt or unreadable`, `cache miss for` in `synthetic_history.py` and `tests/` | S-8.2, S-8.3, S-8.4, S-8.10, S-2.18 | - |
| DC-9 | Decoded content retained between requests by a module global, closure, log record or exception traceback | N6 | grep: `global `, `_CACHE`, `functools`, `lru_cache` in `app.py`, `autotuner.py`, `synthetic_history.py` | S-7.1, S-7.2, S-7.5, S-7.6 | - |
| DC-10 | Planned example values not encoded in a test | B | read: the Worked Example against the tests of S-1.1, S-2.1 and S-2.10 | S-1.1, S-2.1, S-2.10 | - |

## Worked Example
Input (S-1.1, S-2.1): the date is Sunday 2026-11-15 ET; the bot state holds three held symphonies with invented ids `Kp4WqZr7TnB2xLm9Hc3D`, `Vt8sNe1QyR5uGa6Jw0Lf` and `Hb3CdX9mPz2kTq7Ws4Ye`, one no-longer-held symphony and the five metadata keys; the `cache` directory holds one 71 MB file dated 2026-11-13 for the current holdings. The first `GET /api/guard-alpha-preconditions` finds no file for 2026-11-15, finds the 2026-11-13 file one trading day back, decodes it once and returns 200 with three entries, each `{"replay": {...6 fields, "sample_source": "if_held_replay"}, "shadow": {...6 fields, "sample_source": "shadow_history"}}`, byte-equal to base. The poll 60 s later, after an engine cycle rewrote the returns but not the holdings, resolves the same file with the same size and modification time, decodes nothing and returns the same bytes.

Failure case (S-2.10, S-8.2): the file is deleted and no other file is in the window. The next GET decodes nothing, makes no fetch, logs one INFO line in the existing "cache miss for 11 candidate date(s)" wording, and returns three entries whose `replay` is `{"rho": null, "rho_ci": null, "sharpe_daily": null, "n_obs": 0, "verdict": "INSUFFICIENT_DATA", "sample_source": "if_held_replay"}`, as base does for that state.

## Fail-Closed Behaviour
none: the route stays GET only with no write path and takes no parameters; it refuses nothing new, and every unavailable input degrades to the existing degraded row as at base (`app.py:6587-6598`), tested by S-1.4, S-1.5, S-2.9, S-2.10 and S-3.8.

## Authorities Touched
| Value | Authority used | What it replaced | What proves no second source |
|---|---|---|---|
| Which cache file represents the replay sample for the current state and date | `synthetic_history._resolve_history_cache_key` and the reader's backward probe (`synthetic_history.py:487-536`, `synthetic_history.py:577-652`) | nothing | S-2.6, S-2.9, S-3.13 and DC-2: the route resolves the same file the reader resolves |
| A symphony's replay daily returns | the decoded cache file, through `autotuner.collect_if_held_daily_returns` (`autotuner.py:1539-1553`) | nothing; a held derived copy is added | S-2.5 to S-2.12, S-2.16, S-2.17, S-2.19 and DC-1: the held copy is used only while the probe reaches the held identity (same path, size and modification time) |

## Contract Changes and Consumers
none: the route's status and body are byte-identical to base (AC-3, G-18), so its one consumer, `static/performance.js` (`static/performance.js:914-924`), sees no change.

## Sibling Repos and Landing Order
none: one repository, PlanetStopper; nothing must land first (G-19).

## Migrations Required
none: no schema change and no new table (G-18); nothing derived is stored (S-7.7).

## Configuration and Enablement
none: no new or changed environment key or setting (G-18); the change ships enabled with no flag.

## Breaking Changes
none: the body is byte-identical (AC-3) and the log wording is unchanged (S-8.2, S-8.3). The operator-visible difference is fewer duplicate log lines: one reader pass's INFO line per request instead of one per held symphony, and one corrupt-file WARNING per corrupt file identity instead of one per held symphony on every poll (Q-6).

## Security Considerations
- Input validation and injection: the route takes no parameters (unchanged); no new input.
- Authorization and access control: the global auth hook is unchanged (S-8.5); the route stays GET only (S-8.6).
- Data exposure: the body is byte-identical (AC-3); the log lines keep their wording (S-8.2, S-8.3); nothing new is written to disk (S-7.7).
- Client-side rendering: no client change (NB-11, NB-14).
- Abuse or rate limits: a burst of requests decodes the file at most once (AC-4) and holds at most one parsed copy (S-7.3), and a corrupt file is read once per identity (S-2.18), so a reload storm can no longer multiply decode time, memory or log volume.

Sensitive is none (G-20): no security, money or sensitive-data surface; the route reaches no order path, no Composer write and no `LIVE_EXECUTION` (S-8.9).

## Prerequisites
- The captured fixture `tests/fixtures/preconditions_replay/cache_excerpt_2026-11-13.json` (present in the worktree, committed with this plan); every test fixture in Definition of Done derives from it.
- A checkout of aafe476d, used once to produce the golden bodies (Definition of Done).

## Design-System Mapping
none: no visible element, template or client script changes (NB-11, NB-14); the page's rows and copy are the same because the body is byte-identical (AC-3).

## Definition of Done (Verification)
| AC | Scenarios | Test level | Real-entrypoint test |
|---|---|---|---|
| AC-1 | S-1.1, S-1.2, S-1.3, S-1.4, S-1.5, S-1.6, S-1.7, S-1.8, S-1.9, S-1.10 | route test through the Flask test client, real files in a temporary `cache` directory, decodes counted | `GET /api/guard-alpha-preconditions` |
| AC-2 | S-2.1, S-2.2, S-2.3, S-2.4, S-2.5, S-2.6, S-2.7, S-2.8, S-2.9, S-2.10, S-2.11, S-2.12, S-2.13, S-2.14, S-2.15, S-2.16, S-2.17, S-2.18, S-2.19, S-2.20, S-2.21, S-2.22 | route test, files rewritten, deleted, aged and replaced between requests | `GET /api/guard-alpha-preconditions` |
| AC-3 | S-3.1, S-3.2, S-3.3, S-3.4, S-3.5, S-3.6, S-3.7, S-3.8, S-3.9, S-3.10, S-3.11, S-3.12, S-3.13, S-3.14 | route test comparing the head's bytes with the golden bodies produced by base for each state | `GET /api/guard-alpha-preconditions` |
| AC-4 | S-4.1, S-4.2, S-4.3, S-4.4, S-4.5, S-4.6, S-4.7, S-4.8 | route test with concurrent requests from threads | `GET /api/guard-alpha-preconditions` on the threaded server |
| AC-5 | S-5.1, S-5.2, S-5.3, S-5.4, S-5.5, S-5.6, S-5.7 | timing test on the production-sized fixture, in the default suite and CI: decode counts asserted first, wall-clock bounds second, one decode of the fixture file timed alone in the same run | the real threaded server on localhost |
| AC-6 | S-6.1, S-6.2, S-6.3, S-6.4, S-6.5, S-6.6, S-6.7, S-6.8, S-6.9, S-6.10, S-6.11 | timing test in the default suite and CI for S-6.1, S-6.2, S-6.3, S-6.4, S-6.7, S-6.8, S-6.9, S-6.10, S-6.11; browser evidence for S-6.5, S-6.6 | the real threaded server; `/performance` in a browser |
| AC-7 | S-7.1, S-7.2, S-7.3, S-7.4, S-7.5, S-7.6, S-7.7 | route test with `tracemalloc` | `GET /api/guard-alpha-preconditions` |
| AC-8 | S-8.1, S-8.2, S-8.3, S-8.4, S-8.5, S-8.6, S-8.7, S-8.8, S-8.9, S-8.10, S-8.11, S-8.12, S-8.13 | route tests, the existing preconditions, autotuner and synthetic_history suites, and a diff check | `GET /api/guard-alpha-preconditions` |

Gates: `ruff format --check` and `ruff check`; a scoped pytest run (never `-n auto`, at most 4 workers) over `tests/app/test_guard_alpha_preconditions_route.py`, `tests/app/test_preconditions_leave_out_no_longer_held.py`, `tests/autotuner/test_build_if_held_replay_series.py`, `tests/autotuner/test_collect_if_held_daily_returns.py`, `tests/synthetic_history/`, `tests/ui/test_performance_page_data_truth.py` and the new tests, with the timing tests' measured numbers recorded in the slice's review folder; CI on the exact SHA; the PM's live functional check after the review, first on a local server against a copy of the droplet's database and newest cache file (cold and warm preconditions timings, a concurrent `/api/performance`, the rendered `/performance`), then the droplet timing check once the deploy gate passes (G-1, G-2).

Fixture provenance: one real record per case.
- Captured source: `tests/fixtures/preconditions_replay/cache_excerpt_2026-11-13.json`, captured by the PM read-only from the droplet on 2026-11-15T17:51:39Z. Its source is the droplet's cache file for 2026-11-13 written by `generate_synthetic_history` in the autotune run (71,093,029 bytes; 12 symphonies with 121, 188 or 250 dates each; 1 to 456 ticks per date). It holds `bot_state_current_holdings` for all 12 symphonies, whose md5 equals the source file name's hash (recorded in its `provenance` block, so the holdings and the file are proven a pair), `bot_state_names`, and `history`: the last 60 dates per symphony with the last 2 ticks per date (the route reads only `ticks[-1]["return"]`).
- Default-suite cache files (AC-1 to AC-4, AC-7, AC-8): the bot state is built from `bot_state_current_holdings` and `bot_state_names`; `history` is written as a cache file whose name comes from running the real key derivation (`synthetic_history._resolve_history_cache_key`) on that bot state, never a hand-typed hash. Its 60 real dates per symphony are at or above `N_MIN_OBS`, so the replay rows run real statistics. Case files are derived from it by stated edits only: a symphony removed from `history` (S-3.3), a symphony's tick lists emptied (S-3.4), a symphony's last-tick returns set to 0.0 (S-3.5), a symphony removed from the bot state (S-3.6), a newer or older copy dated by the real derivation (S-2.4 to S-2.6, S-2.16, S-2.17).
- Production-sized cache file (AC-5, AC-6): generated at test time, never committed, from the excerpt's real tick records replicated to the recorded source shape (12 symphonies, up to 250 dates, tick counts within 1 to 456 per date) until the file reaches at least 70 MB (147 MB for S-5.3); its provenance is the capture above plus that recorded shape.
- `shadow_history`: rows in the table's real schema for the same ids, written from a seeded sequence with the provenance stated, with some symphonies at or above `N_MIN_OBS` days and some below, so both real and insufficient shadow verdicts appear.
- Corrupt case: a truncated copy of a generated file, the state the in-place writer leaves mid-write or after a failed write (`synthetic_history.py:841-851`).
- Base producer (PM ruling): the expected bodies are golden JSON produced by running the unchanged aafe476d route on each test state, with the ET clock frozen through the existing seam `frozen_datetime_class` (`tests/app/_refresh_timing_state_world.py:119-129`, patched into `app.datetime` at `tests/app/_refresh_timing_state_world.py:366`). That seam alone pins only `app`'s clock, so the same instant is patched into every module that reads the clock on this path: `app.datetime` (the no-longer-held `now`, `app.py:6604`) and `autotuner.datetime` (the probe's start date, `autotuner.py:1601`) at base, and at the head whichever module the head design reads the date in, which the architect names. The golden capture and the head comparison use the identical instant. The goldens are committed beside the tests, and the head's bytes are compared with them. No timing runs base: the cold bound is stated against one decode of the fixture file, timed alone in the same run.
- Tests assert decode counts first, byte equality with the goldens, and relations between timings, never literals copied from a run.

## Success Measures
- On the droplet, measured out of hours (a weekend, or after the Friday autotune, when a file resolves, Trigger fact 10): a warm `GET /api/guard-alpha-preconditions` under 0.5 s and a cold one within one decode of the newest file plus 1.0 s (against about 22 s at base), read from the daemon's per-request `timing_log` lines and the browser's network panel.
- On the droplet, out of hours, `/api/performance?scope=aggregate&days=60d` during a warm preconditions request within 0.5 s of its alone time (1.16 to 1.30 s, G-12), and the Performance headline never shows "--" past that time.
- `/api/header-status` answers in under 1 s while `/performance` is open (refresh-timing 04 AC-1).

## Known-Unconfirmed and Standing Risk
1. removed: the PM captured the fixture (`tests/fixtures/preconditions_replay/cache_excerpt_2026-11-13.json`, Definition of Done).
2. The droplet's per-decode time (about 2 s for 71 MB) is inferred from 22 s over 11 decodes, not measured alone; the cold bound is stated against one decode timed on the same machine, so it does not depend on it. The 1.0 s and 0.5 s margins are chosen generous enough for a shared CI runner (PM ruling); a CI runner slower than that would show as a timing failure with the decode counts still green.
3. A file rewritten in place with the same size within the filesystem's modification-time resolution would read as unchanged (Q-2 accepts this); the writer is non-atomic (`synthetic_history.py:841-843`), and a request during the write is S-2.12.
4. The concurrency claims rest on the threaded server (`app.py:13316`, Flask 3.1); a different server would change S-6.1 to S-6.4 but not the decode counts.
5. Base reads the bot state once in the route and once per symphony (`app.py:6557`, `autotuner.py:1589`). Byte equality (AC-3) is stated for a state that does not change or fail during a request; when an engine write lands, or a later state read fails, mid-request, base's rows can each reflect a different read, and this plan does not assert equality for that interleaving. Likewise a transient non-decode fault during the one shared decode degrades every symphony's replay row in the requests it serves (S-4.4, PM ruling), where base would degrade only the symphony call that hit it; byte equality with base is asserted for states, not for injected transient faults.
6. removed: the existing test patch sites are settled by the code and listed under DC-7.
7. Recent droplet files reach 147 MB (G-9); the cold bound scales with file size through its one-decode term, and the warm bound is checked at that size (S-5.3). Building a 70 to 147 MB fixture in CI costs runner time and disk; a CI limit would surface as a setup failure, not a false pass.
8. removed: the scheduler-thread claim rested on the source's market-hours clause, which the PM's ruling corrects (Trigger fact 11, R-35 removed).
9. The source tracks the deferred GIL contention in "the NB-14 backlog"; at the ref no NB-14 entry or `feature-plans/BACKLOG.md` line names GIL contention (the nearest, `feature-plans/aligned-tables-and-quiet-copy.md` NB-14, lists other items), so the follow-up has no tracked home yet (Q-8).
10. The decode count observable presumes a test can count reads of a cache file's content; the seam is the architect's choice.
11. removed: settled from code by the PM's ruling (Trigger fact 10): no file resolves during market hours, and the slow path runs from the Friday autotune until Monday's first cycle.
12. A `POST /api/force_eod` autotune on a daemon thread (`app.py:8570-8622`) holds its own decoded history in the daemon's memory; with a cold preconditions decode at the same moment, two parsed copies can live at once, as at base. AC-7 bounds only this route's holding.
13. A truncated file left by a failed write (`synthetic_history.py:841-851`) stays in front of the held identity until replaced; its failure is remembered under its identity, so it is read once, its WARNING is logged once and later polls are warm (S-2.18, S-5.7, S-6.9). An operator watching the log for that WARNING sees it once per identity, not every minute.

## Open Questions
| Q-n | Question | Blocks (AC ids or "plan") | Proposed default | Answer |
|---|---|---|---|---|
| Q-1 | May two concurrent requests both parse the file? (G-25) | AC-4 | No. Concurrent requests for the same file share one decode; a second request waits for the first instead of parsing again (G-25, G-26) | default approved [PM-ASSUMED] |
| Q-2 | What identifies an unchanged cache file? (G-27) | AC-2 | The resolved file path plus its size and modification time, re-checked on every request with no time-based expiry (G-27) | default approved [PM-ASSUMED] |
| Q-3 | What is the latency bound? (G-28 to G-30) | AC-5, AC-6 | Decode counts are the primary assertion: cold at most 1 decode per request, warm 0. Wall-clock bounds are secondary and generous enough for CI, on a fixture of at least 11 symphonies and a file of at least 70 MB: a warm request under 0.5 s; a cold request within one decode of the fixture file plus 1.0 s; a concurrent `/api/performance`, `/api/header-status` or `/api/candidate-alert` during a warm request within 0.5 s of its alone time. The source's "1/5 of the current per-symphony cost" means one fifth of one decode (about 0.4 s on the droplet), which no cold path that decodes the whole file once at today's decode speed can meet; the default bound is looser and R-9 is marked narrowed (PM ruling). Choosing the literal bound would require a faster decode or no whole-file decode, and would change the approach | default approved [PM-ASSUMED] |
| Q-4 | What may be kept in memory? (G-31) | AC-7 | Between requests only the derived per-symphony daily return lists for one file identity, plus the identities (no content) of remembered corrupt files, never the parsed history (G-31) | default approved [PM-ASSUMED]; AC-7 is measured on a production-shaped 6.3 MB generated file, within "production-shaped" (approach raised item b) |
| Q-5 | What does "unchanged state" mean for the no-re-parse rule (G-7)? | AC-2 | The state matters only through which file the backward probe resolves (holdings and date); a write that changes returns or values without changing holdings still reuses the held identity. During market hours the holdings' allocations drift, so no file resolves and nothing is decoded, at base and after alike (Trigger fact 10) | default approved [PM-ASSUMED] |
| Q-6 | How many probe log lines does a request emit, and does a corrupt candidate get read again? | AC-1, AC-2, AC-5, AC-6, AC-8 | At most one INFO miss summary per request, in the existing wording, instead of one per held symphony. A corrupt or unreadable file is not re-decoded on every poll: its failure is remembered under its file identity until that identity changes, and its WARNING is logged once, on the first failed read, byte-identical in wording to base's; repeat WARNING lines for the same unchanged file stop, where base logs one per held symphony on every poll, so R-12, R-16 and R-32 are narrowed (PM ruling). A transient error that base does not classify as corrupt or unreadable is not remembered (S-4.4) | default approved [PM-ASSUMED]; the S-4.4 narrowing is accepted: a shared decode's non-decode fault degrades every replay row of the requests it serves and is not remembered, so the next request retries (Known-Unconfirmed item 5) |
| Q-7 | Are the wall-clock bounds asserted in CI? | AC-5, AC-6 | Yes, secondary to the decode counts: they run in the default suite and CI with bounds generous enough for a shared runner (PM ruling), with the measured numbers recorded; the PM's live checks add the droplet numbers | default approved [PM-ASSUMED]; S-6.5 and S-6.6 are carried by a browser-marked test the PM runs at the merge gate plus the PM's live check, with no capturer (approach raised item a) |
| Q-8 | Where are the deferred items NB-12 (GIL contention from other routes) and NB-17 (weekday replay unavailability) tracked? | plan | The slice's doc-gen adds one `feature-plans/BACKLOG.md` line for each at ship, since no NB-14 entry at the ref names either | default approved [PM-ASSUMED]: doc-gen adds the `feature-plans/BACKLOG.md` lines for NB-12 (GIL contention from other routes) and NB-17 (weekday replay-sample unavailability, the cache key hashes drifting allocations) |

## Decisions
| ID | Decision | Rationale |
|---|---|---|
| D-1 | The source's clause that the route competes with the minute scheduler during market hours is corrected, and no criterion rests on it (PM ruling) | The key hashes drifting allocations, so no file resolves in session (Trigger fact 10); the saved source stays verbatim |
| D-2 | Warm means the probe reaches the held identity past only missing candidates or remembered failures; one identity's results are held, and no warmth is claimed across A, then B, then A (PM ruling) | A holdings change resolves a different file or none, so a single-entry store is enough |
| D-3 | A corrupt or unreadable file's failure is remembered under its identity; its WARNING is logged once and repeat lines stop (PM ruling) (narrowed by D-6: decode errors only) | Re-decoding a truncated 70 MB file on every poll would keep the contention; the first WARNING keeps base's wording |
| D-4 | A request waiting on a failed decode gets base's unreadable-file answer (PM ruling) | The answer stays the one base gives for that file |
| D-5 | The expected bodies are golden JSON from the unchanged aafe476d route with the ET date frozen; decode counts are the primary latency assertion; wall-clock bounds are secondary, generous for CI, and the cold bound is stated against one decode (PM ruling) | Counts are deterministic; the goldens fix base without running it in every test |
| D-6 | A read failure is remembered under the file identity only when it is a decode error: the file was opened and read, but its content is not valid JSON or not decodable text (JSONDecodeError, ValueError, UnicodeDecodeError). An OSError while opening or reading (permission denied, too many open files, I/O error) is not remembered. That request gets base's answer (the walk moves to the next older candidate), and the next request reads the file again and logs base's WARNING again, once per request. Where D-3 conflicts, D-6 supersedes it. | PM ruling, review round 1 (code-reviewer decision item). synthetic_history.py:274 catches OSError together with decode errors; chmod changes neither size nor mtime, so a remembered permission error would hide the newest valid file until the weekly rewrite; Defect Class DC-4 forbids remembering a transient error. |

## Docs to Update
- `docs/generated/`: one file per touched module (among `app.md`, `autotuner.md`, `synthetic_history.md`), and `docs/generated/INDEX.md`.
- `README.md` where it describes the Performance page's preconditions panel or the replay cache.
- An appended `DECISIONS.md` entry, minted at ship by the doc-writer.
- A drafted `.claude/CLAUDE.md` Key Files row change for the touched modules, for PM approval.

## Follow-Ups
- NB-12: GIL contention from any other slow route; tracked by the `feature-plans/BACKLOG.md` line Q-8 adds.
- NB-17: the replay sample's weekday unavailability (the key hashes drifting allocations); tracked by the `feature-plans/BACKLOG.md` line Q-8 adds.
