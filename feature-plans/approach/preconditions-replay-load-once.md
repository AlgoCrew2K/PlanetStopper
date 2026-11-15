# Approach: The preconditions route reads its replay cache file once
Spec: feature-plans/preconditions-replay-load-once.md
Ref: PlanetStopper@b137694d9f8f06bce5758077a94695798ba57581

## Architecture

Every file:line below is at PlanetStopper@b137694d. That commit adds only the plan, its source, its audit and the captured fixture to the plan's base aafe476d (`git diff --stat aafe476d b137694d` lists four files under `feature-plans/` and `tests/fixtures/preconditions_replay/`), so every production line cited here is also the base's line.

### What is slow today

`guard_alpha_preconditions` (`app.py:6517-6652`) reads the bot state once (`app.py:6556-6560`) and, for each reported symphony (`app.py:6606-6608`), calls `autotuner.build_if_held_replay_series(sym_id)` (`app.py:6611`). Each call reads the bot state again (`autotuner.py:1589`), reads the ET date (`autotuner.py:1601`) and runs the whole backward probe `synthetic_history.get_cached_synthetic_history_only` (`autotuner.py:1603`, `synthetic_history.py:577-652`), which decodes the one portfolio-wide file with `load_cached_history` (`synthetic_history.py:640`, `synthetic_history.py:264-281`). Eleven symphonies make eleven decodes and log eleven INFO miss summaries per request.

### The design in five parts

1. **One probe per request, through a request scope in `autotuner`.** A new context manager `autotuner.if_held_replay_request(bot_state)` sets a `contextvars.ContextVar` holding one `_IfHeldReplayRequest` for the block. The route wraps its existing per-symphony loop (`app.py:6606-6650`) in `with autotuner.if_held_replay_request(bot_state_dict):` and keeps calling `autotuner.build_if_held_replay_series(sym_id)` exactly as now (`app.py:6611`). Inside a scope, `build_if_held_replay_series` reads nothing itself: it asks the scope's request object, which resolves the replay sample lazily on the first symphony that asks and at most once, then answers every later symphony from that resolution. Outside a scope (the accessor tests, any future caller) it builds a one-off request from `database.load_state()` as today. Entering the scope does no I/O, so a request that reports no symphony probes nothing (S-1.3), as at base.
   - Adopted precedent: `analytics.request_reads` (`analytics.py:637-655`), a `contextlib.contextmanager` that sets a `ContextVar` memo for one request's reads and resets it in `finally`.
   - Why here and not in the route: the per-symphony seam `autotuner.build_if_held_replay_series(sid)` keeps its signature and its call site, so the ten existing test patch sites that replace it per symphony (DC-7: `tests/app/test_guard_alpha_preconditions_route.py:82`, `:150`, `:297`, `:351`, `:543`, `:589`, `:637`, `:665`, `tests/app/test_preconditions_leave_out_no_longer_held.py:27`, `tests/ui/test_performance_page_data_truth.py:654`; verified complete by `git grep build_if_held_replay_series HEAD -- tests`) keep driving the route's replay rows instead of passing vacuously, and the accessor-level tests (`tests/autotuner/test_build_if_held_replay_series.py:37-58`, `:62-131`) still exercise the same request object and store the route uses. A route-side lookup table would have left all ten patch sites inert.
   - Why not per symphony with a memo only: a per-symphony probe logs one INFO line per symphony; AC-8 (S-8.2, S-8.10, S-8.13) needs exactly one per request, which needs one probe per request.
2. **An injectable per-file loader in the probe, so the probe is not copied.** `synthetic_history.get_cached_synthetic_history_only` (`synthetic_history.py:577`) gains one keyword-only parameter, `load: Callable[[str], object] | None = None`. At `synthetic_history.py:640` the call becomes `(load if load is not None else load_cached_history)(cache_file)`, resolving `load_cached_history` from the module at call time so existing monkeypatches of it still take effect. The function returns whatever that loader returned for the first candidate it did not return None for. With no `load` argument every behaviour is byte-unchanged: the key derivation (`synthetic_history.py:486-536`, unchanged, so DC-2 cannot arise), the window (`synthetic_history.py:545`, `:626`), the existence check (`:630-634`), the miss summary (`:548-574`, `:642`, `:651`) and `generate_synthetic_history` (`synthetic_history.py:655`, its own `load_cached_history` call at `:666` untouched). The docstring's "Sole consumer" paragraph (`synthetic_history.py:619-621`) gains one sentence naming the `load` parameter.
3. **A single-entry store of derived series, keyed by file identity, in `autotuner`.** A module-level `_IF_HELD_REPLAY_STORE = _IfHeldReplayStore(...)` supplies the request's `load` callable. For a candidate file it:
   - computes the identity `(os.path.abspath(cache_file), st_size, st_mtime_ns)` with `os.stat`. Adopted precedent: `app._post_mortem_signature` (`app.py:1246-1251`, `st_mtime_ns` and size). The absolute path matters because the cache directory is cwd-relative (`synthetic_history.py:523`, `:532-534`): two test directories, or a changed working directory, never share an identity.
   - returns the held derived series when the identity is the held identity (no read);
   - returns None when the path's remembered failure carries the same size and modification time (no read, no WARNING), so the probe moves to the next older candidate exactly as base does after a failed decode;
   - otherwise decodes through `synthetic_history.load_cached_history(cache_file)`, passing the same relative `cache_file` string base passes, so the WARNING text (`synthetic_history.py:276-280`) names the file in base's bytes;
   - on a dict result, derives `{symphony_id: tuple(collect_if_held_daily_returns(history, symphony_id))}` for every key (reusing `autotuner.py:1539-1553`; a key whose derivation raises maps to None, the per-symphony answer base gives when the seam raises, `app.py:6612-6618`), deletes the parsed history before returning, and holds the result as the one held identity, replacing any earlier one (D-2: no warmth across A, B, A);
   - on a non-dict decoded value, derives `{}`, which stops the probe at that candidate and degrades every symphony, base's answer for such a file (`autotuner.py:1604-1605`);
   - on None from `load_cached_history` (a caught decode error, `synthetic_history.py:274`, or a file holding JSON `null`), remembers the failure by identity (D-3);
   - re-stats after the decode and stores nothing (neither held nor remembered) when the identity moved during the read, because the non-atomic writer (`synthetic_history.py:841-851`) can change the file under a reader;
   - when `os.stat` itself fails (the file vanished after the probe's existence check), calls `load_cached_history` as base does and stores nothing.

   Bounds: one held identity with its derived lists (12 symphonies by 250 floats, about 100 KB); remembered failures kept in an `OrderedDict` keyed by path, at most `synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS + 1` entries (the most candidates one probe visits), oldest evicted first, and replaced when the same path shows a new identity; in-flight decodes exist only while one runs. No time-based expiry (G-27): the identity is re-checked on every request. A daemon restart starts empty (S-2.14). Nothing is written to disk (S-7.7).
4. **Concurrent misses share one decode.** The store holds one `threading.Lock` that guards its three fields and is never held across a decode (the rule `app._ClosedHold` follows, `app.py:4587`). The first request to reach an identity with no held result and no remembered failure registers a `_SharedDecode` for it and decodes. A second request reaching the same identity calls `_SharedDecode.wait()` (a `threading.Event`, as `app.py:1552` uses) and then takes the owner's outcome:
   - derived series: it answers from them (S-4.1, S-4.2);
   - None (a caught decode error): it returns None to its own probe without reading the file, so its probe moves to the next older candidate, where it again shares or finds the held result (S-4.3);
   - any other exception in the owner: it raises `_SharedDecodeFailed`, carrying only the exception type name, so no traceback or parsed content is kept; its request degrades every symphony's replay row (S-4.4). The failure is not remembered, so the next request decodes again.

   The owner sets the event and removes the in-flight entry in `finally`, so no waiter hangs on an owner that raised (S-4.4). Inside one request, the first failure of the resolution is kept on the request object and re-raised (as `_ReplayResolutionFailed`, type name only) to every later symphony, so a failing decode is attempted at most once per request (AC-1). Waiting on an `Event` releases the GIL, so waiters add no contention to the decode. The daemon serves requests on Flask's threaded server in the scheduler's process (`app.py:13316`, `app.py:13310`), and one process-wide store is shared by every request thread.
5. **The clock read stays where base reads it.** The request object reads the ET date once, with the existing expression `synthetic_history.utc_to_eastern(datetime.now(UTC)).strftime("%Y-%m-%d")` (moved from `autotuner.py:1601` into `_IfHeldReplayRequest`, still reading `autotuner.datetime`). The no-longer-held rule reads `app.datetime` (`app.py:6604`) as before. So the golden capture and the head comparison freeze the same two names, `app.datetime` and `autotuner.datetime`; the head adds no clock reader (`synthetic_history.get_cached_synthetic_history_only` only parses the passed date string, `synthetic_history.py:623`; `analytics.get_shadow_current_return_daily_series`, `analytics.py:830`, reads no clock).

### Searches behind the new primitives

- `_ClosedHold` (`app.py:4583-4632`) is the nearest holder, and it does not fit. A waiter that finds no held result after a failed build calls `build()` again itself (`app.py:4615-4628`), which AC-4 forbids (a waiter never re-reads). It also holds up to 4 identities (`app.py:4570`) where D-2 asks for one. So `_SharedDecode` and `_IfHeldReplayStore` are new.
- `analytics._HISTORY_CACHE` (`analytics.py:91-92`, `analytics.py:1650-1680`) has no lock and holds the whole loaded value, which AC-7 forbids.
- `git grep -n "threading.Event()\|Future()\|_inflight\|single.flight" HEAD -- '*.py' ':!tests'` finds only run-once guards that skip a second caller (`app.py:2344`, `advisors/tenure_review.py:1954`) and one ad hoc event (`app.py:1552`). None shares an outcome with waiters.
- `git grep -n "build_if_held_replay_series\|get_cached_synthetic_history_only\|load_cached_history" HEAD -- '*.py'` finds these production callers: `app.py:6611` (the route), `autotuner.py:1603` (the probe), and `synthetic_history.py:640` and `:666` (the two `load_cached_history` calls). No other route or job reads the replay cache.

### Unchanged by construction

`alpha_bot_execution.py` (it imports `autotuner` at `alpha_bot_execution.py:23`; the new module-level store and the stdlib imports add no I/O and no call on the 1-minute path), `math_engine.py`, `guard_preconditions.py`, `analytics.py`, `database.py`, `static/performance.js`, `templates/performance.html`, `migrations/`, `generate_synthetic_history` and the cache file format. The route's `_sample_row`, `_degraded_row` and `_json_safe_float` (`app.py:6562-6598`) and its response (`app.py:6652`) are untouched, so the body is built by the same code from the same values. Each held series is a tuple, handed out as a fresh `list` per request, so no request can alter another's input. `compute_persistence_stats` does not mutate its input anyway (`guard_preconditions.py:68`).

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-3, R-4, R-15, R-16 | app.py:6606-6650 wrapped in `autotuner.if_held_replay_request`; autotuner.py:1556-1608 `build_if_held_replay_series` answering from the scope's one lazy resolution; new: `_IfHeldReplayRequest` (search under Architecture finds no request-scoped replay resolver) | new file `tests/app/test_preconditions_replay_load_once.py`, client pattern of tests/app/test_guard_alpha_preconditions_route.py:55-59; excerpt world (captured droplet fixture, Fixture key FX-A); decode counter wrapping `synthetic_history.load_cached_history` and `json.load` after the CallCounter of tests/app/_refresh_timing_state_world.py:215-236 | C1 |
| AC-2 | R-5, R-16, R-17, R-18, R-20, R-21 | synthetic_history.py:577 and :640 (`load=` keyword); new: `_IfHeldReplayStore` single-entry identity store in autotuner.py (no holder shares a failure with waiters or keeps one identity, see Architecture searches); identity after app.py:1246-1251 | same new file; files rewritten, deleted, aged and replaced between requests with the real key derivation, after tests/synthetic_history/test_autotune_cache_backward_probe.py:59-68; clock frozen through tests/app/_refresh_timing_state_world.py:119-129 | C1 |
| AC-3 | R-1, R-6, R-15, R-19, R-20, R-21, R-22, R-24, R-32 | app.py:6562-6598 and :6652 unchanged; autotuner.py:1539-1553 reused for derivation; autotuner.py:1601 date expression kept | new file `tests/app/test_preconditions_replay_goldens.py`; goldens captured from the unchanged base route by `tests/fixtures/preconditions_replay/capture_goldens.py` (precedent tests/fixtures/refresh_timing/04-c4/capture_requests_per_tick.py:1-60), compared LF-normalized | C1 |
| AC-4 | R-14 | new: `_SharedDecode` with `wait()` and the store's in-flight map in autotuner.py (single-flight sharing an outcome exists nowhere, see Architecture searches); lock never held across a decode, as app.py:4587 | new file `tests/app/test_preconditions_replay_concurrency.py`; real threaded server via tests/ui/_browser_server.py:37-56; a gate around `load_cached_history` and a spy on `autotuner._SharedDecode.wait` to prove the second request waited | C1 |
| AC-5 | R-3, R-9, R-37 | app.py:6606-6650 and autotuner.py:1556-1608 with the AC-2 store (the AC-1 and AC-2 elements); no further production change | new file `tests/app/test_preconditions_replay_latency.py`; production-sized file generated at test time from the excerpt (FX-P), real threaded server tests/ui/_browser_server.py:37-56; decode counts first, then timings against one decode timed alone in the same run | C1 |
| AC-6 | R-7, R-8, R-22, R-36, R-37 | app.py:13316 threaded server unchanged; the AC-4 element (waiters block on an Event, releasing the GIL) and the AC-2 warm path (stat only) | same latency file, with app.py:8833 `/api/performance`, app.py:710 `/api/header-status`, app.py:8374 `/api/candidate-alert` timed alone and paired; new browser file `tests/ui/test_performance_preconditions_warm_browser.py` on the `firefox` fixture of tests/ui/test_dashboard_charts_browser.py:65 | C1 |
| AC-7 | R-10 | the store's bounds in autotuner.py (one held identity, failures capped at synthetic_history.py:545 plus one, parsed history deleted before return) | new file `tests/app/test_preconditions_replay_memory.py` with `tracemalloc`; production-shaped 6.3 MB file generated from the excerpt (FX-M) | C1 |
| AC-8 | R-11, R-12, R-13, R-16, R-26, R-32, R-34 | synthetic_history.py:621-651 probe reused once per request; WARNING only from synthetic_history.py:276-280 inside the owner's decode; app.py:6517 auth and GET-only unchanged | the AC-1 file plus tests/app/test_guard_alpha_preconditions_route.py:401-433 (auth, GET-only, read-only scans) and tests/synthetic_history/test_autotune_cache_backward_probe.py:83 (writer read unchanged); diff check at cycle close | C1 |

## Execution Model

One cycle, C1, because the store, the request scope, the probe's loader keyword and the route's `with` line form one contract that no part delivers alone: the scope without the store still decodes per request, the store without the scope still logs per symphony.

- C1 (AC-1 to AC-8):
  - RED: `test-writer`. One author owns the shared helper module `tests/app/_preconditions_replay_world.py`, the golden capture and every new test file, so the states the goldens were captured on are the states the head is compared on. The RED author is the closer. Before GREEN it runs the golden capture on the RED commit, whose production files equal base's (Cycle-Close Criteria), and commits the goldens with their provenance.
  - GREEN: `implementer`, reading the RED handoff.
  - No `quant-test-writer` and no `quant-code-reviewer`: no statistic, verdict, threshold or math-layer file changes (NB-1); the route builds its rows with the unchanged code (`app.py:6562-6598`), and AC-3 pins the bytes.
- Review, dispatched by the PM at cycle-complete against one head, in parallel:
  - `qa` and `code-reviewer`.
  - `api-expert`, because the API surface changes: the route body and request concurrency on the threaded server.
  - No `pipelines-expert`: no scheduler slot, job or async pipeline is touched; the cache read runs synchronously inside the request.
  - No `*-integration-auditor`: one surface (API) and one repository.
  - No `ux-expert` or capturer: no template, client script or style changes (NB-11, NB-14). The browser scenarios S-6.5 and S-6.6 are a `browser`-marked test the PM runs at the merge gate, plus the PM's live check.
  - Not Sensitive, so no hidden-mode `qa`.
  - One `doc-gen` at the slice tail, after every reviewer passes the head.
- Isolation follows the project's environment rules: every teammate works in its own worktree off the branch base below, never in the primary checkout. Kickoffs mandate `cd` plus a `git rev-parse --abbrev-ref HEAD` and `git rev-parse HEAD` check, and copy `.env` into the worktree. Pytest runs on named files only, `-n0` or the default worker count, never `-n auto`, never more than 4 workers, never `-o addopts=""`; CI is the full gate.

Team size: 6 = 1 test-writer + 1 implementer + 1 qa + 1 code-reviewer + 1 api-expert + 1 doc-gen

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 |  | feat/preconditions-replay-load-once at the commit that adds this document (child of b137694d) |

## Per-Cycle Detail

### C1: AC-1, AC-2, AC-3, AC-4, AC-5, AC-6, AC-7, AC-8

Files modified (production):
- `autotuner.py`:
  - imports at `autotuner.py:1-10`: add `contextlib`, `contextvars`, `threading`, and `Callable` and `Iterator` from `collections.abc`.
  - `build_if_held_replay_series` (`autotuner.py:1556-1608`): body replaced as described in Architecture part 1. The signature `(symphony_id: str) -> list | None` is unchanged. The membership check `symphony_id not in bot_state` (`autotuner.py:1590`) is kept for both paths. The docstring keeps its cache-hit-only contract and adds the request scope and the store.
  - `collect_if_held_daily_returns` (`autotuner.py:1539-1553`): unchanged, reused by the derivation.
  - new, placed after `build_if_held_replay_series`:
    - `_derive_if_held_series(history) -> dict[str, tuple | None]`
    - `class _SharedDecode` (`wait()`, plus the owner's methods that record derived series, None or a failed type name and set the event)
    - `class _SharedDecodeFailed(Exception)` and `class _ReplayResolutionFailed(Exception)`
    - `class _IfHeldReplayStore` (`load(cache_file)`, `clear()`)
    - `_IF_HELD_REPLAY_STORE`
    - `class _IfHeldReplayRequest` (`bot_state`, `series(symphony_id)`)
    - `_IF_HELD_REPLAY_REQUEST: contextvars.ContextVar`
    - `if_held_replay_request(bot_state)`, a `contextlib.contextmanager`
- `synthetic_history.py`: the `get_cached_synthetic_history_only` signature (`synthetic_history.py:577`) and its one loader call (`:640`), plus one docstring sentence (`:619-621`). Nothing else.
- `app.py`: `guard_alpha_preconditions` only. The `with autotuner.if_held_replay_request(bot_state_dict):` statement opens just before the loop at `app.py:6606`, and the loop body is indented under it with no other edit. The docstring's cache-hit-only paragraph (`app.py:6541-6546`) adds one sentence: one probe per request, and the unchanged-file reuse.

Files modified (tests):
- `tests/conftest.py`: one autouse fixture beside `_reset_closed_market_holds` (`tests/conftest.py:542-567`). It clears `autotuner._IF_HELD_REPLAY_STORE` before and after each test when `sys.modules.get("autotuner")` is loaded and has the store (`getattr` default, so it is inert at RED and never imports `autotuner` itself).
- `tests/app/test_guard_alpha_preconditions_route.py`: one added method in `TestReadOnlyScope` (`:414`) sending a real `POST` and asserting 405 (S-8.6).
- `tests/synthetic_history/test_autotune_cache_backward_probe.py`: one added test in `TestBackwardProbeFindsAgedHit` (`:83`). On an existing valid file at the real key, `generate_synthetic_history` returns the decoded dict and never calls `fetch_bars` (S-8.8).

Files added (tests and fixtures):
- `tests/app/_preconditions_replay_world.py`, the helper (Per-Cycle Test Plan lists its parts).
- `tests/app/test_preconditions_replay_load_once.py`, `tests/app/test_preconditions_replay_goldens.py`, `tests/app/test_preconditions_replay_concurrency.py`, `tests/app/test_preconditions_replay_latency.py`, `tests/app/test_preconditions_replay_memory.py`.
- `tests/ui/test_performance_preconditions_warm_browser.py` (marker `browser`).
- `tests/fixtures/preconditions_replay/capture_goldens.py`, `tests/fixtures/preconditions_replay/goldens/<state>.json`, `tests/fixtures/preconditions_replay/PROVENANCE.md`.

Primitives imported: `contextvars.ContextVar` with `contextlib.contextmanager` (`analytics.py:637-655`); `threading.Lock` held only around dict reads and writes (`app.py:4587`); `threading.Event` (`app.py:1552`); `os.stat` size and `st_mtime_ns` identity (`app.py:1246-1251`); `collect_if_held_daily_returns` (`autotuner.py:1539`); `synthetic_history.load_cached_history` (`synthetic_history.py:264`) as the only file read.

First production uses: `autotuner.if_held_replay_request` by the route (`app.py:6606`); the `load=` keyword by `_IfHeldReplayRequest`.

Writers of the entity this cycle adds (the replay store) and of the files whose identity it keys on, each a round-1 RED row:
- The preconditions route through the scope (`app.py:6517`, the loop at `:6606`): S-1.1, S-2.1.
- The standalone accessor `autotuner.build_if_held_replay_series` outside any scope (`autotuner.py:1556`; no production caller, kept for the accessor tests `tests/autotuner/test_build_if_held_replay_series.py:37`, `:62`): those two existing tests stay green on the store.
- `generate_synthetic_history` writing a cache file (`synthetic_history.py:655`, write at `:841-851`), reached from `run_autotuner` (`autotuner.py:2440`) in the engine subprocess and from the `POST /api/force_eod` thread (`app.py:8570-8622`): S-2.6 (new file), S-2.7 and S-2.8 (in-place rewrite), S-2.12 (partial write), S-8.8 (its own read unchanged).

## Per-Cycle Test Plan

C1 scope: route tests through the Flask test client (unit and integration level), concurrency and timing tests through the real threaded server in-process, one `tracemalloc` module, one `browser`-marked module, one diff check. No live backend: the PM's live checks are under Merge Criteria.

Write-path census (the matrix's four columns; every route, job or command that reaches a matrix rule):

| Entry point | Rules |
|---|---|
| Page load | AC-1, AC-2, AC-3, AC-8: `GET /api/guard-alpha-preconditions` (`app.py:6517`), first fetched after the symphony list loads (`static/performance.js:914-924`) |
| 60 s poll | AC-1, AC-2, AC-3, AC-8: the same route from the poll (`static/performance.js:1093-1097`) |
| Concurrent GETs | AC-1, AC-2, AC-3, AC-4, AC-8: overlapping requests on the threaded server (`app.py:13316`) |
| Autotune run | none enforced at this entry point (the matrix marks every rule n/a): `generate_synthetic_history` (`synthetic_history.py:655`, write `:841-851`) from `run_autotuner` (`autotuner.py:2440`) in the engine subprocess and from `POST /api/force_eod` (`app.py:8570-8622`); it changes the file identity AC-2 re-checks (S-2.6, S-2.7, S-2.8, S-2.12) and its own read stays as at base (S-8.8) |

No other entry point reaches a matrix rule: `git grep` finds the route as the only production caller of the replay seam (Architecture searches).

The helper `tests/app/_preconditions_replay_world.py` holds:
- `excerpt()`, which loads `tests/fixtures/preconditions_replay/cache_excerpt_2026-11-13.json`.
- `bot_state(...)`, built from the excerpt's `bot_state_current_holdings` and `bot_state_names`. The five metadata keys are optional.
- `write_cache_file(bot_state, date_str, history)`, whose path comes from `synthetic_history._resolve_history_cache_key`, never a typed hash (pattern `tests/synthetic_history/test_autotune_cache_backward_probe.py:59-68`).
- The stated case edits (S-3.3 to S-3.6). A truncated copy stands for a corrupt or partially written file.
- `seed_shadow(...)`, writing through `database.record_shadow_observation` (`database.py:3254`; pattern `tests/app/_refresh_timing_state_world.py:284-299`) from `random.Random(20261115)`. Some symphonies get at least `N_MIN_OBS` (40, `guard_preconditions.py:19`) days and some fewer, through the frozen instant's most recent completed session. One no-longer-held symphony stops before the second most recent.
- `freeze(monkeypatch, instant)`, which patches `app.datetime` and `autotuner.datetime` with `frozen_datetime_class` (`tests/app/_refresh_timing_state_world.py:119-129`) and sets `analytics.DB_FILE` to the test database (as `tests/app/_refresh_timing_state_world.py:368` does).
- `count_decodes(monkeypatch)`, two CallCounters (`tests/app/_refresh_timing_state_world.py:215-224`). One wraps `synthetic_history.load_cached_history`. The other wraps `json.load`, so a read that bypassed the loader would still be counted. A test asserts on both.
- `generate_file(path, ticks_per_date, dates=250)`.
- `golden(name)`, which reads bytes and normalizes CRLF to LF, because `core.autocrlf` is true on Windows checkouts and the repo has no `.gitattributes`. The response body holds no CR of its own.
- `server()`, which reuses `tests/ui/_browser_server.py:51-56` `running_server`.

Fixture keys used in the table:
- FX-A: the excerpt world. 12 held symphonies from the captured droplet fixture (PM, read-only, 2026-11-15T17:51:39Z; its md5 equals the source file name's hash). The cache file is the excerpt's `history` (60 real dates per symphony, at least `N_MIN_OBS`) written at the real key for 2026-11-13. Shadow rows are seeded as above. The clock is frozen at 2026-11-15 12:00 ET (Sunday) unless the row says otherwise.
- FX-A plus an edit: the edit is named in the row and derived only by the plan's stated edits.
- FX-P: a production-sized file generated at test time from the excerpt's real tick records: 12 symphonies, 250 dates each, the real last tick per date kept so the replay rows stay real. 114 ticks per date gives about 71 MB, and about 235 ticks per date gives about 147 MB for S-5.3, inside the recorded 1 to 456 range. The file is generated once per module in `tmp_path_factory` and deleted at module teardown, never committed. Measured in memory on this machine: 62.7 MB encodes in 0.55 s and decodes in 0.45 s; 128.5 MB encodes in 1.13 s and decodes in 1.14 s. So generation costs about 1 s for 71 MB and about 2.5 s for 147 MB, plus the disk write. The module's total should stay under about 60 s on a CI runner.
- FX-M: a production-shaped file (12 symphonies, 250 dates, 10 ticks per date, about 6.3 MB, decoded in 0.05 s untraced), so `tracemalloc`'s slowdown stays a few seconds.
- FX-C: a corrupt file, the first half of a valid file's bytes, the state the in-place writer leaves mid-write (`synthetic_history.py:841-851`).
- Goldens: one body per named state, captured from the unchanged base route by `capture_goldens.py` at FX-A's instant. The script runs from the repository root with `DB_PATH` set to a scratch file and auth and CSRF off, as the precedent does at `tests/fixtures/refresh_timing/04-c4/capture_requests_per_tick.py:42-51`. It builds each state with the helper, freezes `app.datetime` and `autotuner.datetime`, and writes `resp.data`. It refuses to run when `git diff --name-only aafe476d -- app.py autotuner.py synthetic_history.py analytics.py database.py guard_preconditions.py` is non-empty, and it records that SHA in `PROVENANCE.md`.

Harness rules for every new file:
- An autouse fixture `monkeypatch.chdir(tmp_path)`, so `cache/` is per test (pattern `tests/autotuner/test_build_if_held_replay_series.py:37-38`).
- Decode counts are asserted before any timing or body check.
- Bodies are compared as bytes with the goldens, never with literals copied from a run.
- Concurrency starts requests from threads behind a `threading.Barrier`, with every `join` bounded (10 s), so a hang fails rather than stalls.
- The timing tests carry no `perf` marker. The PM ruled (Q-7) that they run in the default suite and CI. Each records its measured numbers into the review folder through a session-scoped collector.
- Nothing pops `sys.modules`.

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | route integration, decodes counted | Page load | new: tests/app/test_preconditions_replay_load_once.py (no test counts decodes; client pattern tests/app/test_guard_alpha_preconditions_route.py:55-59) | FX-A |
| S-1.2 | AC-1 | route integration | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A reduced to one symphony, file rewritten at that state's real key |
| S-1.3 | AC-1 | route integration | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A bot state with only the five metadata keys |
| S-1.4 | AC-1 | route integration | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A with `current_holdings` removed from every entry |
| S-1.5 | AC-1 | route integration, shadow rows compared with the golden | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A with no cache file; golden no_file |
| S-1.6 | AC-1 | route integration, WARNING captured | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A plus FX-C at the newer date 2026-11-15 key |
| S-1.7 | AC-1 | route integration | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-C as the only file |
| S-1.8 | AC-1 | route integration | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A with one symphony's shadow rows stopping before the second most recent completed session |
| S-1.9 | AC-1 | route integration, clock advanced 60 s per poll | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A |
| S-1.10 | AC-1 | threaded server, two threads | Concurrent GETs | new: tests/app/test_preconditions_replay_load_once.py | FX-A |
| S-2.1 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, then `database.save_state` rewrites returns and values with holdings unchanged |
| S-2.2 | AC-2 | route integration, clock advanced 60 s per poll | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A |
| S-2.3 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, clock moved to 2026-11-16 20:00 ET |
| S-2.4 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, clock at the date that puts 2026-11-13 exactly 10 NYSE trading days back (market_calendar.is_trading_day) |
| S-2.5 | AC-2 | route integration, INFO captured | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, clock one trading day further than S-2.4 |
| S-2.6 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A plus the same history written at the 2026-11-14 key with one return edited (F2) |
| S-2.7 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, file rewritten with one symphony's dates trimmed (new size) |
| S-2.8 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, one digit changed at equal length, `os.utime` moves `st_mtime_ns` by 1 s |
| S-2.9 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, one symphony's holdings edited so the key changes |
| S-2.10 | AC-2 | route integration, fetch instrumented, INFO captured | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, file deleted after the first GET |
| S-2.11 | AC-2 | route integration, WARNING captured | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A as G at 2026-11-12 key plus F at 2026-11-13 replaced in place by FX-C |
| S-2.12 | AC-2 | route integration, WARNING captured | Autotune run | new: tests/app/test_preconditions_replay_load_once.py | FX-A plus F2 at the 2026-11-14 key as FX-C, then completed in place |
| S-2.13 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, one symphony's shadow rows made stale between the GETs (clock advanced) |
| S-2.14 | AC-2 | route integration, store cleared to stand for a restart | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A |
| S-2.15 | AC-2 | threaded server, two threads | Concurrent GETs | new: tests/app/test_preconditions_replay_load_once.py | FX-A, warmed by one GET |
| S-2.16 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A plus a second holdings state with its own file F2 at its real key; holdings switched F, F2, F |
| S-2.17 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A plus F2 at the 2026-11-14 key, decoded, then deleted |
| S-2.18 | AC-2 | route integration, WARNING captured, three polls | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A as G at 2026-11-12 key plus FX-C as F at 2026-11-13 key |
| S-2.19 | AC-2 | route integration | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | S-2.18 state, then F rewritten with valid content |
| S-2.20 | AC-2 | route integration, WARNING captured | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-C as the only file; golden only_corrupt |
| S-3.1 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py (no golden comparison exists for this route) | FX-A; golden full (captured at base) |
| S-3.2 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A without a file; golden no_file |
| S-3.3 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A with one symphony removed from `history`; golden file_lacks_one |
| S-3.4 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A with one symphony's tick lists emptied; golden empty_ticks |
| S-3.5 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A with one symphony's last-tick returns set to 0.0; golden zero_variance |
| S-3.6 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A with one symphony removed from the bot state only (key recomputed for that state); golden file_has_extra |
| S-3.7 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A with one symphony's shadow rows omitted; golden no_shadow |
| S-3.8 | AC-3 | route integration, byte compare, decodes counted | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A with `database.load_state` raising; golden load_state_raises |
| S-3.9 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | `database.load_state` returning `{}`; golden load_state_empty |
| S-3.10 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A with `analytics.get_shadow_current_return_daily_series` raising for one id; golden shadow_raises |
| S-3.11 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A plus the five metadata keys and a no-longer-held symphony; golden metadata_and_stale |
| S-3.12 | AC-3 | route integration, byte compare, clock advanced 60 s | 60 s poll | new: tests/app/test_preconditions_replay_goldens.py | FX-A; goldens full and full_plus_60s |
| S-3.13 | AC-3 | route integration, byte compare | Page load | new: tests/app/test_preconditions_replay_goldens.py | FX-A plus an added symphony with holdings and the file at the new state's real key; golden symphony_added |
| S-3.14 | AC-3 | threaded server, byte compare | Concurrent GETs | new: tests/app/test_preconditions_replay_goldens.py | FX-A; golden full |
| S-4.1 | AC-4 | threaded server, gate plus wait spy | Concurrent GETs | new: tests/app/test_preconditions_replay_concurrency.py (no concurrent test of this route exists; server tests/ui/_browser_server.py:37-56) | FX-A; golden full |
| S-4.2 | AC-4 | threaded server, gate plus wait spy | Concurrent GETs | new: tests/app/test_preconditions_replay_concurrency.py | FX-A |
| S-4.3 | AC-4 | threaded server, WARNING captured | Concurrent GETs | new: tests/app/test_preconditions_replay_concurrency.py | FX-A as G at 2026-11-12 key plus FX-C as F at 2026-11-13 key; golden corrupt_newest_valid_older |
| S-4.4 | AC-4 | threaded server, injected fault | Concurrent GETs | new: tests/app/test_preconditions_replay_concurrency.py | FX-A; `load_cached_history` raises RuntimeError on its first call only; golden full for the next GET |
| S-4.5 | AC-4 | threaded server, gate | Concurrent GETs | new: tests/app/test_preconditions_replay_concurrency.py | FX-A, file rewritten at a new size while the gated decode runs |
| S-4.6 | AC-4 | threaded server | Concurrent GETs | new: tests/app/test_preconditions_replay_concurrency.py | FX-A; `/api/performance?scope=aggregate&days=60d` body alone as reference |
| S-4.7 | AC-4 | threaded server, fetch instrumented | Concurrent GETs | new: tests/app/test_preconditions_replay_concurrency.py | FX-A without a file; golden no_file |
| S-5.1 | AC-5 | threaded server, timed, median of 5 | 60 s poll | new: tests/app/test_preconditions_replay_latency.py (no timing test of this route exists) | FX-P 71 MB |
| S-5.2 | AC-5 | threaded server, timed against one decode alone | Page load | new: tests/app/test_preconditions_replay_latency.py | FX-P 71 MB |
| S-5.3 | AC-5 | threaded server, timed | 60 s poll | new: tests/app/test_preconditions_replay_latency.py | FX-P 147 MB |
| S-5.4 | AC-5 | threaded server, timed | Page load | new: tests/app/test_preconditions_replay_latency.py | FX-A world, no file |
| S-5.5 | AC-5 | threaded server, timed, 5 in a row | 60 s poll | new: tests/app/test_preconditions_replay_latency.py | FX-P 71 MB |
| S-5.6 | AC-5 | threaded server, timed against one read of F and one decode of G alone | Page load | new: tests/app/test_preconditions_replay_latency.py | FX-P as G plus its FX-C copy as newer F |
| S-5.7 | AC-5 | threaded server, timed | 60 s poll | new: tests/app/test_preconditions_replay_latency.py | S-5.6 state after its GET |
| S-6.1 | AC-6 | threaded server, paired, median of 5 | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | FX-P 71 MB, warm |
| S-6.2 | AC-6 | threaded server, paired | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | FX-P 71 MB, warm |
| S-6.3 | AC-6 | threaded server, paired | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | FX-P 71 MB, warm |
| S-6.4 | AC-6 | threaded server, paired, store cleared per run, median of 3 | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | FX-P 71 MB, cold |
| S-6.5 | AC-6 | browser (`browser` marker), real threaded server, page requests continued to the app | Page load | new: tests/ui/test_performance_preconditions_warm_browser.py (no browser test opens /performance against real answers; `firefox` fixture tests/ui/test_dashboard_charts_browser.py:65) | FX-P 71 MB, warmed by one GET; headline time read from the page's resource timing |
| S-6.6 | AC-6 | browser, `context.clock.install` then two 60 s runs forward (precedent tests/ui/_state_poll_page_capture.py:208) | 60 s poll | new: tests/ui/test_performance_preconditions_warm_browser.py | FX-A, warm |
| S-6.7 | AC-6 | threaded server, paired | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | FX-A world, no file |
| S-6.8 | AC-6 | threaded server, paired, cold | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | FX-P as G plus FX-C copy as F |
| S-6.9 | AC-6 | threaded server, paired | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | S-5.7 state |
| S-6.10 | AC-6 | threaded server, paired, cold | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | FX-P 71 MB, cold |
| S-6.11 | AC-6 | threaded server, paired, cold | Concurrent GETs | new: tests/app/test_preconditions_replay_latency.py | FX-P 71 MB, cold |
| S-7.1 | AC-7 | route integration, `tracemalloc`, after a warm-up GET on a no-symphony state so lazy imports fall outside the window | Page load | new: tests/app/test_preconditions_replay_memory.py (no memory test of this route exists) | FX-M |
| S-7.2 | AC-7 | route integration, `tracemalloc` | 60 s poll | new: tests/app/test_preconditions_replay_memory.py | FX-M replaced twice by edited copies |
| S-7.3 | AC-7 | threaded server, `tracemalloc` peak | Concurrent GETs | new: tests/app/test_preconditions_replay_memory.py | FX-M, cold |
| S-7.4 | AC-7 | route integration, `tracemalloc` | Page load | new: tests/app/test_preconditions_replay_memory.py | FX-A world, no file |
| S-7.5 | AC-7 | route integration, `tracemalloc` | Page load | new: tests/app/test_preconditions_replay_memory.py | FX-M truncated (FX-C) |
| S-7.6 | AC-7 | route integration, `tracemalloc`, 20 GETs | 60 s poll | new: tests/app/test_preconditions_replay_memory.py | FX-M, warm |
| S-7.7 | AC-7 | route integration, directory listings before and after | Page load | new: tests/app/test_preconditions_replay_memory.py | FX-A, FX-M and FX-C states in turn |
| S-8.1 | AC-8 | route integration, `fetch_bars`, `generate_synthetic_history` and `socket.socket.connect` instrumented | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A without a file |
| S-8.2 | AC-8 | route integration, caplog INFO | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A without a file (11 candidates missing) |
| S-8.3 | AC-8 | route integration, caplog WARNING, two polls | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A as G at 2026-11-12 key plus FX-C at 2026-11-13 key |
| S-8.4 | AC-8 | route integration, caplog | Page load | new: tests/app/test_preconditions_replay_load_once.py | FX-A with the file at the frozen date's own key |
| S-8.5 | AC-8 | route, auth on (existing test, kept unchanged) | Page load | tests/app/test_guard_alpha_preconditions_route.py:401 | `auth_client_no_session` (tests/app/test_guard_alpha_preconditions_route.py:390) |
| S-8.6 | AC-8 | route, real POST asserting 405 | Page load | tests/app/test_guard_alpha_preconditions_route.py:414 | none needed beyond the client fixture (tests/app/test_guard_alpha_preconditions_route.py:55) |
| S-8.7 | AC-8 | diff check at cycle close and merge | Autotune run | held: diff check recorded in the coverage map; `git diff --stat aafe476d` over the protected list, and hunks of synthetic_history.py, database.py and analytics.py outside the named functions only (tests/test_mdd_window_truth_zero_diff_guard.py:1 is the existing engine pin, left as is) | the slice head against aafe476d |
| S-8.8 | AC-8 | unit, `fetch_bars` instrumented | Autotune run | tests/synthetic_history/test_autotune_cache_backward_probe.py:83 | a valid file at the real key (pattern tests/synthetic_history/test_autotune_cache_backward_probe.py:59-68) |
| S-8.9 | AC-8 | source scans (existing tests, kept unchanged) | Page load | tests/app/test_guard_alpha_preconditions_route.py:421 | route source via `inspect.getsource` (tests/app/test_guard_alpha_preconditions_route.py:421-433) |
| S-8.10 | AC-8 | threaded server, caplog per request thread | Concurrent GETs | new: tests/app/test_preconditions_replay_load_once.py | FX-A without a file |
| S-8.11 | AC-8 | route integration, fetch instrumented | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, file deleted after the first GET |
| S-8.12 | AC-8 | threaded server, fetch instrumented | Concurrent GETs | new: tests/app/test_preconditions_replay_load_once.py | FX-A without a file |
| S-8.13 | AC-8 | route integration, caplog | 60 s poll | new: tests/app/test_preconditions_replay_load_once.py | FX-A, clock rolled past the window after the first GET |

Existing files the cycle's closer runs unchanged, and whose meaning the design keeps:
- `tests/app/test_guard_alpha_preconditions_route.py` (all ten patch sites still drive the route).
- `tests/app/test_preconditions_leave_out_no_longer_held.py`.
- `tests/ui/test_performance_page_data_truth.py`.
- `tests/autotuner/test_build_if_held_replay_series.py`.
- `tests/autotuner/test_collect_if_held_daily_returns.py`.
- `tests/synthetic_history/` (the backward-probe tests call `get_cached_synthetic_history_only` with no `load` argument, `tests/synthetic_history/test_autotune_cache_backward_probe.py:91-338`).

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | The route stops calling the per-symphony seam and the ten existing patch sites pass vacuously (DC-7) | Low | High | The design keeps `autotuner.build_if_held_replay_series(sym_id)` at `app.py:6611`. The closer runs the files listed at the end of the test plan, and the first route test file's patched replay series must appear in the body (`tests/app/test_guard_alpha_preconditions_route.py:68-123`) |
| C1 | A held result leaks across tests or directories and a test reads another's series | Medium | High | Identity uses the absolute path, because the cache directory is cwd-relative (`synthetic_history.py:523`, `:532-534`). The autouse store reset follows `tests/conftest.py:542-567`, and every new file chdirs to `tmp_path` |
| C1 | Goldens differ only by line endings between Windows and CI | Medium | Medium | `core.autocrlf` is true here and there is no `.gitattributes`. The golden reader normalizes CRLF to LF, the failure class the engine pin records (`tests/test_mdd_window_truth_zero_diff_guard.py:21-34`) |
| C1 | Goldens capture the head instead of base | Low | High | `capture_goldens.py` refuses to run unless the six production files equal aafe476d (precedent `tests/fixtures/refresh_timing/04-c4/capture_requests_per_tick.py:30-40`). It runs at RED before GREEN, and the base SHA goes into `PROVENANCE.md` |
| C1 | The golden freeze misses a clock reader, so goldens hold the real date | Low | High | The head keeps the date read in `autotuner` (`autotuner.py:10`, `:1601`) and adds none. `freeze` patches `app.datetime` and `autotuner.datetime`, and S-2.3 to S-2.5 fail if the probe ignores the frozen date |
| C1 | A waiter hangs when the owner's decode raises | Low | High | The owner sets the event and pops the in-flight entry in `finally`. S-4.4 joins with a bound |
| C1 | The non-atomic writer (`synthetic_history.py:841-851`) changes the file during a decode and a stale or partial result is held | Medium | Medium | Re-stat after the decode and store only when the identity is unchanged (S-2.12, S-4.5). The same-size, same-mtime rewrite stays as accepted in plan Q-2 and Known-Unconfirmed 3 |
| C1 | Timing tests flake on CI's two workers (`pyproject.toml:87-89`, `-n 2 --dist loadfile`) | Medium | Medium | Decode counts come first and are deterministic. Bounds are relative to timings from the same run, with medians and the PM's margins (Known-Unconfirmed 2). Measured numbers are recorded so a failure shows its cause |
| C1 | The 147 MB generation and decode strain a CI worker's memory or disk | Low | Medium | Measured locally: 128.5 MB decodes in 1.14 s. The file is generated once, only for S-5.3, and deleted at module teardown. `tracemalloc` runs only on the 6.3 MB FX-M (Known-Unconfirmed 7) |
| C1 | A lock held across the decode serializes unrelated requests, or the parsed history survives in a traceback | Low | High | The lock guards dict operations only (`app.py:4587` rule). The parsed history is deleted before return, and failures are shared as type names only. AC-7 (S-7.1, S-7.5) measures what is retained (DC-9) |
| C1 | The engine imports `autotuner` (`alpha_bot_execution.py:23`), so new module-level state reaches the engine process | Low | Low | Stdlib imports and an empty store, no I/O at import, nothing called on the 1-minute path. `alpha_bot_execution.py` carries zero diff (S-8.7) |
| C1 | Waiters on a failed non-decode error degrade every symphony, where base degraded one call | Low | Low | This is plan S-4.4 and Known-Unconfirmed 5 by PM ruling. The failure is not remembered, so the next request recovers |

## Cycle-Close Criteria

- Every scenario assigned to C1 has a row in `records/coverage.json` under `.claude/review/<HEAD-sha12>/`: `tested` with its test file and name, or held with its reason (S-8.7: the diff check, with its output pasted). `plan-trace check-coverage` runs clean against the plan and this approach.
- RED was committed before GREEN, and each RED test was observed failing for the stated reason. The tests asserting today's behaviour stay green at RED (S-1.3, S-1.4, S-1.5, S-3.x byte compares, S-8.5, S-8.8, S-8.9).
- The goldens were captured at the RED commit. `PROVENANCE.md` records the base SHA and the empty output of `git diff --name-only aafe476d <RED sha> -- app.py autotuner.py synthetic_history.py analytics.py database.py guard_preconditions.py`. No golden changes after GREEN.
- The new files and these existing files pass under `-n0`, each named on its own pytest run:
  - `tests/app/test_guard_alpha_preconditions_route.py`
  - `tests/app/test_preconditions_leave_out_no_longer_held.py`
  - `tests/ui/test_performance_page_data_truth.py`
  - `tests/autotuner/test_build_if_held_replay_series.py`
  - `tests/autotuner/test_collect_if_held_daily_returns.py`
  - every file under `tests/synthetic_history/`
  - `tests/test_mdd_window_truth_zero_diff_guard.py`
  - `tests/js_syntax/test_js_syntax.py`
- `ruff format --check` and `ruff check` are clean (line length 100).
- Diff scope:
  - `git diff --stat aafe476d -- alpha_bot_execution.py math_engine.py guard_preconditions.py analytics.py database.py static/performance.js templates/performance.html migrations/` is empty.
  - In `synthetic_history.py`, only `get_cached_synthetic_history_only` changes.
  - In `app.py`, only `guard_alpha_preconditions` changes.
- The timing tests' measured numbers are written into the review folder.
- The sufficiency sweep runs the plan's DC-1 to DC-10 sibling queries and records each result in the coverage map.

## Merge Criteria

Delivery: no PR (project mandate). The PM merges the C1 branch into `feat/preconditions-replay-load-once`, pushes the head to an `assembly/**` branch for CI, and fast-forwards `main` only when CI is green on that exact SHA and the `qa`, `code-reviewer` and `api-expert` verdicts on the head read PASS.

The head being merged must pass:
- CI green on the exact SHA: ruff format, ruff check and the full pytest under CI's own worker count, including the timing tests (no `perf` marker, plan Q-7).
- No migration and no configuration key (NB-6): `migrations/` and `database._MIGRATION_FILES` are unchanged.
- The `browser` file `tests/ui/test_performance_preconditions_warm_browser.py`, run by the PM with `-m browser` (the marker's merge-gate convention, `pyproject.toml:97`).
- The PM's live functional check, first on a local server against a copy of the droplet's database and its newest cache file, with a held symphony's holdings left equal to the file's hash so the file resolves (plan Trigger fact 10):
  - a cold and a warm `GET /api/guard-alpha-preconditions`, timed from the daemon's per-request timing lines;
  - a concurrent `/api/performance?scope=aggregate&days=60d` during the warm request, timed against its alone time;
  - the rendered `/performance` checked in a browser: the headline figures fill, and the preconditions rows and verdicts equal those the unchanged base build gives for the same copy;
  - the log shows one INFO miss line per request.
- The deploy gate (G-1, G-2):
  - no logout generation bump, `SECRET_KEY` unchanged, the session lifetime and migrations 040 and 041 untouched, so the restart forces no re-login (NB-15);
  - deployed out of market hours after a closed-market check that includes the NYSE holiday calendar;
  - the morning refresh is proven to start on the next session.
- After the deploy, the PM runs the droplet timing check out of hours, on a weekend or after the Friday autotune (about 19:50 ET), when a file resolves. Warm under 0.5 s, cold within one decode plus 1.0 s, and `/api/performance` within 0.5 s of its alone time during a warm request (Success Measures).
- At ship, doc-gen adds one `feature-plans/BACKLOG.md` line each for NB-12 and NB-17 (plan Q-8).

## Items Raised to the PM

none: every criterion, requirement item and scenario is carried in C1, and the census entry points are the matrix's four columns. Two design choices are within the plan and noted so the PM can confirm them:
1. S-6.5 and S-6.6 are carried by a `browser`-marked test (deselected by default, run by the PM at the merge gate) and by the PM's live check, not by a capturer dispatch, because the diff renders no UI.
2. AC-7 is measured on a production-shaped 6.3 MB file (FX-M), not the 71 MB file: `tracemalloc` slows decoding several times over, and the plan asks only for a "production-shaped" file in S-7.1.
