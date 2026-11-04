# Approach: Daily regime label producer for the live trailing-stop confirmation
Spec: feature-plans/regime-label-producer.md
Ref: AlphaBotPM@ff161d72cb6efc73dcc51ef1cc5e1aa12c1b44cc

## Architecture

Every file:line below is at AlphaBotPM@ff161d72 (the worktree HEAD equals the ref).

### Q-4 choice: the same-session rule is a keyword at the engine's existing call

The rule (AC-4) is enforced at the read, so a run that never happens cannot defeat it (D-3). Of the two placements the PM allowed, the design takes the engine call site:

- `database.get_cached_regime_label` (`database.py:1745-1797`) gains a keyword-only parameter `session_date: str | None = None`. When it is a string, the accessor returns the stored label only when the row's `as_of_date` equals `session_date` exactly, and None otherwise, including an empty or malformed `as_of_date` (S-4.6). The equality test runs on the same single-statement row the accessor already reads (`database.py:1766-1768`), so label and date always come from one row (S-4.9). With `session_date=None` the accessor behaves exactly as at the ref, and the `staleness_cutoff_days` branch (`database.py:1783-1797`) is unchanged.
- The engine's read (`alpha_bot_execution.py:1526-1529`) passes `session_date=current_date_str`. `current_date_str` is the engine's ET calendar date (`alpha_bot_execution.py:716`, from `get_current_et()` at `:621` and `:453-462`), assigned once per process before the symphony loop (`:1236-1239`) and never reassigned. The engine diff is that one keyword argument inside the statement at `:1526-1529`, and nothing else in the file.

Why not the accessor default: changing the default to "today in ET" would move four existing accessor tests, not only the item 11 pin the PM authorized (`tests/execution/test_regime_cache_isolation.py:354-368`). Items 6, 7 and the latest-wins test read with the default after saving rows dated 2026-05-30 and 2026-05-31 (`:197-215`, `:223-248`, `:252-275`), so each would return None. It would also put a second ET clock in `database.py` that can disagree with the engine's `current_et` at a date boundary. Both placements are equally safe against a run that never happens; the call-site keyword stays inside the PM's authorized pin moves (`tests/test_mdd_window_truth_zero_diff_guard.py:72-77`, `tests/engine/test_close_snapshot_builder_scope.py:28`) and keeps one clock.

### New module `regime_label_job.py` (repository root)

new: no producer exists. `git grep save_regime_label` outside `tests/` finds only its definition (`database.py:1717`) and the docstrings at `database.py:1722`, `:1727`. The module sits beside `regime_classifier.py` at the root, not in `advisors/`, because its output governs the live engine, not an advisory panel.

Public entry:
- `run_regime_label_job(load_symphonies, *, clock=None) -> RegimeLabelRun`. `load_symphonies` is a zero-argument callable returning `{symphony_id: entry}` for symphony entries only. `clock` returns an aware UTC `datetime`; it defaults to `datetime.now(UTC)`, and tests inject it. It never raises: every failure becomes a per-symphony reason or the run-level failure line (S-7.3).
- `RegimeLabelRun` is a frozen dataclass: `session` (D as `date`, or None when D could not be fixed), `outcomes` (one `SymphonyOutcome` per symphony seen), `failure` (an exception class name or None). `SymphonyOutcome` holds `symphony_id`, `name`, `label`, `reason`, `window_first`, `window_last`, `window_count` and `detail` (an HTTP status or an exception class name, never a message).

Run order inside `run_regime_label_job`:
1. `start = clock()`; `D = target_session(start)`. This fixes D when the job starts (Q-12, D-9).
2. `symphonies = load_symphonies()`; a raise logs the summary line with the class name and zero labelled, then returns (S-7.3).
3. For each symphony, parse `current_holdings` inside its own `try`. An absent key or an empty list gives `no_holdings` (S-5.3). A malformed entry gives `unexpected_error` (S-5.8) and adds no tickers to the fetch.
4. `end_bound = start - SIP_END_LAG`; `newest_eligible = market_calendar.most_recent_completed_session(end_bound)` (`market_calendar.py:170-185`); `sessions = window_sessions(D, newest_eligible)`.
5. One SIP fetch over the union of tickers (`fetch_sip_daily_closes`, below).
6. Per symphony, in its own `try`: compute the series and label (below). Then call `clock()`; if it is at or after D's 09:30 ET open, the reason is `after_open` and no write happens (S-1.10). Otherwise `database.save_regime_label(symphony_id, label, D.isoformat())` (`database.py:1717-1742`, parameterized upsert). A raise from the write gives `write_failed` (S-5.9).
7. One log line per symphony, then one summary line (AC-7).

Session arithmetic, built on the existing NYSE calendar (`market_calendar.is_trading_day` `:42-61`, `market_calendar.session_open` `:64-79`, `market_calendar.most_recent_completed_session` `:170-185`):
- `target_session(start)`: walk calendar days forward from `start`'s ET date and return the first `d` with `is_trading_day(d)` whose `datetime.combine(d, session_open(d), ET)` is after `start`. The walk is bounded by `_NEXT_SESSION_LOOKAHEAD_DAYS = 10`, the same bound as `market_calendar._COMPLETED_SESSION_LOOKBACK_DAYS` (`market_calendar.py:166`). This covers S-1.2 to S-1.5.
- `window_sessions(D, newest_eligible)`: the `REGIME_WINDOW_MAX_LOOKBACK_SESSIONS = 30` NYSE sessions strictly before D (D-8), plus the session before the oldest of them, whose close the first return needs. A session later than `newest_eligible` stays in the 30-session count but is never computable, because it has no completed daily close at the request's end bound (see Items Raised). For the 04:30 slot this never removes a session: the previous session closed hours earlier.
- No existing helper returns "next session after an instant" or "N sessions before a date". `advisors/tenure_review.py:491-499` has `_last_sessions`, but it is private to an advisory module and bounded by its own `CALENDAR_FLOOR`, so the job does not import it. Its walk shape is mirrored.

Series and label (pure functions, no I/O):
- A session s is computable when every holding has a SIP daily close on s and on the session before s. A ticker missing from a successful response, or a bar missing on a session, makes the session not computable. No zero is ever substituted (S-2.8, S-2.10, S-5.10; DC-2).
- `session_return(s) = sum(allocation_i * (close_i(s) / close_i(prev(s)) - 1))` over the stored holdings, in fractions. The weights are used as stored, as the replay does (`synthetic_history.py:414-427`). A present close that is not a finite positive number yields a NaN return for that session. The NaN stays in the series, so `classify_regime`'s own finite filter (`regime_classifier.py:126-130`, `:190`) returns None and the reason is `no_classifier_label` (S-5.6).
- The series is the `REGIME_WINDOW_SESSIONS` most recent computable sessions, oldest first, where `REGIME_WINDOW_SESSIONS` is imported as `regime_classifier.MIN_LABEL_SERIES_LENGTH` (`regime_classifier.py:63`), never a literal 20. Fewer than 20 computable sessions among the 30 gives `insufficient_history` (S-2.11, S-5.2).
- `label = regime_classifier.classify_regime(series)` (`regime_classifier.py:110-144`, consumed unchanged). None gives `no_classifier_label`. The job writes only the three strings the classifier returns (S-5.11).

SIP request (`fetch_sip_daily_closes(tickers, start, end) -> SipCloses`):
- new: both existing daily-bar fetchers swallow a failed batch and return a smaller result. `synthetic_history.fetch_bars` breaks out at `synthetic_history.py:348-353`; `alpha_bot_execution.fetch_alpaca_history` breaks out at `alpha_bot_execution.py:368-370`. Both hard-code `feed=iex` (`synthetic_history.py:318`, `alpha_bot_execution.py:346`). AC-2 needs `feed=sip`, and S-5.5 needs the failed batch to be observable.
- Adopted: `synthetic_history.get_alpaca_headers` (`synthetic_history.py:300-301`) and `synthetic_history.ALPACA_BASE_URL` (`:22`), imported lazily inside the function. The request is `GET {ALPACA_BASE_URL}/stocks/bars` with `params=` `symbols`, `timeframe=1Day`, `adjustment=split`, `feed=sip`, `limit=10000`, `start` (midnight ET of the oldest fetched session as RFC 3339 UTC), `end` and `page_token`. `start` is the only window anchor besides `end`, so no date-only `end` is ever sent (D-12).
- `end = (job start - SIP_END_LAG).strftime("%Y-%m-%dT%H:%M:%SZ")`, where `SIP_END_LAG = timedelta(minutes=20)`. The comment cites D-12: an end 20 minutes before the request returned the previous session's bar, and an end 5 minutes before returned 403. The end is fixed once at job start, so every later request is at least 20 minutes after it.
- Batches of `SIP_BATCH_SIZE = 30` symbols, the size both existing fetchers use (`synthetic_history.py:306`, `alpha_bot_execution.py:337`). Each page follows `next_page_token`, with `_PAGE_PAUSE_SECONDS = 0.35` between pages (`synthetic_history.py:365`).
- Each request: `timeout=REQUEST_TIMEOUT_SECONDS (30.0)`, `allow_redirects=False`. `MAX_ATTEMPTS = 3` on 429, 500, 502, 503, 504 and transport errors, with exponential backoff from 1 s capped at `RETRY_AFTER_CAP_SECONDS = 30.0` and an integer `Retry-After` clamped into `[0, 30]`. Any other non-200 status (403 included) fails at once. These constants and the shape mirror `advisors/composer_series_client.py:82-107` and `:263-284`, which are private to that module; the module also imports `alpha_bot_execution` at its top (`advisors/composer_series_client.py:71`), so importing its helpers would put the engine module in the job's import graph and in the reach of the no-trade scan. One `timing_log.emit("alpaca", "GET", "/v2/stocks/bars", status=..., ms=...)` per attempt, the seam `advisors/composer_series_client.py:287-290` uses. Ids, symbols and bodies are never logged.
- Any page of a batch failing after its attempts fails the whole batch. Every ticker in it is marked failed with the status or the exception class name, whatever earlier pages returned (S-5.5, S-5.12).
- Bar dates come from parsing `t` (RFC 3339) and converting to the ET date. Only dates in the fetched session set are kept, so a bar dated D or later never enters (S-2.4).

Logging: `logging.getLogger("alphabot.regime_label")`, a child of the daemon's `alphabot` logger (`app.py:619-623`), so its records reach the daemon log file through propagation.
- Per symphony, INFO: `regime_label symphony=<hash> name=<name> session=<D> label=<label> window=<first>..<last> sessions=<n>` or `... reason=<code>` plus `status=<int>` or `error=<ClassName>` for `price_fetch_failed`, `write_failed` and `unexpected_error`. The name is written with `%r`, so a newline in a display name cannot split the line.
- Summary, INFO: `regime_label summary session=<D> seen=<n> labelled=<n> <reason>=<count>...`. On a run-level failure the summary carries `failed=<ClassName>`.
- Reason codes are module constants: `insufficient_history`, `no_holdings`, `price_fetch_failed`, `no_classifier_label`, `write_failed`, `after_open`, `unexpected_error` (S-7.2).
- No API key, header, body or exception message is ever formatted into a line (S-7.4).

### Daemon slot (`app.py`)

Adopted pattern: the two-function wrapper of `_incubation_tick_worker` / `_run_incubation_tick` (`app.py:1911-1935`) and `_start_model_warmup` (`app.py:1906-1908`).
- `_regime_label_job_worker()`: lazy `import regime_label_job` (CC-2), then `regime_label_job.run_regime_label_job(_regime_label_symphony_entries)`. It catches `Exception` and logs only `type(exc).__name__` at ERROR on `_daemon_log`.
- `_regime_label_symphony_entries()`: `{k: v for k, v in database.load_state(read_only=True).items() if _is_symphony_state_entry(v)}`. It uses the read-only load (`database.py:323-332`) and the shared shape check (`app.py:6142-6150`), so the five top-level metadata keys never reach the job (S-1.8).
- `_run_regime_label_tick()`: `threading.Thread(target=_regime_label_job_worker, daemon=True, name="regime-label-job").start()`, then returns at once (S-6.7).
- Registration in `run_scheduler` (`app.py:2735-2765`), after the 04:15 line at `:2762`: `_guard(schedule.every().day.at("04:30").do(_run_regime_label_tick), "04:30 regime label job")` (Q-3, NB-12). `_guard` (`app.py:1468-1475`) keeps a raising slot from stopping the scheduler.
- There is no lock and no run-now route (NB-1). The slot runs once a day, and a repeated run is idempotent by the upsert (S-1.6).

No migration (the plan's Migrations Required: none; migration 027's table at `migrations/027_regime_label_cache.sql:13-18` carries every field). No new configuration key (G-21). No route, template or JS change. `math_engine.py`, `regime_classifier.py`, `autotuner.py`, `synthetic_history.py` and migration 027 are byte-unchanged (S-6.4).

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-1, R-11, R-12, R-17, R-28 | new: `regime_label_job.run_regime_label_job` and `target_session`, writing through `database.py:1717-1742`; slot registered in `app.py:2735-2762`; no producer exists (`git grep save_regime_label` outside tests finds only `database.py:1717`) | new `tests/regime_label_job/test_job_run.py` and `test_target_session.py`; slot integration extends `tests/app/test_scheduler_survival.py:111-148`; fixtures: captured SIP pages under `tests/fixtures/alpaca/stock_bars_captured/` (PM `/api-fixture` capture), one entry of `tests/fixtures/prod_droplet/bot_state_positions.json` (captured, `tests/fixtures/prod_droplet/capture_from_droplet.py`); fixed `clock` injection; HTTP faked by the scripted-`requests.get` pattern of `tests/advisors/test_composer_series_client.py:99-145` | C2 |
| AC-2 | R-2, R-3, R-4, R-6 | new: `regime_label_job.window_sessions`, `session_return` and `fetch_sip_daily_closes`; classifier consumed unchanged at `regime_classifier.py:110-144`; replay compared at `autotuner.py:1630-1660` | new `tests/regime_label_job/test_label_computation.py`, `test_sip_fetch.py`, `test_replay_parity.py`; fixtures: derived SIP pages under `tests/fixtures/alpaca/stock_bars_derived/` built from `tests/fixtures/math/regime_classifier/*_basic.json` in the captured shape, with a validator test against the captured record; real `synthetic_history.build_replay_day` as in `tests/autotuner/test_ac6_bar_level_replay_production_parity.py:191` | C2 |
| AC-3 | R-5, R-6, R-15, R-24, R-27, R-28 | `alpha_bot_execution.py:1526-1529` (one keyword argument) and `database.py:1745-1797` (`session_date` keyword) | new `tests/execution/test_regime_label_session_read.py` driving real `alpha_bot_execution.main()` cycles through the harness at `tests/execution/test_main_pipeline.py:154`, with the mocked `database.get_cached_regime_label` wired to the real accessor over the per-test temp DB (`tests/conftest.py`); rows written by real `database.save_regime_label`; live leg uses `_configure_trigger_scenario` at `tests/execution/test_main_live_execution.py:116` with `alpha_bot_execution.requests.post` faked | C1 |
| AC-4 | R-8, R-13, R-14, R-18, R-27 | `database.py:1745-1797` (`session_date` equality on the one-row read at `:1766-1768`) and `alpha_bot_execution.py:1526-1529` | accessor cases extend `tests/execution/test_regime_cache_isolation.py:354`; engine cases in new `tests/execution/test_regime_label_session_read.py` on the harness at `tests/execution/test_main_pipeline.py:154` | C1 |
| AC-5 | R-8, R-9, R-10 | new: per-symphony `try` and reason codes in `regime_label_job.run_regime_label_job`; failed-batch marking in `regime_label_job.fetch_sip_daily_closes` (the existing fetchers swallow failures, `synthetic_history.py:348-353`) | new `tests/regime_label_job/test_job_run.py` and `test_sip_fetch.py`; captured 403 and missing-symbol records under `tests/fixtures/alpaca/stock_bars_captured/`; S-5.7 in new `tests/execution/test_regime_label_session_read.py` | C1 |
| AC-6 | R-7, R-19, R-20, R-24, R-25 | `app.py:1906-1935` wrapper pattern for the new `_run_regime_label_tick`; engine read stays the single call at `alpha_bot_execution.py:1526-1529`; byte pins `tests/test_mdd_window_truth_zero_diff_guard.py:72-77`, `tests/engine/test_close_snapshot_builder_scope.py:28` | engine side: new `tests/execution/test_regime_label_session_read.py`, existing `tests/execution/test_regime_cache_isolation.py:55` and `:99`, `tests/autotuner/test_ac4_regime_conditional_exit_ticks.py:165`, new `tests/test_regime_label_protected_files.py`; job side: new `tests/security/test_regime_label_job_no_trade_boundary.py` on the walker of `tests/security/test_model_warmup_no_trade_boundary.py:108-161`, new `tests/app/test_regime_label_scheduling.py` on `tests/app/test_scheduler_survival.py:219-250` | C1 |
| AC-7 | R-16 | new: `alphabot.regime_label` logger lines in `regime_label_job.run_regime_label_job`, propagating to the handler at `app.py:619-623` | new `tests/regime_label_job/test_job_logging.py` with `caplog.set_level(logging.INFO, logger="alphabot")` | C2 |

AC-5 and AC-6 span both cycles: the Cycle column names the cycle that lands the engine half, and the Per-Cycle Test Plan assigns each scenario to its own cycle (S-5.7, S-6.1 to S-6.4 in C1; the rest in C2).

## Execution Model

Two cycles run at once, each a Toxic Pair on its own branch and worktree, because neither reads the other's code. Their only shared contract is the existing one: `as_of_date` is an ISO `YYYY-MM-DD` string (`migrations/027_regime_label_cache.sql:16`) written through `database.save_regime_label` (`database.py:1717-1742`). C1 reads it with string equality against `current_et.strftime("%Y-%m-%d")`; C2 writes `D.isoformat()`.

- C1, engine read and accessor (AC-3, AC-4, S-5.7, S-6.1 to S-6.4):
  - RED: `quant-test-writer`, because this is an engine and math-adjacent change on a Sensitive (money) plan and the project rule adds the adversarial author.
  - GREEN: `implementer`. The diff is an accessor keyword plus one call argument, so `risk-engine-specialist` is not dispatched as a builder.
  - The RED author is the closer. After GREEN it re-pins the two engine byte pins in a test-only commit, on the PM's recorded word (brief and plan Q-4). The new scope pin (S-6.4) must already hold at RED, because it hashes the engine with the regime-read statement cut out.
- C2, the job and its slot (AC-1, AC-2, AC-5 except S-5.7, AC-6 S-6.5 to S-6.8, AC-7). Two RED batches share one implementer:
  - Batch A, `quant-test-writer`: label computation, SIP fetch semantics, date math and replay parity. These carry the money-relevant defect classes DC-2 to DC-5 and DC-12.
  - Batch B, `test-writer`: the slot, run orchestration, logging, the scheduler timing test and the boundary scans.
  - GREEN: one `implementer` for both batches. The batch A author is the closer and sends the one cycle-complete.
- Fixture precondition, PM before C2's dispatch: the C2 brief names a captured fixture folder that must exist. The PM runs `/api-fixture alpaca` read-only, after market close, on the droplet's credentials, and commits `tests/fixtures/alpaca/stock_bars_captured/` with `PROVENANCE.md` (host, UTC capture time, request parameters, one record per case). The cases:
  - a `feed=sip` `1Day` page;
  - a page carrying `next_page_token`;
  - the HTTP 403 recent-SIP refusal (end 5 minutes before the request);
  - a 200 response missing a requested symbol;
  - a `feed=iex` `1Min` page and a `feed=iex` `1Day` page, for S-2.12.

  Workers cannot reach the credentials. The derived pages are written by batch A's author, from the captured shape plus the classifier golden series, each listed in `tests/fixtures/alpaca/stock_bars_derived/PROVENANCE.md` the way `tests/fixtures/composer/series_derived/PROVENANCE.md` lists its derivations.
- Review: one round at the end, dispatched by the PM when both cycle-completes are in, in parallel. One `qa` and one `code-reviewer` each serve both heads; one `qa` in hidden mode (Sensitive plan); `quant-code-reviewer` once per head (both heads touch the math layer's input or its consumer); `pipelines-expert` on C2's head (scheduler slot and data pipeline). No UI head, so no `ux-expert` or capturer. No `*-integration-auditor`: the diff touches one of UI, API and pipelines (the engine is not one of the three) and no cross-repo contract. One `doc-gen` at the slice tail, after every reviewer passes both heads.
- Isolation follows the project's environment rules: each teammate works in its own worktree off the base below, never in the primary checkout. Kickoffs mandate `cd` plus a `git rev-parse` check, and copy `.env` into each worktree. Pytest runs `-n0` on named files only, never the full tree and never `-o addopts=""`; CI is the full gate.

Team size: 12 = 2 quant-test-writer + 1 test-writer + 2 implementer + 1 qa + 1 qa (hidden mode) + 1 code-reviewer + 2 quant-code-reviewer + 1 pipelines-expert + 1 doc-gen

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 |  | feat/regime-label-producer@ff161d72 |
| C2 |  | feat/regime-label-producer@ff161d72 |

C2's dispatch waits only for the PM's fixture capture commit on `feat/regime-label-producer`, not for C1. Its branch base is the capture commit when that commit lands first; otherwise the PM cherry-picks the capture into C2's branch before RED.

## Per-Cycle Detail

### C1: engine read and accessor
- Satisfies: AC-3, AC-4, and the engine halves of AC-5 (S-5.7) and AC-6 (S-6.1 to S-6.4).
- Files modified:
  - `database.py:1745-1797` (`session_date` keyword-only parameter and its docstring).
  - `alpha_bot_execution.py:1526-1529` (pass `session_date=current_date_str`; nothing else in the file).
  - `tests/test_mdd_window_truth_zero_diff_guard.py:72-77` (re-pin the `alpha_bot_execution.py` hash only; `math_engine.py`'s hash stays).
  - `tests/engine/test_close_snapshot_builder_scope.py:28` (re-pin `_PINNED_HASH_WITHOUT_BUILDER`).
  - `tests/execution/test_regime_cache_isolation.py` (new accessor cases appended after `:368`; items 1 to 11 unchanged).
- Files added:
  - `tests/execution/test_regime_label_session_read.py`.
  - `tests/test_regime_label_protected_files.py`: LF-normalized hashes of `math_engine.py`, `regime_classifier.py`, `autotuner.py`, `synthetic_history.py` and `migrations/027_regime_label_cache.sql`, plus the engine hashed with the `_regime_exit_ticks` assignment cut out by AST, all taken at the ref. This follows the `hash_without_builder` pattern at `tests/engine/test_close_snapshot_builder_scope.py:49-55`.
- Primitives imported: none new. The engine already imports `database` (`alpha_bot_execution.py:26`).
- First production use: the `session_date` keyword's only caller is `alpha_bot_execution.py:1527`.

### C2: regime label job and daemon slot
- Satisfies: AC-1, AC-2, AC-5 (except S-5.7), AC-6 (S-6.5 to S-6.8), AC-7.
- Files added:
  - `regime_label_job.py`.
  - `tests/regime_label_job/__init__.py`, `test_target_session.py`, `test_label_computation.py`, `test_sip_fetch.py`, `test_replay_parity.py`, `test_job_run.py`, `test_job_logging.py`, `test_fixture_shapes.py` (the derived-page validator).
  - `tests/app/test_regime_label_scheduling.py`.
  - `tests/security/test_regime_label_job_no_trade_boundary.py`.
  - `tests/fixtures/alpaca/stock_bars_derived/` with `PROVENANCE.md`.
- Files modified:
  - `app.py`: new `_regime_label_job_worker`, `_regime_label_symphony_entries` and `_run_regime_label_tick` beside `app.py:1906-1935`; one registration line after `app.py:2762`.
  - `tests/app/test_scheduler_survival.py:112-120`: `_EXPECTED` gains `("_run_regime_label_tick", "days", dtime(4, 30))`. This is a round-1 RED row: the test asserts the total slot count (`:145-148`), so the new slot fails it until listed. The plan's Contract Changes do not list this consumer; the design carries it.
- Primitives imported by the new module: `database.save_regime_label` (`database.py:1717`); `regime_classifier.classify_regime` and `MIN_LABEL_SERIES_LENGTH` (`regime_classifier.py:110`, `:63`); `market_calendar.is_trading_day`, `session_open` and `most_recent_completed_session` (`market_calendar.py:42`, `:64`, `:170`); `synthetic_history.get_alpaca_headers` and `ALPACA_BASE_URL` (`synthetic_history.py:300`, `:22`, lazy); `timing_log.emit` (`timing_log.py:78`). By `app.py`: `database.load_state(read_only=True)` (`database.py:323`) and `_is_symphony_state_entry` (`app.py:6142`).
- First production use: the first production caller of `database.save_regime_label`; the first `feed=sip` request in the repository.
- Write census for `regime_label_cache`: the job is the only writer, and the engine only reads. `git grep` over production code finds no other caller of `save_regime_label` and no other SQL naming `regime_label_cache` outside `database.py:1731-1738` and `:1767`.

## Per-Cycle Test Plan

Census of every entry point that writes or reads the entity this slice changes (`regime_label_cache`), each a round-1 RED row:

| Entry point | Rules |
|---|---|
| Engine cycle, dry run (`LIVE_EXECUTION` false or `live_mode` false) | AC-3, AC-4 (reader: `alpha_bot_execution.py:1526-1529`) |
| Engine cycle, live (`LIVE_EXECUTION` true and `live_mode` true) | AC-3, AC-4 (reader: `alpha_bot_execution.py:1526-1529`) |
| daily regime label slot | AC-1, AC-2, AC-5, AC-6, AC-7 (sole writer: `app.py:2735-2762` registration, new `_run_regime_label_tick`) |

Scope:
- C1 is unit tests (accessor) and engine integration through real `alpha_bot_execution.main()` cycles with Composer and Alpaca faked, against a temp state DB.
- C2 is unit tests (pure series, label and date functions), integration through `run_regime_label_job` and the slot callable with `requests.get` faked by a scripted stand-in, AST scans, and one scheduler timing test.
- No e2e, screenshot or live-backend test in either cycle; the live check is the PM's (Merge Criteria).
- Expected values come from the fixture closes and from calling the real `classify_regime` and `apply_regime_exit_adjustment`, never from literals copied from a run.

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-3.1 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (no module drives multi-cycle `main()` with a real regime row; harness tests/execution/test_main_pipeline.py:154) | rows via real `database.save_regime_label`; Composer payload from `_make_symphony_payload` (harness builder, tests/execution/test_main_pipeline.py) |
| S-3.2 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | as S-3.1, label mean-reverting |
| S-3.3 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | as S-3.1, label high-vol |
| S-3.4 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | empty `regime_label_cache` in the conftest temp DB |
| S-3.5 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | rows dated D with `Trending`, empty string and `sideways`, one case each |
| S-3.6 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | as S-3.2 plus one above-stop tick after 4 qualifying ticks |
| S-3.7 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | temp DB without migration 027's table; and `database.get_ro_connection` patched to raise `sqlite3.OperationalError("database is locked")` |
| S-3.8 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | same seeded state run with and without a trending row; take-profit and VWAP paths from the harness payload builders |
| S-3.9 | AC-3 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | recorded multi-cycle sequence through the harness, run once with the accessor wired to an empty temp DB and once with the accessor returning None (the ref behaviour); decisions compared |
| S-3.10 | AC-3 | engine integration | Engine cycle, dry run (`LIVE_EXECUTION` false or `live_mode` false) | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | as S-3.1, parametrized over `LIVE_EXECUTION` false and `live_mode` false |
| S-3.11 | AC-3 | engine integration | Engine cycle, live (`LIVE_EXECUTION` true and `live_mode` true) | tests/execution/test_main_live_execution.py:116 | `_configure_trigger_scenario` seed; `alpha_bot_execution.requests.post` faked, asserting the `/go-to-cash` POST (`alpha_bot_execution.py:278-284`) after tick 2 and not before |
| S-4.1 | AC-4 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | row (S, mean-reverting, previous trading day) |
| S-4.2 | AC-4 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | two symphonies, rows dated the previous trading day only |
| S-4.3 | AC-4 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | no rows dated D; fixed ET clock before then after 09:30 via `get_current_et` patch |
| S-4.4 | AC-4 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | row dated the Friday before; clock on the Monday |
| S-4.5 | AC-4 | unit and engine integration | Engine cycle, dry run | tests/execution/test_regime_cache_isolation.py:354 | rows dated the next trading day and a later date |
| S-4.6 | AC-4 | unit | Engine cycle, dry run | tests/execution/test_regime_cache_isolation.py:354 | rows with `as_of_date` empty and `"2026/11/09"` (direct SQL insert, since `save_regime_label` accepts any string) |
| S-4.7 | AC-4 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | symphony T present in the Composer payload, absent from the cache |
| S-4.8 | AC-4 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | row for R dated D; R absent from the Composer payload; accessor call log shows no read keyed R |
| S-4.9 | AC-4 | unit | Engine cycle, dry run | tests/execution/test_regime_cache_isolation.py:354 | writer thread alternating (A, X) and (B, Y) upserts while reads with `session_date=X` run; every result is A or None |
| S-4.10 | AC-4 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | spy on `database.get_ro_connection` and `database.save_regime_label`; the read uses the read-only path and no write occurs |
| S-4.11 | AC-4 | engine integration | Engine cycle, dry run (`LIVE_EXECUTION` false or `live_mode` false) | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | as S-4.1, parametrized over both dry-run states |
| S-4.12 | AC-4 | engine integration | Engine cycle, live (`LIVE_EXECUTION` true and `live_mode` true) | tests/execution/test_main_live_execution.py:116 | as S-4.1; `/go-to-cash` POST after tick 3 and not before |
| S-5.7 | AC-5 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | row (S, mean-reverting, previous trading day), no row dated D |
| S-6.1 | AC-6 | engine integration | Engine cycle, dry run | new: tests/execution/test_regime_label_session_read.py (as S-3.1) | row dated D; accessor call count per symphony per cycle; `regime_classifier.classify_regime` patched to raise; harness fetch mocks' call lists compared with and without the row |
| S-6.2 | AC-6 | structural scan | Engine cycle, dry run | tests/execution/test_regime_cache_isolation.py:55 | engine and math_engine sources at HEAD (existing tests 1 and 2, kept) |
| S-6.3 | AC-6 | structural scan | Engine cycle, dry run | tests/autotuner/test_ac4_regime_conditional_exit_ticks.py:165 | autotuner source at HEAD (existing test, kept) |
| S-6.4 | AC-6 | content check | Engine cycle, dry run | new: tests/test_regime_label_protected_files.py (no test pins regime_classifier.py, autotuner.py, synthetic_history.py or migration 027; pattern tests/engine/test_close_snapshot_builder_scope.py:49-55) | LF-normalized hashes taken at AlphaBotPM@ff161d72; the engine is hashed with the `_regime_exit_ticks` assignment cut out by AST |

### C2
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | slot integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (no module tests the job; scripted HTTP pattern tests/advisors/test_composer_series_client.py:99-145) | two entries from tests/fixtures/prod_droplet/bot_state_positions.json (captured, capture_from_droplet.py); derived SIP pages with 21 computable sessions; one case through `app._run_regime_label_tick` with the thread joined |
| S-1.2 | AC-1 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_target_session.py (pattern tests/market_calendar/test_most_recent_completed_session.py:51) | fixed UTC instants on a Saturday and a Sunday, and a weekend before an NYSE Monday holiday; expected D from `market_calendar.is_trading_day` |
| S-1.3 | AC-1 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_target_session.py (as S-1.2) | instant the night before a weekday NYSE holiday; window dates checked against `market_calendar.is_trading_day` |
| S-1.4 | AC-1 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_target_session.py (as S-1.2) | 04:30 UTC on an EST date and an EDT date |
| S-1.5 | AC-1 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_target_session.py (as S-1.2) | instant 10:00 ET on trading day D |
| S-1.6 | AC-1 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | as S-1.1, two runs; row count, labels and `updated_at` order read by SQL |
| S-1.7 | AC-1 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | rows pre-seeded dated the previous trading day |
| S-1.8 | AC-1 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | captured entries plus the five metadata keys, loaded through `app._regime_label_symphony_entries` over a saved state |
| S-1.9 | AC-1 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | temp DB dump of every table but `regime_label_cache` and a temp post-mortems directory compared before and after |
| S-1.10 | AC-1 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | injected `clock` that returns 09:20 ET at start and before A's write, then 09:31 ET before B's write |
| S-2.1 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (no test of a SIP close-to-close series exists) | derived SIP pages, two holdings, 21 computable sessions; expected series computed in the test from the fixture closes, tolerance 1e-12 |
| S-2.2 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | derived pages whose returns are `trending_basic.json`, `mean_reverting_basic.json`, `high_vol_stressed_basic.json` (tests/fixtures/math/regime_classifier/) |
| S-2.3 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | derived pages with sample std 0.02 as fractions |
| S-2.4 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_sip_fetch.py (scripted HTTP as tests/advisors/test_composer_series_client.py:99-145) | derived page carrying a bar dated D and a bar after D |
| S-2.5 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | 25 computable sessions; a second page variant with the 4 oldest closes changed |
| S-2.6 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | two holdings at 0.6 and 0.4 with distinct closes |
| S-2.7 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | captured entry whose `current_holdings` differ from an earlier set; every window session uses the stored set |
| S-2.8 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_sip_fetch.py (as S-2.4) | captured missing-symbol 200 record |
| S-2.9 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_replay_parity.py (pattern tests/autotuner/test_ac6_bar_level_replay_production_parity.py:191) | S-2.12's history; `_replay_resolve_regime_exit_ticks` per date before and after a job run in the same process are equal; existing tests/autotuner/test_ac4_regime_conditional_exit_ticks.py:206 stays green |
| S-2.10 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | derived page omitting one holding's bar on one session, and on the session before it |
| S-2.11 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | 19 computable sessions in the 30, more further back |
| S-2.12 | AC-2 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_replay_parity.py (as S-2.9) | derived `feed=iex` 1Min and 1Day pages and SIP 1Day pages from the captured records, equal closes, last 1Min bar at the same position closing at the daily close; history built by real `synthetic_history.build_replay_day` |
| S-5.1 | AC-5 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | three entries; scripted HTTP failing B's batch after its attempts (`time.sleep` patched) |
| S-5.2 | AC-5 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | 19 and 20 computable sessions within the 30 |
| S-5.3 | AC-5 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | captured entry with `current_holdings` emptied, and with the key removed |
| S-5.4 | AC-5 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | scripted HTTP raising `requests.ConnectionError` on every attempt |
| S-5.5 | AC-5 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_sip_fetch.py (as S-2.4) | 31 tickers so two batches; second batch transport error and, separately, HTTP 500 after its attempts; second case the missing-symbol record |
| S-5.6 | AC-5 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | derived page with one close set to NaN inside the 20 sessions |
| S-5.8 | AC-5 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | entry with a holding missing `ticker` |
| S-5.9 | AC-5 | integration (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_run.py (as S-1.1) | `database.save_regime_label` patched to raise `sqlite3.OperationalError` for the first symphony only |
| S-5.10 | AC-5 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | 200 responses with no bars for any of S's tickers |
| S-5.11 | AC-5 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_label_computation.py (as S-2.1) | every failure case above run in one parametrized sweep; rows read back by SQL |
| S-5.12 | AC-5 | unit (batch A) | daily regime label slot | new: tests/regime_label_job/test_sip_fetch.py (as S-2.4) | captured HTTP 403 recent-SIP record; plus 401 and 404 statuses |
| S-6.5 | AC-6 | structural scan (batch B) | daily regime label slot | new: tests/security/test_regime_label_job_no_trade_boundary.py (walker of tests/security/test_model_warmup_no_trade_boundary.py:108-161; route functions found by `@app.` decorator AST scan) | `app.py` and repository module sources at HEAD |
| S-6.6 | AC-6 | structural scan (batch B) | daily regime label slot | new: tests/security/test_regime_label_job_no_trade_boundary.py (plant pattern tests/security/test_model_warmup_no_trade_boundary.py:304-313) | sources with a forbidden call planted in `_regime_label_job_worker`, in `run_regime_label_job` and in `fetch_sip_daily_closes` |
| S-6.7 | AC-6 | scheduler timing (batch B) | daily regime label slot | new: tests/app/test_regime_label_scheduling.py (pattern tests/app/test_scheduler_survival.py:219-250) | job fetch held on an event for the test's simulated 3 minutes; `stub_engine`, `join_engine_threads` from tests/app/conftest.py |
| S-6.8 | AC-6 | unit (batch B) | daily regime label slot | new: tests/app/test_regime_label_scheduling.py (as S-6.7) | 04:30 host time mapped through UTC and America/New_York on an EST date and an EDT date; slot time read from the registered job (tests/app/test_scheduler_survival.py:111-148) |
| S-7.1 | AC-7 | log capture (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_logging.py (caplog on `alphabot`) | as S-1.1 plus one entry with insufficient history |
| S-7.2 | AC-7 | log capture (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_logging.py (as S-7.1) | one entry per reason code, each forced by the S-5 seam named above |
| S-7.3 | AC-7 | log capture (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_logging.py (as S-7.1) | `load_symphonies` raising `sqlite3.OperationalError` |
| S-7.4 | AC-7 | log capture (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_logging.py (as S-7.1) | sentinel key, header and body strings planted in the env, the response body and an exception message; every record's message scanned for them |
| S-7.5 | AC-7 | log capture (batch B) | daily regime label slot | new: tests/regime_label_job/test_job_logging.py (as S-7.1) | `load_symphonies` returning `{}` |

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | Re-pinning the two engine byte pins (`tests/test_mdd_window_truth_zero_diff_guard.py:72-77`, `tests/engine/test_close_snapshot_builder_scope.py:28`) hides an engine change outside the regime read | Medium | High (money path) | S-6.4's scope pin is written at RED against the ref with the `_regime_exit_ticks` statement cut out, so it holds before and after GREEN and fails on any other engine byte; the closer re-pins only after it passes |
| C1 | The engine harness patches the whole `database` module (`tests/execution/test_main_pipeline.py:154-196`), so a test can pass on a mocked accessor | High if unaddressed | High | `get_cached_regime_label` is wired by `side_effect` to the real accessor over the conftest temp DB, and rows are written by the real `save_regime_label`; S-6.1 counts real reads |
| C1 | The rule keys on the wrong identifier (DC-9): the engine reads with `sym["id"]` (`alpha_bot_execution.py:1239`), not `symphony_id` | Low | High | S-3.1 seeds the row under the harness's `id` value and asserts the 2-tick fire; a row under the `symphony_id` value would leave 3 |
| C1 | `current_date_str` and the job's D disagree in format | Low | High | Both use ISO `YYYY-MM-DD` (`alpha_bot_execution.py:716`, `date.isoformat()`), the format migration 027 documents (`migrations/027_regime_label_cache.sql:16`); C1's tests write ISO strings through `save_regime_label`, C2's read the stored string by SQL |
| C2 | The SIP request is refused (HTTP 403 for an end inside the last 15 minutes or a date-only end equal to today, D-12) | Low after D-12 | Feature inert (fails closed to 3 ticks) | `end` is an explicit UTC timestamp 20 minutes before job start, never date-only; S-5.12 pins the fail-closed path; the first live summary line shows it |
| C2 | A failed batch is read as missing bars, so a symphony is labelled on partial data (DC-7; both existing fetchers break silently, `synthetic_history.py:348-353`, `alpha_bot_execution.py:368-370`) | High if an existing fetcher were reused | High | New fetch marks every ticker of a failed batch failed; S-5.5 |
| C2 | A missing close is filled with zero, giving a constant series that classifies mean-reverting (5 ticks) (DC-2; `regime_classifier.py:138-144`) | Medium (the replay uses `.get("return", 0.0)`, `autotuner.py:1653`) | High | No default fill anywhere in the series path; S-2.8, S-2.10, S-5.10 |
| C2 | Percent and fraction confused (DC-4; the replay divides by `RETURN_PCT_TO_FRACTION`, `autotuner.py:727`) | Medium | High (high-vol never or always) | Returns are computed as fractions directly from closes, never via percent; S-2.3 |
| C2 | An in-progress session's partial daily bar enters the window when the job starts after an open | Low (the slot is 04:30 host) | Medium | Sessions later than `most_recent_completed_session(end_bound)` (`market_calendar.py:170-185`) are never computable; raised to the PM for confirmation |
| C2 | Host timezone differs from the assumed UTC (`app.py:7078` calls the 03:45 slot UTC; unconfirmed, plan Known-Unconfirmed 2) | Medium | Low (04:30 is off-hours in UTC and in ET) | D is computed in ET from an aware instant; S-1.4 and S-6.8 cover both hosts; the PM's `timedatectl` read before deploy |
| C2 | The retry constants mirror `advisors/composer_series_client.py:82-107` instead of importing them; the two copies can drift | Medium | Low | Importing them would pull `alpha_bot_execution` in (`advisors/composer_series_client.py:71`); the job's constants carry a source comment naming the sibling |
| C2 | `tests/app/test_scheduler_survival.py:145-148` fails on any unknown slot | Certain | Low | Round-1 RED row extends `_EXPECTED` |
| C2 | The no-trade walker flags a fragment reached through an adopted helper (`_FORBIDDEN_FRAGMENTS`, `tests/security/test_model_warmup_no_trade_boundary.py:33-51` includes `alpha_bot_execution` and `live_execution`) | Low | Low | The job imports only `database`, `regime_classifier`, `market_calendar`, `synthetic_history` (two names) and `timing_log`; none of the reached function bodies carries a fragment; S-6.6 proves it |

## Cycle-Close Criteria

- Every scenario assigned to the cycle has a row in `records/coverage.json` under `.claude/review/<HEAD-sha12>/`: `tested` with its test file and name, or held with its reason. `plan-trace check-coverage` runs clean against the plan and this approach.
- RED was committed before GREEN, and each RED test was observed failing for the stated reason.
- The cycle's named test files pass under `pytest -n0`, together with these existing files:
  - C1: `tests/execution/test_regime_cache_isolation.py`, `tests/execution/test_main_pipeline.py`, `tests/execution/test_main_live_execution.py`, `tests/execution/test_main_insufficient_mc_failsafe.py`, `tests/engine/test_close_snapshot_builder_scope.py`, `tests/test_mdd_window_truth_zero_diff_guard.py`, `tests/autotuner/test_ac4_regime_conditional_exit_ticks.py`, `tests/math_engine/test_regime_exit_adjustment.py`.
  - C2: `tests/app/test_scheduler_survival.py`, `tests/app/test_incubation_scheduling.py`, `tests/security/test_model_warmup_no_trade_boundary.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/alpaca/test_feed_pinning.py`, `tests/autotuner/test_ac4_regime_conditional_exit_ticks.py`.
- `ruff format --check` and `ruff check` are clean (line length 100).
- C1 only:
  - `git diff ff161d72 -- alpha_bot_execution.py` touches only the statement at `:1526-1529`.
  - The two re-pins sit in a test-only commit after GREEN, and S-6.4's scope pin is unchanged from RED.
- C2 only:
  - `git diff ff161d72 -- alpha_bot_execution.py math_engine.py regime_classifier.py autotuner.py synthetic_history.py migrations/` is empty.
  - Every new constant carries a source comment.
  - The captured and derived fixture folders each carry `PROVENANCE.md`, and the derived-page validator passes.
- The sufficiency sweep greps for the DC-1 to DC-12 sibling queries named in the plan's Defect Classes table and records the result in the coverage map.

## Merge Criteria

Delivery: no PR (project mandate). The PM merges both cycle branches into `feat/regime-label-producer`, pushes the head to an `assembly/**` branch for CI, and fast-forwards `main` only when CI is green on that exact SHA and every reviewer verdict on both heads reads PASS.

The assembled head must pass:
- CI full gate green on the exact assembled SHA: ruff format, ruff check, full pytest under CI's own worker count.
- No migration: the migration-count guard in `tests/test_mdd_window_truth_zero_diff_guard.py:135-160` still passes; `migrations/` is unchanged.
- Persistence roundtrip: on the assembled tree, the PM runs one `-n0` check over the C1 and C2 test files that touch `regime_label_cache`. A row the job writes for D must be the row the accessor returns for `session_date=D` and refuses for any other date. Both halves pin the ISO format, and this run proves the join on one tree.
- Deploy only after market close, after a closed-market check (`market_calendar.get_market_state` reads `closed_frozen` and the day is checked against the NYSE holiday calendar).
- Before the first slot run: the PM reads the droplet host timezone (`timedatectl`, read-only) and records it.
- PM live functional check after the first 04:30 run, against the droplet:
  - `SELECT symphony_id, label, as_of_date, updated_at FROM regime_label_cache` (read-only) shows one row per labelled symphony dated the next session.
  - The daemon log holds one line per symphony and the summary line, and the summary's labelled count equals the row count.
  - During that session, the engine log shows a labelled symphony's trailing-stop confirmation at its label's count. Next-morning open proof: the engine's first cycles after 09:30 ET complete with no error, and the cache rows are unchanged during the session.
- Backlog lines at merge, per the plan's Follow-Ups (NB-13, NB-14, NB-7 and Q-7, NB-5, Known-Unconfirmed 10, Known-Unconfirmed 1 and D-5).

## Items Raised to the PM

1. The census entry point "daily regime label slot" is not a column of the plan's Rule-by-Entry-Point Matrix. The matrix's columns are the engine's two execution states only. The plan's own matrix preamble names this slot as the job's single entry point, and S-6.5 proves no route reaches it. The design carries it as the sole writer of `regime_label_cache`, with AC-1, AC-2, AC-5, AC-6 and AC-7 tested through it in C2. Answered [PM-ASSUMED]: no extra matrix column is owed, because the 04:30 slot is not a user-facing write path; the census entry stays in Per-Cycle Test Plan.
2. The plan does not say whether a session still in progress at the request's end bound has a SIP daily close. Alpaca returns a partial daily bar for a session in progress. The design treats such a session as not computable: only sessions at or before `market_calendar.most_recent_completed_session` of the job start minus 20 minutes count. The case arises only when the job starts after an open, which the 04:30 slot never does (S-1.5 is the only scenario that reaches it). A partial bar is not a close-to-close return. Answered [PM-ASSUMED]: confirmed; a session not completed by the SIP request's end bound (job start minus 20 minutes) never counts, and no partial bar is used.
3. The design keeps a present but non-finite or non-positive close in the series as a NaN return, so `classify_regime` returns None and the reason is `no_classifier_label`. That is the outcome S-5.6 states. The alternative reading, treating such a close as missing so the window reaches further back, would contradict S-5.6. Answered [PM-ASSUMED]: accepted; a non-finite or non-positive close leaves NaN in the series, `classify_regime` returns None, and the reason is `no_classifier_label`.
4. Answered [PM-ASSUMED]: the PM accepts the Q-4 choice (the engine call site passes the keyword-only `session_date`), and the PM captures the Alpaca fixtures listed in Execution Model via `/api-fixture` before C2 dispatches.
