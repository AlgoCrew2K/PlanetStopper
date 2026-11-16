# Approach: Flag dead tickers in live symphony trees
Spec: feature-plans/live-tree-ticker-health.md
Ref: AlphaBotPM@c3e12882141ae601f900fdc13e0c342462a739bb

## Architecture

Every `file:line` below is at AlphaBotPM@c3e12882. The worktree HEAD (`5af9e9ec`) differs from the ref only in the two plan files, so the working tree is the ref for every code citation.

### Shape: two advisory modules, wiring in `app.py`, no schema change

- `advisors/ticker_health.py` (new, pure, no I/O): the tree walker, the Alpaca answer classifier, the carry-forward of warnings, the completion rule, the dashboard view builder and the Discord message builder.
- `advisors/ticker_health_runner.py` (new, I/O): one run. It reads the rosters and the score trees through the existing read client, reads Alpaca asset records, writes the run record and posts to Discord. It never raises.
- `app.py`: the 01:30 slot, the catch-up start at daemon start, the single-flight lock, the predicate that sees the other in-process Composer readers, and the dashboard render.
- `templates/index.html`: one warning element and one notice element above the hero section.

The two-module split mirrors the tenure review (`advisors/tenure_review.py` pure, `advisors/tenure_review_runner.py` I/O, header at `advisors/tenure_review_runner.py:1-6`). Search for an existing judge of tradability: `git grep -n "tradable"` outside `tests/` finds only `advisors/universe_provider.py:78-85` (filter of the weekly set) and `:103`. The plan rules out that set (G-10, S-2.8, S-2.13), so the verdict logic is new.

### Persistence: append-only advisor observations, no migration

The check stores two roles through `database.insert_advisor_observation` (`database.py:1221-1273`), which forces `is_advisory_only=1` and has no update accessor, and reads them through `database.get_advisor_observations_for_role` (`database.py:1276-1295`, read-only connection, newest first). Neither role joins `_ADVISOR_ROLES` (`app.py:13029-13037`), so neither appears in the AI Advisor lists, as with `RETIREMENT_RUN_SUMMARY` and `TENURE_REVIEW_RUN`.

- `TICKER_HEALTH_RUN`: one row per run that ends, written once at the end. `subject_type="ticker_health_run"`, `subject_id=<run_id>`, `verdict=<status>`. The `raw_response` (schema 1):
  - `schema`, `run_id`, `trigger` (`nightly` or `catch_up`), `started_at` and `ended_at` (aware UTC ISO text).
  - `status`: `completed`, `unavailable`, `skipped` or `failed`. `completed` is a boolean (D-6). `reason` holds the fixed run-level text for `unavailable` and `skipped`. `error_type` holds the exception class name for `failed`.
  - `warnings`: written on completed runs only. Each warning has `symphony_id`, `display_name`, `ticker`, `reason`, `asset_nodes`, `conditions`, `held` (`held`, `not_held` or `unknown`), `found_at` (the `ended_at` of the run that last judged the pair dead) and `announced` (boolean).
  - `not_checked`: `accounts` (`label`, `reason`), `symphonies` (`symphony_id`, `display_name`, `reason`) and `tickers`. A ticker entry is either a named ticker (`symphony_id`, `display_name`, `ticker`, `reason`) for a ticker not in plain form, or an Alpaca failure count per symphony and reason (`symphony_id`, `display_name`, `reason`, `count`).
  - `counts`: accounts read, symphonies listed and judged, tickers asked and answered, dead pairs, Composer and Alpaca request totals.
- `TICKER_HEALTH_ANNOUNCEMENT`: one row per Discord message that Discord accepted. `subject_id=<run_id>`, `verdict="announced"`, `raw_response={"run_id", "pairs": [[symphony_id, ticker], ...]}`.

Once-only announcement without a mutable table: when run R+1 starts, a pair counts as announced when the previous completed run R carries it with `announced` true, or when an announcement row names R's `run_id` and the pair. R+1 writes its warnings with that flag carried. A pair that leaves the warnings (cleared, AC-6) loses the flag, so a later death is announced again (S-5.5). Only completed runs post, and the lock below admits one run at a time. Every announcement row newer than R therefore belongs to R, and a scan of the newest `ANNOUNCEMENT_SCAN_LIMIT = 200` rows covers it.

Why no table: a migration and any `database.py` change would break three live pins:
- the `database.py` content hash in `tests/fixtures/page_explainers/ref_capture.json` (checked by `tests/ui/test_page_explainers_unchanged_surfaces.py:285-290` over `tests/ui/_page_explainers.py:444-449`);
- the migration listing (`tests/ui/test_page_explainers_unchanged_surfaces.py:293-294`);
- the schema record (`tests/app/test_hygiene_surface_unchanged.py:122-155`).

The append-only design needs neither, and S-8.9 holds with no migration added.

Restart safety (R-32, S-7.7, S-5.9):
- A run computes everything in memory and writes its one run row last. A daemon stop before that write leaves no row, so `/` shows the previous completed run.
- Order after the row: post the new pairs, then write one announcement row per accepted message. A stop between a post and its row repeats that message once on the next completed run (Q-6), and never loses it.

### `advisors/ticker_health.py` (pure)

Constants, each with a source comment:
- `RUN_ROLE = "TICKER_HEALTH_RUN"` and `ANNOUNCEMENT_ROLE = "TICKER_HEALTH_ANNOUNCEMENT"`.
- `STALE_AFTER = timedelta(hours=36)` (Q-4).
- `COVERED_ASSET_CLASS = "EQUITIES"` (S-1.10, NB-19).
- `BINARY_COMPOUND_PLACEHOLDER = "%"` (the placeholder documented at `advisors/symphony_schema.py:591-593`).
- `_PLAIN_TICKER = re.compile(r"[A-Z][A-Z0-9]*(?:\.[A-Z0-9]+)?")`, used with `fullmatch`. It admits one share-class dot and rejects a slash, a space, `..`, a leading digit and the trimmed fixtures' `CORE_ASSET_0001` placeholders (S-9.4).
- `LEAD_SENTENCE` (AC-3 text).
- `DISCORD_CONTENT_LIMIT = 2000`, Discord's documented limit on a message's `content`.

Tree walker, `tree_ticker_uses(tree) -> dict[str, TickerUse]`, where `TickerUse` is a frozen dataclass of `asset_nodes` and `conditions`. The walk is iterative over `children` lists, never recursive, as `extract_tickers` is (`advisors/symphony_schema.py:597-612`):
- An asset node (`step == "asset"`) with a string `ticker` other than `%` adds 1 to that ticker's `asset_nodes` (S-1.1).
- A condition node is a node that is not an else branch (`is-else-condition?` not true) and that is either an `if-child` or carries a dict `condition`. Its operand set is built two ways:
  - With a dict `condition` (a compound if-child): the operand set is `symphony_schema.extract_tickers({"condition": block})`. That public function (`advisors/symphony_schema.py:585-613`, walking the block at `:616-645`) already covers binary `lhs` and `rhs` tickers, binary-compound `tickers` lists, nested `conditions` at any depth, and drops `%` (S-1.5, S-1.7). The if-child's own `lhs-val` and `rhs-val` are display labels on such a node and are not read (S-1.6). Measured over the 34 compound if-children in the case4 capture and the 11 trimmed trees, every plain label is a ticker of its own block, and the one comma-joined label is not a ticker.
  - Without one (a legacy if-child): `lhs-val` when it is a string, and `rhs-val` when it is a string and `rhs-fixed-value?` is not `True` (S-1.2, S-1.3, S-1.4). The trimmed trees show an absent `rhs-fixed-value?` with a ticker `rhs-val` and an `rhs-fn` (for example `real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json`, 356 if-children).
  - Each distinct ticker in one node's operand set adds 1 to that ticker's `conditions`, so a node naming T on both sides counts once (S-1.8).
- `symphony_schema.extract_tickers` and its other callers are untouched (S-1.11, NB-14, NB-18).

Other pure functions:
- `is_plain_ticker(text) -> bool`.
- `asset_class_covered(tree) -> bool`: false only when the top-level `asset_class` is present and is not `EQUITIES`. The case4 capture has no such key and is covered (S-1.10).
- `classify_asset_answer(status, body) -> TickerVerdict` (`state` is `dead`, `healthy` or `not_checked`, plus `reason`), DC-9 enumerated whole:
  - 200 with a dict body whose `status` is `"active"` or `"inactive"` and whose `tradable` is a bool: inactive and not tradable reads "inactive and not tradable at Alpaca"; active and not tradable reads "not tradable at Alpaca"; inactive and tradable reads "inactive at Alpaca"; active and tradable is `healthy`. `exchange` is never read (S-2.1 to S-2.6).
  - Any other 200 body reads not checked, "Alpaca response unreadable" (S-2.12).
  - 404 reads dead, "not listed at Alpaca" (S-2.4, Q-2).
  - 401 or 403 reads "Alpaca refused the credentials"; 429 reads "Alpaca rate limit"; 5xx reads "Alpaca unavailable"; every other status (3xx, other 4xx) reads "Alpaca rejected the request" (S-2.16).
- `classify_asset_failure()` returns not checked, "Alpaca unreachable", for a transport error.
- `display_name(entry)`: the trimmed roster `name`, else the Composer id (S-3.6). This mirrors `advisors/tenure_review_runner.py:114-117`, which is private to that module.
- `held_state(holdings_fields, ticker)`: over the roster `holdings` of every account listing the symphony.
  - `held` when any entry's `ticker` or `working_ticker` equals the ticker. These are the same two keys the engine reads at `alpha_bot_execution.py:920-927`.
  - Otherwise `unknown` when any account's field is absent, not a list, or has an entry that is not a dict with a string ticker.
  - Otherwise `not_held` (S-3.2, S-3.3, S-3.7; DC-2).
- `carry_warnings(previous_warnings, judged, unjudged_symphonies, listed_ids, every_roster_read, ended_at) -> list[dict]`, for each previous warning (X, T), against this run's state:
  - X judged, T in X's tree and judged: replaced by this run's verdict. Dead gives a fresh warning with `found_at=ended_at` and the counts from this tree; healthy drops it (S-6.1).
  - X judged and T not in its tree: dropped (S-6.2, S-6.6).
  - X judged, T in its tree, T not judged: carried unchanged (S-6.4).
  - X not judged (score failed, market opened, rate limit, other reader, id not usable): carried (S-6.3).
  - X absent from every roster with `every_roster_read` true: dropped (S-6.5). Absent while some roster failed: carried.
  - `announced` is carried with each kept pair. A fresh dead pair takes `announced` from the previous warning for the same pair, else from the previous run's announcement rows, else false.
- `is_completed(rosters_read, tickers_asked, tickers_answered) -> bool` implements D-6 literally: at least one roster read and (no ticker asked, or at least one answered). An answer is a `dead` or `healthy` verdict.
- `page_view(latest, latest_completed, now_utc) -> dict` builds `{"warning": None | {"checked", "lines": [{"text", "from_check"}]}, "notices": [str]}`:
  - Lines come from `latest_completed["warnings"]`, sorted by `display_name.casefold()`, then symphony id, then ticker (S-3.5).
  - Line text: "X: T is {reason}. Used by n asset node(s) and m condition(s). Held now." (or "Not held now." or "Holding unknown."). The plural follows the count (S-3.4).
  - `from_check` is the formatted `found_at` when the latest run is not the latest completed run, or when `found_at` differs from that run's `ended_at` (S-4.6).
  - Times are formatted with `analytics.format_et_stamp` (`analytics.py:2524`).
  - Notices, in this order:
    1. "No ticker check has completed yet." when no run has completed (S-4.9).
    2. "Ticker check failed on {ended_at}." when the latest run is `failed` (S-7.10).
    3. "Ticker check unavailable: {reason}. Last completed check {stamp}." (or "No completed check yet.") when the latest run is `unavailable` or `skipped` (S-4.7).
    4. "No ticker check has completed since {stamp}." when the newest completed run ended more than `STALE_AFTER` before `now_utc` (S-4.10).
    5. When the latest run is the completed one, its `not_checked` in record order: accounts first ("Not checked: symphonies of the {label} account ({reason})."), then symphonies ("Not checked: Y ({reason})."), then tickers ("Not checked: T in Y (ticker form not checked)." and "k ticker(s) in Y could not be checked ({reason}).") (S-4.1 to S-4.5, S-4.8, S-4.15 to S-4.21, S-1.10, S-9.4).
  - No notice, and no warning without lines, when the latest completed run is the latest run, is within `STALE_AFTER`, and left nothing unchecked (S-3.8, S-4.12).
- `discord_literal(text)`: puts a backslash before each backslash, asterisk, underscore, tilde, backtick, vertical bar, angle bracket, hash, hyphen, square bracket, parenthesis, at sign and colon, so a name or ticker never forms Discord markdown, a link or mention syntax (S-9.3).
- `discord_messages(pairs) -> list[str]`:
  - Each message begins with `LEAD_SENTENCE`, then one line per pair: "{name} ({hash id}): {T} is {reason}. Used by ... {held text}". The name and ticker pass through `discord_literal`; the line follows the AC-3 wording plus the hash (S-5.1).
  - Lines pack into messages of at most `DISCORD_CONTENT_LIMIT` characters (S-5.11). A single line longer than the room left after the lead keeps its hash, ticker and facts and shortens only the name, ending it with "...".

### `advisors/ticker_health_runner.py` (I/O)

Public entry:
- `run_ticker_health_check(*, account_ids, account_labels, alpaca_key, alpaca_secret, webhook_url, trigger, other_reader_running, clock=None) -> RunOutcome`.
  - `clock` returns an aware UTC datetime and defaults to `datetime.now(UTC)`.
  - `other_reader_running` is a zero-argument callable that `app.py` supplies.
  - `RunOutcome` is frozen: `run_id`, `status`, `completed`.
  - It never raises an `Exception`. A `BaseException` (daemon stop) propagates and writes nothing (S-7.7).
- `read_latest_runs(*, scan_limit=RUN_SCAN_LIMIT) -> LatestRuns(latest, latest_completed)`:
  - It reads the newest `RUN_ROLE` row first. Only when that row is not completed does it read `scan_limit` rows and take the newest completed one.
  - `RUN_SCAN_LIMIT = 365` follows the fast-path-then-scan precedent at `app.py:7850-7857`.
  - It raises on a database error; each caller degrades.

Run order inside `run_ticker_health_check`:
1. `run_id = uuid4().hex`; `started_at = clock()`. Read `read_latest_runs()` and the announcement rows. A raise here goes to the run-level `except` and ends the run as failed before any request.
2. No account id: write `unavailable`, "no Composer account configured", with no request (S-4.11).
3. A blank `alpaca_key` or `alpaca_secret`: write `unavailable`, "Alpaca credentials not configured", with no request (S-2.14).
4. Composer gate before every Composer request, `_composer_gate()`, in order:
   1. A stop reason already set (rate limit, market opened, other reader) skips the request with that reason.
   2. `market_calendar.get_market_state(clock().astimezone(ET)) == "open"` (`market_calendar.py:132-163`) sets the stop reason "market opened" (S-7.3). Before the first request it instead ends the run as `skipped`, "market open" (S-7.2).
   3. While `other_reader_running()` is true, sleep `OTHER_READER_POLL_SECONDS = 15`, up to `OTHER_READER_WAIT_LIMIT_SECONDS = 2700` after `started_at` (S-7.5, Q-7). When the limit passes before the first request, the run ends `skipped`, "another Composer job was still running". After it, the stop reason is "another Composer job was running". The market test repeats after any wait.
5. Rosters: `composer_portfolio_client.read_symphony_statistics(account, None)` (`advisors/composer_portfolio_client.py:151-172`) for each account in order. Pacing is the client's own `COMPOSER_READ_SPACING_SECONDS = 6.0` sleep after every request (`:27-34`, `:139-141`), which keeps S-7.11 at 10 a minute and under the PM's one-a-second ceiling. The client's failure classes (`:86-96`) map to fixed reasons:
   - `unavailable`: "Composer unavailable"; `refused`: "Composer refused the request"; `not_found`: "account not found"; `rejected`: "Composer rejected the request"; `unreadable` (a 200 that is not a roster, or a non-plain account id, refused at `:158-159` with no request): "Composer response unreadable"; `rate_limited`: "Composer rate limit", which also sets the stop reason (S-4.5, S-4.17 to S-4.21, S-7.6).
   - Symphonies listed by several accounts merge by id: the first account's name, and every account's holdings (S-3.7).
6. No roster read: write `unavailable`. The reason is the shared reason when every account failed with one class, else "no account's symphony list could be read".
7. Trees, in roster order: a symphony whose id fails `composer_portfolio_client.is_plain_id` (see Per-Cycle Detail C1) gets "symphony id not usable" with no request (S-4.16). Otherwise `read_symphony_definition(symphony_id, None)` (`:207-223`), mapped:
   - `unavailable`: "Composer unavailable" (S-4.1); `not_found`: "definition not found" (S-4.2); `refused`: "Composer refused the request" (S-4.3); `unreadable`: "definition unreadable" (S-4.4); `rejected`: "Composer rejected the request" (S-4.15); `rate_limited`: "Composer rate limit" (S-7.6).
   - A tree whose `asset_class_covered` is false: "asset class not covered by the check", and none of its tickers is asked (S-1.10).
   - Else `tree_ticker_uses`. Non-plain tickers become named not-checked entries (S-9.4); plain ones join the run's ask set.
8. Alpaca: the ask set is sorted, and each ticker is asked once per run (S-2.15).
   - `GET {universe_provider.ALPACA_TRADING_BASE_URL}/v2/assets/{symbol}`. The constant is imported from `advisors/universe_provider.py:58`; no other universe-provider name is imported, and its cache is never read or refreshed (S-2.13).
   - Headers `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY`, the form at `advisors/universe_provider.py:99-102`. `timeout=ALPACA_READ_TIMEOUT_SECONDS (10)` and `allow_redirects=False`.
   - Pacing: `ALPACA_REQUEST_SPACING_SECONDS = 0.5`, a sleep after every request whether or not it was answered. That is at most 120 a minute against Alpaca's documented 200 a minute. 303 tickers take about 2.5 minutes and 1,500 about 13.
   - Retries: `ALPACA_MAX_ATTEMPTS = 2` on 429, 5xx and transport errors. The wait is an integer `Retry-After` clamped into `[0, RETRY_AFTER_CAP_SECONDS = 30]`, else `ALPACA_RETRY_BACKOFF_SECONDS = 2`. The cap mirrors `advisors/composer_series_client.py:82-107`, private to that module, whose top-level engine import (`:71`) rules out importing it.
   - Stops: the first 401 or 403 stops all Alpaca requests, and every unasked ticker reads "Alpaca refused the credentials" (S-2.10). `ALPACA_FAILURE_STREAK_LIMIT = 5` consecutive tickers ending unreachable or unavailable stop the rest with the last reason. That bounds an outage to about 2 minutes of requests (S-2.9).
   - Failure counts are stored per symphony and reason (S-2.11, S-4.8).
   - One `timing_log.emit("alpaca", "GET", "/v2/assets", status=..., ms=...)` per attempt. The symbol is never logged.
9. `ended_at = clock()`. `completed = is_completed(...)`. When not completed because Alpaca gave no answer, the run reason follows the failures:
   - any refusal reads "Alpaca refused the credentials";
   - only transport errors read "Alpaca unreachable" (S-2.9);
   - otherwise the first failed ticker's reason.
10. Completed: `warnings = carry_warnings(...)`. One `database.insert_advisor_observation(advisor_role=RUN_ROLE, ...)`.
11. Discord, completed runs only, inside its own `try`:
    - The pairs are this run's fresh dead warnings that are not announced (S-5.2, S-5.4, S-5.8). With no `webhook_url`, nothing is posted (S-5.7).
    - Each message from `discord_messages` is sent as `requests.post(webhook_url, json={"content": text, "allowed_mentions": {"parse": []}}, timeout=DISCORD_TIMEOUT_SECONDS (10), allow_redirects=False)`. The timeout matches `reporting.py:875`. The sends are `DISCORD_MESSAGE_SPACING_SECONDS = 1.5` apart (`reporting.py:769`).
    - A 2xx response writes one announcement row for that message's pairs. Any other status, or an exception, logs the status or the exception class and stops further posts in the run (S-5.6).
    - The exit alert and EOD senders (`reporting.py:370-776`, `:779-875`) are not reused: neither checks the response, neither disables mentions, and `send_discord_alert` posts outside any `try` (`reporting.py:875`). `reporting.py` stays byte-unchanged (S-5.12).
12. The run-level `except Exception` writes a `failed` row with `error_type=type(exc).__name__`, unless the run row was already written. A Discord-phase error is logged and never fails a stored run.

Logging: `logging.getLogger("alphabot.ticker_health")`, a child of the daemon's `alphabot` logger (`app.py:813`).
- One INFO start line (trigger, run_id) and one INFO end line (status, counts, Discord posted and failed counts).
- WARNING lines carry status codes, fixed reasons and exception class names only.
- No line carries a key, secret, webhook URL, response body, symphony name, ticker or account id (S-9.1, S-9.5; the matrix row "the log names no pair beyond counts").
- The read client's own lines (`advisors/composer_portfolio_client.py:137`, `:142`) already carry the kind and status only.

### `app.py` wiring

- `_env_file_settings(keys) -> dict`: new. It is the body of `_tenure_review_settings` (`app.py:2653-2663`) lifted out unchanged (the `.env` file wins over `os.environ`). `_tenure_review_settings` becomes `return _env_file_settings((_TENURE_REVIEW_THRESHOLD_KEY, *_TENURE_REVIEW_ACCOUNT_KEYS))`, so one rule reads both features' settings (S-8.5 proves the review unchanged).
- `_ticker_health_settings()`: `_env_file_settings((*_TENURE_REVIEW_ACCOUNT_KEYS, "ALPACA_KEY", "ALPACA_SECRET", "DISCORD_WEBHOOK_URL"))`. These are the keys the plan names (Configuration and Enablement) and the dashboard's Discord read (`app.py:8647`).
- `_TICKER_HEALTH_RUN_LOCK = threading.Lock()`, the same single-flight form as `_RETIREMENT_RUN_LOCK` (`app.py:2348`).
- `_TICKER_HEALTH_WAITS_FOR_THREADS = frozenset({"incubation-tick", "retirement-recommender-tick", "retirement-run-now", "tenure-review-on-demand"})`. These are the thread names at `app.py:2132`, `:3008`, `:12562` and `:12597`. The nightly 03:45 thread runs the review and then the recommender inside one thread (`app.py:2953-2969`), so its name covers both stages and the gap between them.
- `_ticker_health_other_reader_running() -> bool` answers auditor point (a). It is true when any live thread carries one of those names, when `_RETIREMENT_RUN_LOCK.locked()`, or when `tenure_review.REVIEW_RUN_STATE.snapshot()["running"]` (`advisors/tenure_review.py:2007-2019`). The last two cover the moment between a slot claim and its thread start (`app.py:2992-3010`, `:12555-12563`, `:12591-12598`). Any error answers true, so an unknown state waits rather than overlaps. Nothing in the incubation tick changes: its thread's life is the record of its run.
- `_ticker_health_run_body(trigger)`:
  - It acquires the lock non-blocking. When the lock is held it logs one INFO line and returns, writing nothing (S-7.4, S-10.7).
  - Otherwise it lazily imports `advisors.tenure_review_runner` and `advisors.ticker_health_runner` (CC-2). It calls `run_ticker_health_check` with `configured_account_ids(env)` and `configured_account_labels(env)` (`advisors/tenure_review_runner.py:82-89`) and the settings above, then releases in `finally`.
  - An escaping `Exception` is logged by class name only.
- `_run_ticker_health_tick()`: `threading.Thread(target=_ticker_health_run_body, args=("nightly",), daemon=True, name="ticker-health-check").start()`, then returns at once. This is the regime-label wrapper form (`app.py:2153-2155`).
- `_ticker_health_catch_up_due(now_utc) -> bool`:
  - False while `get_market_state(now_et) == "open"` (S-10.6).
  - Else true when no completed run ended at or after the close instant of `market_calendar.most_recent_completed_session(now_et)` (`market_calendar.py:170-185`) at `market_calendar.session_close(session)` (`:82`, half days included) in ET (S-10.3, S-10.4, S-10.5).
  - A database error answers false and logs the class; the nightly slot still runs.
- `_start_ticker_health_catch_up() -> bool`: when due, it starts the same body on a daemon thread named `ticker-health-catch-up` with trigger `catch_up`. It never raises.
- `run_scheduler` (`app.py:3078-3111`) gains one registration before the 02:00 line at `:3090`: `_guard(schedule.every().day.at("01:30").do(_run_ticker_health_tick), "01:30 ticker health check")`. `_guard` is at `app.py:1666-1673`. Every existing slot keeps its time (S-7.9).
- The `__main__` block calls `_start_ticker_health_catch_up()` right after `_start_account_refresh("startup")` (`app.py:13312`) and before the scheduler thread (`:13315`). This is the precedent pinned by `tests/app/test_startup_account_refresh.py:337-387`. It sits there, not inside `run_scheduler`, because about a dozen test files run `run_scheduler` (for example `tests/app/test_scheduler_survival.py:77-107`). A start inside it would launch real runs in them, and a worktree carries a copied `.env` with live keys.
- Slot time (S-10.8). `schedule` runs host-local times with no zone (asserted at `tests/app/test_scheduler_survival.py:161-162`). The droplet host is `Etc/UTC` (`.claude/CLAUDE.md`, `DE-REGIME-LABEL-001`).
  - On a UTC host, 01:30 is 20:30 EST or 21:30 EDT on the previous ET day: after the 16:00 and 13:00 closes and about 12 hours before the 09:30 open.
  - On an ET host, 01:30 exists on both clock-change days (the change is at 02:00) and is before the open.
  - The weekly suggestions timer fires Mondays 04:00 America/New_York (`docs/DEPLOYMENT.md:356-364`). The check's Composer reads end within `OTHER_READER_WAIT_LIMIT_SECONDS` plus about 2 minutes of its start: by 22:20 EST Sunday on the droplet, and by 02:20 ET on an ET host. That is the scheduling constraint the PM ruling asks for.
- Dashboard, in `_render_dashboard` (`app.py:3618-3844`): one new keyword `ticker_health=_ticker_health_page_view()` in the `render_template` call (`:3807-3844`).
  - `_ticker_health_page_view()` calls `ticker_health_runner.read_latest_runs()` and then `ticker_health.page_view(..., datetime.now(UTC))`. Both reads are read-only database reads, and nothing calls Composer or Alpaca (S-3.9, S-3.13).
  - A read failure returns `{"warning": None, "notices": ["The ticker check result could not be read."]}`, never an empty view that reads as all clear (Items Raised).
- Nothing changes in `/health` (`app.py:683-703`, reasons at `:1688-1712`), `/api/header-status` (`:706`), `static/chrome.js` or `_SETTINGS_WRITE_ALLOWLIST` (S-8.6, S-8.7, Q-3).

### `templates/index.html`

- After `.market-banner[hidden]` (`templates/index.html:833`), two classes:
  - `.ticker-health-warning`: the `.fleet-banner` tokens at `:794-806` (`--studio-warn-tint`, `--studio-warn-border`, `--studio-ink`, 13 px, weight 500), block layout and left-aligned text.
  - `.ticker-health-notice`: the `.market-banner` tokens at `:822-833` (`--studio-chip-bg`, `--studio-rule`, `--studio-ink-dim`), left-aligned.
  - Both use `overflow-wrap: anywhere` (S-3.12), and `.ticker-health-earlier` uses `--studio-ink-dim` (S-4.6). No raw colour.
- Between the market-state banner (`:1313-1315`) and the hero comment (`:1317`):
  - `<div class="ticker-health-warning" data-testid="ticker-health-warning">` with one `<p>` holding the lead and "Checked {stamp}.", then a `<ul>` of lines, each line's suffix in `<span class="ticker-health-earlier">`.
  - Then `<div class="ticker-health-notice" data-testid="ticker-health-notice">` with one `<p>` per notice.
  - Each element renders only when it has content. All text goes through Jinja autoescape, with no `| safe` (S-9.2). There is no `.section-header`, no button and no script (NB-10, NB-11, NB-12).
- The fleet banner (`:1283-1291`) and the market-state banner stay byte-identical (S-3.11). No Glossary line: the copy uses no term the page's Glossary (`:2095`) lacks.

### Dependencies, routes, configuration

No new package, route, POST, configuration key or migration. `alpha_bot_execution.py`, `math_engine.py`, `database.py`, `reporting.py`, `advisors/symphony_schema.py`, `advisors/universe_provider.py`, `advisors/tenure_review*.py` and `migrations/` carry zero diff.

## Criterion Thread

| AC | R items | Design element (file:line, or "new: <reason and search>") | Test seam (test file:line; fixture and provenance; harness pattern) | Cycle |
|---|---|---|---|---|
| AC-1 | R-2, R-6, R-7, R-8, R-22, R-23 | new: `advisors/ticker_health.tree_ticker_uses`; `extract_tickers` (`advisors/symphony_schema.py:585-613`) misses legacy operands, measured on every captured tree, so the check reads them itself and calls `extract_tickers` only on condition blocks; S-1.10 lands in `advisors/ticker_health_runner.py` step 7 | new `tests/advisors/test_ticker_health_tree.py` and `tests/advisors/test_ticker_health_runner.py` (pattern `tests/advisors/test_tenure_review_runner.py:156-200`); captured trees `tests/fixtures/composer/tenure-review/case4_definition_nOyb55RMGVCKPiYXv7TI.json` (README provenance), `tests/fixtures/advisors/frontrunner/real_tree_*.json` (operand coverage only), the PM's fresh score capture of `8FAXAnQmYi1INDubazeC` | C1 |
| AC-2 | R-2, R-3, R-4, R-9, R-24, R-25 | new: `advisors/ticker_health.classify_asset_answer` and `advisors/ticker_health_runner` Alpaca read; the only Alpaca asset reader (`advisors/universe_provider.py:88-118`) reads the active set only and caches it 7 days (`:65-68`) | new `tests/advisors/test_ticker_health_verdicts.py` and `tests/advisors/test_ticker_health_runner.py`; new router `tests/advisors/_ticker_health_http_router.py` extending `tests/advisors/_composer_http_router.py:295-460`; captured Alpaca records `tests/fixtures/alpaca/assets_captured/` (PM `/api-fixture`, paper host) | C1 |
| AC-3 | R-1, R-2, R-6, R-7, R-11, R-12, R-13, R-14, R-16, R-18, R-22, R-27, R-33 | new: `app._ticker_health_page_view`, keyword at `app.py:3807-3844`; `advisors/ticker_health.page_view`; markup between `templates/index.html:1315` and `:1317` | new `tests/app/test_ticker_health_dashboard.py`: records written by the real C1 runner over the C1 router, page through the Flask test client, state seeded by `tests/ui/_shell_support.py:108` (captured bot state), clock fixed by `tests/app/_refresh_timing_state_world.py:119`; auth leg on `tests/app/test_dashboard_auth.py:87-111` | C2 |
| AC-4 | R-18, R-25, R-26 | new: `advisors/ticker_health.page_view` notices, because no ticker check exists to extend (search above); the run record's `not_checked` written by `advisors/ticker_health_runner` steps 2 to 9 | `tests/app/test_ticker_health_dashboard.py` as AC-3; error replies from `tests/advisors/_composer_http_router.py:142-240` (captured error bodies, marker appended) | C2 |
| AC-5 | R-1, R-6, R-11, R-12, R-13, R-14, R-20, R-28, R-33 | new: `advisors/ticker_health.discord_messages`, `discord_literal`; runner step 11; announcement rows through `database.insert_advisor_observation` (`database.py:1221-1273`); no Discord helper returns a status or disables mentions (`reporting.py:764`, `:770`, `:875`; `git grep allowed_mentions` finds none) | `tests/advisors/test_ticker_health_runner.py` and new `tests/advisors/test_ticker_health_discord_text.py`; webhook replies by the C1 router (shape schema-derived from Discord's published webhook execute schema: 204 empty body, 429 with `retry_after`) | C1 |
| AC-6 | R-29 | new: `advisors/ticker_health.carry_warnings` and runner step 10, because no stored ticker verdict exists to clear (search above) | C1: `tests/advisors/test_ticker_health_runner.py` (S-6.7); C2: `tests/app/test_ticker_health_dashboard.py`, two consecutive real runs then `GET /` | C2 |
| AC-7 | R-21, R-30, R-31, R-32, R-40, R-44 | runner `_composer_gate`; `app._TICKER_HEALTH_RUN_LOCK`, `_ticker_health_other_reader_running` reading `app.py:2132`, `:2348`, `:3008`, `:12562`, `:12597`, `advisors/tenure_review.py:2007-2019`; slot in `app.py:3078-3111` | C1: `tests/advisors/test_ticker_health_runner.py` (S-7.3, S-7.6, S-7.11 with `assert_requests_spaced`, `tests/advisors/_composer_http_router.py:459`); C2: new `tests/app/test_ticker_health_scheduling.py` (pattern `tests/app/test_regime_label_scheduling.py:71-221`), `tests/app/test_scheduler_survival.py:130-139` | C2 |
| AC-8 | R-19, R-34, R-35, R-40, R-41, R-42, R-44 | no diff to the protected files; new scan file; `app.py:683-720` and `_SETTINGS_WRITE_ALLOWLIST` untouched | new `tests/security/test_ticker_health_no_trade_boundary.py` (pattern `tests/security/test_regime_label_job_no_trade_boundary.py:1-486`, client allowance as `tests/security/test_retirement_recommender_no_trade_boundary.py:37-48`); existing `tests/test_mdd_window_truth_zero_diff_guard.py:74-75`, `tests/app/test_hygiene_surface_unchanged.py:91`, `:122-155`; `tests/app/test_health_endpoint.py:80-199`, `tests/app/test_header_status_route.py:80-118`; `tests/app/test_tenure_review_end_to_end.py:196` | C2 |
| AC-9 | R-43 | new: runner logging, `discord_literal` and `_PLAIN_TICKER`, because no Discord escaper exists (`git grep allowed_mentions` and `escape_markdown` find none); the warning renders through the template autoescape at `templates/index.html:1313-1317` | C1: runner tests with `caplog` and a `LEAKMARK` body marker (pattern `tests/advisors/test_tenure_review_runner.py:132`); C2: dashboard test with a captured name made hostile | C2 |
| AC-10 | R-10, R-21, R-44 | `app._run_ticker_health_tick` at 01:30, `app._start_ticker_health_catch_up` from the `__main__` block (`app.py:13312`) | `tests/app/test_ticker_health_scheduling.py` with a fixed ET clock (`tests/app/_refresh_timing_state_world.py:119`); `__main__` order by AST as `tests/app/test_startup_account_refresh.py:337-387` | C2 |

## Execution Model

Two cycles in order, because C2 reads C1's code. C2's dashboard and scheduler tests build their stored results with the real C1 runner, and the app wiring calls `run_ticker_health_check` and `read_latest_runs`.

- Fixture precondition, PM before C1 dispatches. Workers cannot reach the credentials. The PM runs `/api-fixture` read-only, after the close, on the droplet's credentials, and commits each folder with a `PROVENANCE.md` (host, UTC time, request, one record per case):
  - `tests/fixtures/alpaca/assets_captured/`, from the paper host `GET /v2/assets/{symbol}`:
    - SHNY (inactive and not tradable);
    - one active and tradable ETF;
    - one unlisted symbol (404);
    - one active and not tradable symbol when one exists, found by one read of the full list (Known-Unconfirmed);
    - one 401 with a deliberately wrong key.
  - `tests/fixtures/composer/ticker-health/`:
    - `/symphonies/8FAXAnQmYi1INDubazeC/score` (SHNY in asset nodes and a legacy condition);
    - one `symphony-stats-meta` for the Roth account with `holdings` kept, trimmed to the entries the tests use, its trimming recorded.
- C1, the advisory modules. Two RED batches share one implementer:
  - Batch A, `test-writer`: walker, verdict classifier, completion rule, carry-forward, page view text, Discord text (DC-1, DC-2, DC-9).
  - Batch B, `test-writer`: runner integration over the HTTP router, persistence, Discord posting, pacing, logs (DC-3 to DC-8).
  - GREEN: one `implementer`. The batch B author is the closer.
- C2, the app wiring. Two RED batches share one implementer:
  - Batch A, `test-writer`: slot, catch-up, single-flight, other-reader wait, no-trade scans, protected surfaces (DC-5, DC-6, DC-7, DC-11).
  - Batch B, `test-writer`: dashboard render, notices, clearing, escaping, auth.
  - GREEN: one `implementer`. The batch A author is the closer. After GREEN it re-pins `scheduler_digest` (`tests/fixtures/page_explainers/ref_capture.json:971`) in a test-only commit, on the PM's recorded word (Items Raised 1).
- Review per head, dispatched by the PM at each cycle-complete:
  - one `qa` serving both heads, plus one `qa` in hidden mode (Sensitive: security);
  - one `code-reviewer` serving both heads;
  - `pipelines-expert` once per head (C1 data pipeline, C2 scheduler);
  - on C2's head, one `ux-expert` with its `behavioral-evidence-capturer` (the dashboard renders), then the task walkthrough;
  - one `integration-auditor` on C2's head, because the diff spans UI and pipelines through the run-record contract C1 writes and C2 renders.
  - No `api-expert`: no route or JSON body changes. One `doc-gen` at the slice tail, after every reviewer passes both heads.
- Isolation follows the project's environment rules. Each teammate works in its own worktree off the branch base below, never the primary checkout. Kickoffs mandate `cd` and a `git rev-parse --show-toplevel --abbrev-ref HEAD` check and copy `.env`. Every test that reaches `app._ticker_health_settings` points `app.ENV_FILE_PATH` at a temp file, so no test reads the copied live keys. Pytest runs `-n0` on named files only, never the full tree, never `-n auto`, never `-o addopts=""`. CI is the full gate.

Team size: 15 = 4 test-writer + 2 implementer + 1 qa + 1 qa (hidden mode) + 1 code-reviewer + 1 ux-expert + 1 behavioral-evidence-capturer + 2 pipelines-expert + 1 integration-auditor + 1 doc-gen

## Dispatch Ordering

| Cycle | Consumes (cycle: committed artifact) | Branch base |
|---|---|---|
| C1 |  | feat/live-tree-ticker-health@5af9e9ec |
| C2 | C1: advisors/ticker_health_runner.py (run_ticker_health_check, read_latest_runs); C1: advisors/ticker_health.py (page_view and the run-record schema); C1: tests/advisors/_ticker_health_http_router.py | C1's branch at its cycle-complete SHA |

C1's dispatch waits for the PM's fixture capture commit on `feat/live-tree-ticker-health`; its base is then that commit.

## Per-Cycle Detail

### C1: advisory modules (AC-1, AC-2, AC-5; AC-6 S-6.7; AC-9 S-9.1, S-9.3 to S-9.5; AC-7 S-7.3, S-7.6, S-7.11; AC-8 S-8.4, S-8.9)

- Files added:
  - `advisors/ticker_health.py`, `advisors/ticker_health_runner.py`.
  - Tests: `tests/advisors/_ticker_health_http_router.py`, `tests/advisors/test_ticker_health_tree.py`, `tests/advisors/test_ticker_health_verdicts.py`, `tests/advisors/test_ticker_health_discord_text.py`, `tests/advisors/test_ticker_health_runner.py`.
- Files modified: `advisors/composer_portfolio_client.py` gains `is_plain_id(identifier) -> bool`, which returns `_is_plain_id(identifier)` (`:109-111`). It sits beside the public `is_valid_spacing_seconds` (`:99-106`), so the runner and the client share one plain-id rule (S-4.16). The four reads at `:151-298` are unchanged (Items Raised 5).
- Primitives imported:
  - `composer_portfolio_client.read_symphony_statistics`, `read_symphony_definition` (`:151-223`);
  - `symphony_schema.extract_tickers` (`:585-613`);
  - `universe_provider.ALPACA_TRADING_BASE_URL` (`:58`);
  - `market_calendar.get_market_state` (`:132`);
  - `analytics.format_et_stamp` (`:2524`);
  - `database.insert_advisor_observation` and `get_advisor_observations_for_role` (`:1221`, `:1276`);
  - `timing_log.emit` (`timing_log.py:78`).
- First production uses: this is the first reader of Alpaca's single-asset endpoint, the first Discord sender that checks the response, and the first call of `extract_tickers` on a lone condition block.

### C2: app wiring and dashboard (AC-3, AC-4, AC-10; AC-6 S-6.1 to S-6.6; AC-7 S-7.2, S-7.4, S-7.5, S-7.7 to S-7.10, S-7.12; AC-8 S-8.1 to S-8.3, S-8.5 to S-8.8; AC-9 S-9.2)

- Files modified:
  - `app.py`: `_env_file_settings` and the delegating `_tenure_review_settings` (`:2653-2663`); the ticker-health constants, `_ticker_health_settings`, `_ticker_health_other_reader_running`, `_ticker_health_run_body`, `_run_ticker_health_tick`, `_ticker_health_catch_up_due` and `_start_ticker_health_catch_up`, placed after `:2155`; one slot line in `run_scheduler` (`:3078-3111`); `_ticker_health_page_view` and one `render_template` keyword (`:3807-3844`); one call in `__main__` after `:13312`.
  - `templates/index.html`: CSS after `:833`, markup between `:1315` and `:1317`.
  - `tests/app/test_scheduler_survival.py:130-139` gains the 01:30 row.
  - `tests/fixtures/page_explainers/ref_capture.json:971`: the re-pin commit, after GREEN.
- Files added: `tests/app/test_ticker_health_scheduling.py`, `tests/app/test_ticker_health_dashboard.py`, `tests/security/test_ticker_health_no_trade_boundary.py`. The existing `*no_trade_boundary*` files are not edited, because their content is pinned (`tests/ui/test_page_explainers_unchanged_surfaces.py:297-308`).
- Primitives imported: `market_calendar.most_recent_completed_session`, `session_close` (`:170-185`, `:82`); `tenure_review_runner.configured_account_ids`, `configured_account_labels` (`:82-89`); `tenure_review.REVIEW_RUN_STATE` (`:2019`); `_guard` (`app.py:1666-1673`).
- First production uses: the first daemon-start job besides the account refresh, and the first dashboard element fed by advisor observations.

## Per-Cycle Test Plan

Census of the entry points that write the run and announcement rows, or show what they hold, each a round-1 RED row:

| Entry point | Rules |
|---|---|
| Scheduled nightly start (`app._run_ticker_health_tick`, registered in `app.py:3078-3111` at 01:30) | AC-4, AC-6, AC-7, AC-9, AC-10 (and AC-1, AC-2, AC-5 through the run it starts) |
| Catch-up start at daemon start (`app._start_ticker_health_catch_up`, called from the `__main__` block after `app.py:13312`) | AC-4, AC-6, AC-7, AC-9, AC-10 |
| Dashboard page `/` (`app.dashboard`, `app.py:3598-3613`, through `_render_dashboard` `:3618-3844`) | AC-3, AC-4, AC-6, AC-9 |
| Discord message (runner step 11, POST to `DISCORD_WEBHOOK_URL`) | AC-3, AC-4, AC-5, AC-6, AC-9 |
| Daemon log (`alphabot.ticker_health`, child of `app.py:813`) | AC-9 |
| Shared run entry `advisors.ticker_health_runner.run_ticker_health_check` (reached by both starts through `app._ticker_health_run_body`) | AC-4, AC-6, AC-7, AC-9, AC-10 |

Scope:
- C1 is unit tests of the pure module plus integration through `run_ticker_health_check`, with every request answered by the router and `time.sleep` recorded.
- C2 is scheduler and daemon-start tests with a fixed ET clock, AST scans, and route tests of `GET /` over records the real C1 runner wrote.
- Screenshot and live-render evidence is the capturer's; the live check is the PM's (Merge Criteria).
- Expected counts come from the fixture trees through an independent walk in the test, never from literals copied from a run (the plan's Known-Unconfirmed on tree drift).

### C1
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-1.1 | AC-1 | unit | `tree_ticker_uses`, `page_view` line text | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests (pattern `tests/advisors/test_tenure_review_runner.py:156-200`) | case4 capture (README provenance) |
| S-1.2 | AC-1 | unit | `tree_ticker_uses` | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests | `real_tree_02_8FAXAnQmYi1INDubazeC.json` (trimmed real tree, operand coverage) |
| S-1.3 | AC-1 | unit | `tree_ticker_uses` | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests | `real_tree_09_n2ooAZTvBRN6ZzpMmWmU.json` (absent and false `rhs-fixed-value?` records) |
| S-1.4 | AC-1 | unit | `tree_ticker_uses` | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests | case4 capture (`rhs-fixed-value?` true, numeric `rhs-val`) |
| S-1.5 | AC-1 | unit | `tree_ticker_uses` | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests | case4 capture (binary-compound block with `%`) |
| S-1.6 | AC-1 | unit | `tree_ticker_uses` | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests | case4 capture (comma-joined `lhs-val`) |
| S-1.7 | AC-1 | unit | `tree_ticker_uses` | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests | case4 capture (nested compound) |
| S-1.8 | AC-1 | unit plus runner | `tree_ticker_uses`, `run_ticker_health_check` | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests, new `tests/advisors/test_ticker_health_runner.py` | PM score capture of `8FAXAnQmYi1INDubazeC`; captured SHNY asset record |
| S-1.9 | AC-1 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | a root node with no children, derived from the case4 root (derivation recorded in the router docstring) |
| S-1.10 | AC-1 | runner then view | `run_ticker_health_check`, `page_view` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router (asserts the stored not-checked entry, that no ticker of the tree is asked, and the `page_view` notice the route renders) | trimmed real tree with top-level `asset_class` set to `CRYPTO` (derived, labelled) |
| S-1.11 | AC-1 | unit | `extract_tickers` for each named caller | new: tests/advisors/test_ticker_health_tree.py, the walker's unit tests | every captured tree; expected sets from the ref-time `extract_tickers` stored as a sidecar of the tree file names and set sizes |
| S-2.1 | AC-2 | unit | `classify_asset_answer` | new: tests/advisors/test_ticker_health_verdicts.py, the Alpaca verdict unit tests | captured SHNY record |
| S-2.2 | AC-2 | unit | `classify_asset_answer` | new: tests/advisors/test_ticker_health_verdicts.py, the Alpaca verdict unit tests | captured active and not tradable record, or the active record with `tradable` set false, labelled derived |
| S-2.3 | AC-2 | unit | `classify_asset_answer` | new: tests/advisors/test_ticker_health_verdicts.py, the Alpaca verdict unit tests | captured SHNY record with `tradable` set true (derived, labelled) |
| S-2.4 | AC-2 | unit | `classify_asset_answer` | new: tests/advisors/test_ticker_health_verdicts.py, the Alpaca verdict unit tests | captured 404 record |
| S-2.5 | AC-2 | unit | `classify_asset_answer` | new: tests/advisors/test_ticker_health_verdicts.py, the Alpaca verdict unit tests | captured active record |
| S-2.6 | AC-2 | unit | `classify_asset_answer` | new: tests/advisors/test_ticker_health_verdicts.py, the Alpaca verdict unit tests | captured active record with `exchange` replaced (derived, labelled) |
| S-2.7 | AC-2 | runner | `run_ticker_health_check` (asserts no bars request) | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | captured active record |
| S-2.8 | AC-2 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | universe cache row seeded through `atlas_cache` (`advisors/universe_provider.py:65-68`) listing SHNY; captured SHNY record |
| S-2.9 | AC-2 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | router raising `requests.ConnectionError` and `Timeout` (pattern `tests/advisors/_composer_http_router.py:233`) |
| S-2.10 | AC-2 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | captured 401 record; 403 by status only |
| S-2.11 | AC-2 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | 429 and 503 replies (router `error_reply`, `:208`) mixed with captured 200s |
| S-2.12 | AC-2 | unit | `classify_asset_answer` | new: tests/advisors/test_ticker_health_verdicts.py, the Alpaca verdict unit tests | captured active record with `status`, then `tradable`, removed (derived, labelled) |
| S-2.13 | AC-2 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | the seeded universe cache row; asserts content and timestamp unchanged |
| S-2.14 | AC-2 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | none; blank key and blank secret each |
| S-2.15 | AC-2 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | PM roster capture listing two symphonies whose trees share a ticker |
| S-2.16 | AC-2 | unit | `classify_asset_answer` | new: tests/advisors/test_ticker_health_verdicts.py, the Alpaca verdict unit tests | statuses 302, 400, 422 by status only |
| S-5.1 | AC-5 | runner | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | SHNY score and asset captures; webhook URL in Discord's real path form with an invented id and token |
| S-5.2 | AC-5 | runner, two runs | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | as S-5.1 |
| S-5.3 | AC-5 | runner | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | two-symphony roster capture |
| S-5.4 | AC-5 | runner, two runs | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | second tree adds a dead ticker by the captured 404 record |
| S-5.5 | AC-5 | runner, three runs | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | SHNY record, then active record, then SHNY record |
| S-5.6 | AC-5 | runner, two runs | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | webhook 500, 429 and a raised `Timeout` |
| S-5.7 | AC-5 | runner, two runs | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | `webhook_url=None`, then set |
| S-5.8 | AC-5 | runner | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | one score read answered 503 |
| S-5.9 | AC-5 | runner, two runs | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | the announcement insert raises `SystemExit` after the 204 (crash simulated, never caused) |
| S-5.10 | AC-5 | runner, two runs | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | roster capture with the name changed on run 2 (derived, labelled) |
| S-5.11 | AC-5 | unit | `discord_messages` | new: tests/advisors/test_ticker_health_discord_text.py, the Discord text unit tests | 40 pairs built from captured roster names and tickers |
| S-5.12 | AC-5 | unit | `reporting.py` content | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router (LF sha256 pin of `reporting.py` at the ref, pattern `tests/test_mdd_window_truth_zero_diff_guard.py:60-80`) | none |
| S-6.7 | AC-6 | runner, two runs | Discord POST | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | SHNY record, then active record |
| S-7.3 | AC-7 | runner | `_composer_gate` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | clock crossing 09:30 ET between score reads |
| S-7.6 | AC-7 | runner | `_composer_gate` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | 429 on a roster read and on a score read |
| S-7.11 | AC-7 | runner | `run_ticker_health_check` | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router, `assert_requests_spaced` (`tests/advisors/_composer_http_router.py:459`) over half-open 60 second windows | roster capture of 11 symphonies |
| S-8.4 | AC-8 | runner | request ledger | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router (router `violations`, `tests/advisors/_composer_http_router.py:301`) | full happy-path captures |
| S-8.9 | AC-8 | static | migration listing | held in coverage: no migration is added; proven by `tests/ui/test_page_explainers_unchanged_surfaces.py:293-294` and `tests/app/test_hygiene_surface_unchanged.py:122-144` | none |
| S-9.1 | AC-9 | runner with `caplog` | daemon log | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | router service key and secret markers (`tests/advisors/_composer_http_router.py:51-52`); webhook URL marker |
| S-9.3 | AC-9 | unit plus runner | `discord_literal`, Discord POST | new: tests/advisors/test_ticker_health_discord_text.py, the Discord text unit tests | captured roster name with `@everyone` and `<@123456789012345678>` inserted (derived, labelled) |
| S-9.4 | AC-9 | runner | Alpaca read | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | score capture with one asset ticker set to `BRK/B`, `A B`, `..` (derived, labelled) |
| S-9.5 | AC-9 | runner | Alpaca read | new: tests/advisors/test_ticker_health_runner.py, the runner integration tests over the C1 router | captured 401 body with a `LEAKMARK` marker appended (pattern `tests/advisors/_composer_http_router.py:212`) |

### C2
| S id | AC | Test level | Entry point | Test file to extend (file:line) | Fixture (provenance) |
|---|---|---|---|---|---|
| S-3.1 | AC-3 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | SHNY score and asset captures; PM roster capture |
| S-3.2 | AC-3 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | roster capture with a holding entry for the dead ticker (derived, labelled) |
| S-3.3 | AC-3 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | roster capture with `holdings` removed, then a non-list (derived, labelled) |
| S-3.4 | AC-3 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | trimmed real trees giving 1 and 0, and 0 and 1 counts |
| S-3.5 | AC-3 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | two-symphony roster, three dead tickers by the 404 record |
| S-3.6 | AC-3 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | roster capture with `name` blank (derived, labelled) |
| S-3.7 | AC-3 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | two account rosters listing one id |
| S-3.8 | AC-3 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | captured active records for every ticker |
| S-3.9 | AC-3 | route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (router ledger empty during the request) | stored records from earlier runs |
| S-3.10 | AC-3 | route, auth on | `GET /` | `tests/app/test_dashboard_auth.py:87-111` fixture, new test in `tests/app/test_ticker_health_dashboard.py` | stored records |
| S-3.11 | AC-3 | route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote ; banner markup by `tests/ui/_page_explainers.py:275-277` against `ref_capture.json` fleet and market entries | `tests/fixtures/engine/fleet_correlation/alert_state_shape.json` (schema-derived, existing) |
| S-3.12 | AC-3 | browser render | `GET /` at 375 px | new: tests/app/test_ticker_health_dashboard.py, a CSS token test (wrap rule, no raw colour) beside the capturer's 375 px render | five stored lines |
| S-3.13 | AC-3 | route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (render while a held run is paused in the router) | stored records |
| S-4.1 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | 503 and a raised `Timeout` on one score read |
| S-4.2 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | 404 score reply (captured `case7a` 404 body) |
| S-4.3 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | captured `case7b` 401 body; 403 by status |
| S-4.4 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | `synthetic_non_json_200.txt` and a 200 JSON list |
| S-4.5 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | 503 on one account's roster |
| S-4.6 | AC-4 | two runs then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | SHNY captures, then 503 on that score |
| S-4.7 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | every Alpaca request raising `ConnectionError`; every roster 503 |
| S-4.8 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | 429 for k tickers |
| S-4.9 | AC-4 | route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | empty table |
| S-4.10 | AC-4 | route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`), clock 37 hours after the stored run | stored run |
| S-4.11 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | `account_ids=[]` |
| S-4.12 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | roster capture with `symphonies` emptied (derived, labelled) |
| S-4.13 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | error body with `LEAKMARK` appended (`tests/advisors/_composer_http_router.py:212`) |
| S-4.14 | AC-4 | runs then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`), runs on Friday, Saturday and Sunday nights and a holiday | captured records |
| S-4.15 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | 302 (`redirect_reply`, `:228`), captured `case7c` 400, 422 |
| S-4.16 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | roster entry id `a.b/c` (derived, labelled) |
| S-4.17 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | captured `case7b_statistics_invalid_credentials_401.json`; 403 by status |
| S-4.18 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | 404 on a roster |
| S-4.19 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | 302, 400, 422 on a roster |
| S-4.20 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | non-roster 200; account id `../x` |
| S-4.21 | AC-4 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | 429 on the first of two rosters |
| S-6.1 | AC-6 | two runs then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | SHNY record, then active record |
| S-6.2 | AC-6 | two runs then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | score capture, then the same tree with SHNY nodes removed (derived, labelled) |
| S-6.3 | AC-6 | two runs then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | second score read 503 |
| S-6.4 | AC-6 | two runs then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | second SHNY asset read 503 |
| S-6.5 | AC-6 | two runs then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | second roster without the symphony (derived, labelled) |
| S-6.6 | AC-6 | two runs then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote (client and seeding as `tests/ui/test_cycle_2_dashboard.py:124-131`, `tests/ui/_shell_support.py:108`) | two trees sharing SHNY; one edited |
| S-7.2 | AC-7 | scheduler | `_ticker_health_run_body` | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests (pattern `tests/app/test_regime_label_scheduling.py:71-135`) | ET clock inside a session |
| S-7.4 | AC-7 | scheduler | both starts | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests (pattern `tests/app/test_startup_account_refresh.py:216-262`) | router held mid-run |
| S-7.5 | AC-7 | scheduler | `_ticker_health_other_reader_running` | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests | live threads named as at `app.py:2132`, `:3008`, `:12562`, `:12597`; `REVIEW_RUN_STATE.try_start`; `_RETIREMENT_RUN_LOCK` held |
| S-7.7 | AC-7 | run then route | runner interrupted, `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | a router reply raising `SystemExit` mid-run (crash simulated) |
| S-7.8 | AC-7 | scheduler | `run_scheduler` | `tests/app/test_scheduler_survival.py:369-503` loop harness | ET clock inside a session |
| S-7.9 | AC-7 | scheduler | `run_scheduler` | `tests/app/test_scheduler_survival.py:130-168` (`_EXPECTED` gains the 01:30 row) | none |
| S-7.10 | AC-7 | run then route | runner with a raising collaborator, `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | `tree_ticker_uses` patched to raise `RuntimeError` |
| S-7.12 | AC-7 | scheduler then run | `_run_ticker_health_tick` | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests | captured happy path |
| S-8.1 | AC-8 | AST scan | every new function | new: tests/security/test_ticker_health_no_trade_boundary.py, the feature's no-trade scan (pattern `tests/security/test_regime_label_job_no_trade_boundary.py:97-305`) | module sources |
| S-8.2 | AC-8 | AST scan with planted statements | every new function | new: tests/security/test_ticker_health_no_trade_boundary.py, the feature's no-trade scan (pattern `:434-486`) | planted `save_symphony`, `go-to-cash`, `perform_account_liquidation`, `/deploy` |
| S-8.3 | AC-8 | static | engine files | existing `tests/test_mdd_window_truth_zero_diff_guard.py:74-75` (coverage row cites it) | none |
| S-8.5 | AC-8 | end to end | `_tenure_review_job` | `tests/app/test_tenure_review_end_to_end.py:196` (run the review with and without stored ticker-health rows and a check run, compare stored review records without run id and times) | captured tenure records |
| S-8.6 | AC-8 | route | `GET /health` on-box and remote, `GET /api/header-status` | `tests/app/test_health_endpoint.py:80-199`, `tests/app/test_header_status_route.py:80-118` | stored run with dead tickers |
| S-8.7 | AC-8 | static | `_SETTINGS_WRITE_ALLOWLIST` | existing `tests/app/test_hygiene_surface_unchanged.py:91` (coverage row cites it) | `tests/fixtures/hygiene_waves_3_4/app_database/` (recorded) |
| S-8.8 | AC-8 | AST scan | every new function | new: tests/security/test_ticker_health_no_trade_boundary.py, the feature's no-trade scan | module sources |
| S-9.2 | AC-9 | run then route | `GET /` | new: tests/app/test_ticker_health_dashboard.py, the dashboard route tests over records the real runner wrote | roster name with `<img src=x onerror=alert(1)>` (derived, labelled) |
| S-10.1 | AC-10 | scheduler then route | `_run_ticker_health_tick`, `GET /`, Discord POST | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests | SHNY captures; clock from D's close to D+1's open |
| S-10.2 | AC-10 | scheduler | `_run_ticker_health_tick` | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests | a Saturday, a Sunday and 2026-11-26 (NYSE holiday) |
| S-10.3 | AC-10 | daemon start | `_start_ticker_health_catch_up` | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests | clock 06:00 ET trading day |
| S-10.4 | AC-10 | daemon start | `_start_ticker_health_catch_up` | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests | interrupted run (no row) |
| S-10.5 | AC-10 | daemon start | `_start_ticker_health_catch_up` | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests | stored completed run after the close |
| S-10.6 | AC-10 | daemon start | `_start_ticker_health_catch_up` | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests ; `__main__` order by AST (`tests/app/test_startup_account_refresh.py:337-387`) | clock inside a session |
| S-10.7 | AC-10 | scheduler | both starts | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests | catch-up run held in the router |
| S-10.8 | AC-10 | scheduler | slot time | new: tests/app/test_ticker_health_scheduling.py, the slot, catch-up and wait tests (pattern `tests/app/test_regime_label_scheduling.py:221`) | UTC host in EST and EDT, ET host, and 2026-11-27 (13:00 close) |

## Per-Cycle Risk Register

| Cycle | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| C1 | A condition form the walker misses hides a dead ticker | Low | High | The walker reads every form found in the 12 captured trees: legacy `lhs-val`, an `rhs-val` with `rhs-fixed-value?` absent, false or true, compound blocks, and binary-compound lists with `%`. S-1.2 to S-1.7 cover each form; DC-1's sibling grep runs in the sufficiency sweep |
| C1 | A compound if-child's plain `lhs-val` names a ticker its block lacks, so ignoring labels misses it | Low | Medium | Measured: 0 of 34 compound if-children in the captures; S-1.6 pins the rule; the PM's live check compares the warnings with Alpaca by hand |
| C1 | The Alpaca paper host's 404 shape or status for an unlisted symbol differs from the assumption | Medium | High | The PM capture records the real reply before C1; S-2.4 and S-2.16 test against it; any status other than 404 reads not checked, never dead |
| C1 | The live ticker total runs the Alpaca phase long | Medium | Low | 0.5 s spacing and per-run asking once; 1,500 tickers is about 13 minutes, inside the 01:30 to 04:00 ET window on any host |
| C1 | Composer `/score` shares the 25-a-minute budget with the minute account refresh, which runs at all hours (`app.py:3085-3089`) | Medium | Medium | 6.0 s client spacing; the first 429 stops all Composer requests (S-7.6); unread symphonies carry their warnings (S-6.3) |
| C1 | The announcement derivation misreads after a run fails between its row and its posts | Low | Medium | Posts follow the row; an unposted pair stays `announced` false and posts on the next completed run (S-5.6); a post without its row repeats once (S-5.9, Q-6) |
| C1 | Importing `composer_portfolio_client` brings the engine module into the import graph (`advisors/composer_portfolio_client.py:21`) | Certain | Low | Same allowance as the tenure scans (`tests/security/test_retirement_recommender_no_trade_boundary.py:42-48`); the new scan forbids a direct engine import in both new modules |
| C2 | The 01:30 slot fires inside an existing scheduler test with copied live keys and runs a real check | Medium | High | Tests point `app.ENV_FILE_PATH` at a temp file; the catch-up start is in `__main__`, not `run_scheduler`; the batch A author greps `run_pending` and forced-due callers (`tests/app/test_scheduler_survival.py:109-118`) and patches `_run_ticker_health_tick` there |
| C2 | Changing `run_scheduler` breaks the AST digest pin (`tests/ui/test_page_explainers_unchanged_surfaces.py:334-335`, `tests/ui/_page_explainers.py:517-523`) | Certain | Low | A test-only re-pin commit after GREEN on the PM's recorded word (Items Raised 1); no other pinned file changes |
| C2 | Every existing world with no ticker-health row now renders "No ticker check has completed yet.", which moves layout or text captures of `/` (`tests/ui/_page_explainers.py`, `tests/dashboard/_first_paint_dom.py`, `tests/ui/_alignment.py`) | High | Medium | Batch B runs `tests/ui` and `tests/dashboard` named files before GREEN. Where a capture moves, it seeds one completed clean run through the real runner in the shared world seeding (`tests/ui/_shell_support.py:108`), never re-pins the capture |
| C2 | The other-reader predicate keys on thread names that a later rename breaks silently | Medium | Medium | The scan file asserts each name in `_TICKER_HEALTH_WAITS_FOR_THREADS` is the literal `name=` of a `threading.Thread` call in `app.py` (AST), so a rename fails a test |
| C2 | The weekly suggestions timer has `Persistent=true` (`docs/DEPLOYMENT.md:364`) and can fire at a boot inside the check's window, an out-of-process overlap the predicate cannot see | Low | Low | The first 429 stops the check (S-7.6); the stale notice covers a lost night |
| C2 | `_tenure_review_settings` delegation changes the review | Low | Medium | Body lifted verbatim; S-8.5 compares stored review records with and without the feature |

## Cycle-Close Criteria

- Every scenario of the cycle has a row in `records/coverage.json` under `.claude/review/<HEAD-sha12>/`: `tested` with its test file and name, or held with its reason (S-8.9 held: no migration). `plan-trace check-coverage` runs clean against the plan and this approach.
- RED was committed before GREEN, and each RED test was seen failing for the stated reason. At least one test drives the real entry point: `run_ticker_health_check` in C1; `_run_ticker_health_tick`, `_start_ticker_health_catch_up` and `GET /` in C2.
- The cycle's named test files pass under `pytest -n0`, with these existing files:
  - C1: `tests/advisors/test_tenure_review_runner.py`, `tests/advisors/test_composer_portfolio_client.py`, `tests/advisors/test_universe_provider.py`, `tests/security/test_retirement_recommender_no_trade_boundary.py`, `tests/test_mdd_window_truth_zero_diff_guard.py`.
  - C2: `tests/app/test_scheduler_survival.py`, `tests/app/test_regime_label_scheduling.py`, `tests/app/test_incubation_scheduling.py`, `tests/app/test_retirement_recommendations_scheduling.py`, `tests/app/test_startup_account_refresh.py`, `tests/app/test_tenure_review_end_to_end.py`, `tests/app/test_tenure_review_run_route.py`, `tests/app/test_health_endpoint.py`, `tests/app/test_header_status_route.py`, `tests/app/test_hygiene_surface_unchanged.py`, `tests/app/test_dashboard_no_order_path.py`, `tests/ui/test_page_explainers_unchanged_surfaces.py`, `tests/dashboard/test_fleet_banner.py`, `tests/security/test_retirement_action_no_trade_boundary.py`, `tests/security/test_regime_label_job_no_trade_boundary.py`.
- `ruff format --check` and `ruff check` are clean (line length 100). Every new constant carries a source comment.
- `git diff c3e12882 -- alpha_bot_execution.py math_engine.py database.py reporting.py migrations/ advisors/symphony_schema.py advisors/universe_provider.py advisors/tenure_review.py advisors/tenure_review_runner.py` is empty. C1 alone adds `is_plain_id` to `advisors/composer_portfolio_client.py` and nothing else there.
- New fixture files sit in folders whose `PROVENANCE.md` names each record's case and whether it is captured or derived from a captured record.
- C2 only: the `scheduler_digest` re-pin is a test-only commit after GREEN, and no other recorded hash in `ref_capture.json` changes.
- The sufficiency sweep runs the plan's DC-1 to DC-11 sibling queries and records each result in the coverage map.

## Merge Criteria

Delivery: no PR (project mandate). The PM merges C2's branch (which carries C1) into `feat/live-tree-ticker-health`, pushes the head to an `assembly/**` branch for CI, and fast-forwards `main` only when CI is green on that exact SHA and every reviewer verdict on both heads reads PASS.

The assembled head must pass:
- CI full gate green on the exact assembled SHA: ruff format, ruff check and the full pytest under CI's own worker count.
- Migration proof: none added. `tests/ui/test_page_explainers_unchanged_surfaces.py:293-294` and `tests/app/test_hygiene_surface_unchanged.py:122-155` pass unchanged.
- Persistence roundtrip: one `-n0` run of `tests/advisors/test_ticker_health_runner.py` and `tests/app/test_ticker_health_dashboard.py` on the assembled tree. A run row the runner writes must be the row `read_latest_runs` returns and the page renders, and an announcement row must suppress the next run's post of the same pair.
- Deploy outside market hours, after a closed-market check (`market_calendar.get_market_state` reads `closed_frozen` and the day is checked against the NYSE holiday calendar). The PM reads the droplet host timezone (`timedatectl`, read-only) and records it before the first 01:30 run.
- PM live functional check on the droplet after the first nightly run (or the catch-up run at the deploy restart):
  - The newest `TICKER_HEALTH_RUN` row (read-only `SELECT`) reads `completed`.
  - `/` shows one line for each live symphony whose tree names a ticker Alpaca reports inactive, not tradable or not listed, Planet LQD and Trolling with SHNY among them while SHNY stays in their trees. Each line is checked by hand against `GET /v2/assets/<symbol>` on the paper host and against the symphony's tree in Composer.
  - One Discord message names each pair. A second night posts nothing for the same pairs.
  - The daemon log holds one start and one end line per run with no name, ticker, key or URL.
- Backlog lines at merge, per the plan's Follow-Ups: NB-18, NB-19, NB-20, and the `logic_holdings` key with no writer.

## Items Raised to the PM

1. Re-pin of a protected digest. `run_scheduler` gains the 01:30 registration, which changes the AST digest pinned at `tests/fixtures/page_explainers/ref_capture.json:971` (checked by `tests/ui/test_page_explainers_unchanged_surfaces.py:334-335`, whose module says to stop and ask before re-pinning). The design asks for one test-only re-pin commit of that one value after C2's GREEN. Placing the registration outside `run_scheduler` would dodge the pin and leave the slot outside the existing slot tests, so it is not proposed.
2. Wording the plan does not fix. The design uses these texts inside the plan's own templates ("Ticker check unavailable: {reason}." and "Not checked: ... ({reason}).") and asks the PM to accept them:
   - run-level: "market open" (skipped), "another Composer job was still running" (skipped after the 45 minute wait), "no account's symphony list could be read", and the shared account reason when all accounts fail alike;
   - per symphony: "market opened" (S-7.3), "another Composer job was running";
   - per ticker: "Not checked: T in Y (ticker form not checked)." (S-9.4), "1 ticker in Y could not be checked (...)", and "Alpaca unreachable" for a transport failure (S-2.11 names only rate limit and unavailable);
   - render failure: "The ticker check result could not be read."
3. D-6 corner cases, applied literally:
   - A run whose rosters are read and whose every score read fails has no ticker to ask, so it completes and resets the stale clock, with every symphony named not checked.
   - A run with no Alpaca key or secret ends `unavailable` before any request, even on a night when no ticker would have been asked (S-2.14).
   Confirm both, or rule that a run judging no symphony while the rosters list some does not complete.
4. The set of Composer readers the check waits for. It waits for the threads and slots of the 03:30 incubation tick, the 03:45 tenure and retirement tick, the retirement Run now and the tenure review Run now, as ruled. These other in-process Composer readers are neither waited for nor blocked; the first 429 stops the check:
   - Strategy Builder run (`app.py:12006`);
   - Frontrunner Builder executor (`app.py:12279`);
   - asset swap and logic change evaluate (`app.py:11391`, `:11749`);
   - suggest (`app.py:12745`);
   - EOD tasks (`app.py:8593`);
   - the minute account refresh (`app.py:3085-3089`).
   The weekly suggestions process is avoided by the 01:30 slot, with the boot-time `Persistent=true` firing as residual risk. Confirm that the waited set is the four named forms.
5. One addition to a consumed module. NB-21 lists `advisors/composer_portfolio_client.py:151-223` as consumed, not changed. The design adds one public `is_plain_id` beside `is_valid_spacing_seconds` (`:99-106`), so S-4.16's "symphony id not usable" uses the client's own plain-id rule instead of a second copy. The four reads and the tenure review's use stay unchanged.
6. The census entry point "Shared run entry `advisors.ticker_health_runner.run_ticker_health_check` (reached by both starts through `app._ticker_health_run_body`)" is not a column of the plan's Rule-by-Entry-Point Matrix. Both matrix start columns reach the matrix rules through it, so the design tests AC-4, AC-6, AC-7, AC-9 and AC-10 at it in C1 and at the two starts in C2. No matrix column is proposed: it is not an operator-facing entry point.
7. Fixture capture before C1: the Alpaca asset records and the Composer score and roster records listed in Execution Model, by the PM through `/api-fixture`, read-only and after the close.
