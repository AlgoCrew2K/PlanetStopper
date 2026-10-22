# Feature: Retirement Recommender — real data depth + visible nightly runs (Cycle 2e)
Status: ready
Created: 2026-10-22

> Hand-written in `/scaffold` format: the `/scaffold`, `/tdd`, `/tdd-implement` and `/tdd-finalize`
> skills are not installed in the cloud container this cycle runs in. Teammates follow the same
> protocol by hand (RED tests + `.claude/tdd-handoff.md`, implementer reads the handoff only).

## Summary
The Retirement Recommender (Cycles 2a–2d, PRs #138–#141, merged and deployed) has never surfaced a
recommendation on the droplet, and the UI cannot show whether it even ran. Operator, 2026-10-22:
"It's not exposed on the droplet or in the UI so it's not done."

Two root causes, both verified against the code:

1. **Data depth.** The only series source is `analytics.get_symphony_bot_and_held_daily_returns`
   over `shadow_history`, which starts at the 2026-06-22 go-live floor (~87 trading days on
   2026-10-22). The AC-6 structural-redundancy gate needs `ceil(STRESS_WINDOW_FRACTION * n) >=
   STRESS_MIN_OBS`, i.e. **n >= 181 aligned days** (`advisors/retirement_recommender.py:396-399`),
   so it fails closed for every pair until roughly March 2027. No pair can ever pass today.
2. **No run trace.** The 03:45 tick persists only recommendation rows. A night that screens 11
   symphonies and blocks every pair writes nothing, so the panel shows the same static
   "No retirement recommendations yet" whether the tick ran, crashed, or never fired.

Fix: source each symphony's daily returns from Composer's account-scoped realized series
(`GET /api/v0.1/portfolio/accounts/{account-id}/symphonies/{symphony-id}`, live-probed 2026-09-03:
538 daily points back to 2024-07-12, zero truncation — `docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md`
E-24), persist a run summary every night, render that summary in the panel, and add a "Run now"
button so the operator can see a run without waiting for 03:45. **The gates, thresholds and
composite math do not change.**

[PM-ASSUMED] **Basis change is faithful to Cycle 2a's AC-2, not a relaxation of it.** AC-2 pinned the
basis to "actual-traded, never the if-held counterfactual". The droplet runs `LIVE_EXECUTION=False`
(per the project record — `DE-AUDIT-BL4-001`, the 2026-09-04 deploy notes; not re-verified live by
this cycle), so `shadow_return` there is a *simulation* of what the bot would have done.
Composer's `deposit_adjusted_series` is what the account actually earned (it includes Planet
Stopper's real exits whenever `LIVE_EXECUTION=True`), so it is the truer "actual-traded" basis.
The if-held counterfactual (`current_return`) stays excluded.

## Acceptance Criteria
- [ ] **AC-1 (Composer daily-series client).** New `advisors/composer_series_client.py` exposing
  `fetch_symphony_daily_returns(account_id: str, symphony_id: str) -> SeriesResult`.
  `SeriesResult` is a dataclass: `returns: dict[str, float]` (ISO `YYYY-MM-DD` America/New_York
  calendar date → simple daily return in **percent** scale, matching
  `get_symphony_bot_and_held_daily_returns`'s `_pct` convention that
  `analytics.compute_quantstats_metrics` divides by 100), `points: int`, `first_date`,
  `last_date` (`str | None`), `error: str | None`. Requests
  `GET {COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphonies/{symphony_id}` with
  `get_composer_headers()`, an explicit timeout, and a bounded retry on 429/5xx/transport errors
  (429 honours `Retry-After`, capped). Parses `epoch_ms` + `deposit_adjusted_series` (never the
  raw `series`, which jumps on deposits/withdrawals); return for day t =
  `(v_t / v_{t-1} - 1) * 100` over consecutive points. Never raises; every failure is
  `SeriesResult(returns={}, error=<short reason>)` where the reason carries at most the HTTP status
  or `type(exc).__name__` (D-1), never response bodies, headers, or keys.
- [ ] **AC-2 (recommender source seam).** `build_recommendations` gains a keyword
  `source: str = "shadow"` (`"shadow"` | `"composer"`). `"shadow"` is byte-for-byte the current
  behaviour (all existing tests keep passing unchanged). `"composer"` builds each live symphony's
  series via AC-1 using the account id at `bot_state[sym]["account"]`, keeps the most recent `days`
  (default `RETIREMENT_LOOKBACK_DAYS`) observations, and sets `basis_label` to
  `"realized daily returns (Composer, deposit-adjusted)"`. A symphony with no account id or a
  failed/too-short series (< 2 returns) is excluded from that run — never mixed with a shadow
  series (one basis per run). Composer fetches are spaced >= 1 s apart (Composer's 1 req/s limit).
  Gate thresholds, composite weights, screening and candidate selection are unchanged.
- [ ] **AC-3 (run summary).** New `build_recommendations_with_summary(**same kwargs) ->
  tuple[list[dict], dict]`; `build_recommendations` returns its first element (no behaviour
  change). Summary keys: `run_id` (uuid4 hex), `run_at` (UTC ISO-8601), `source`, `basis_label`,
  `status` (`"ok"` | `"insufficient_symphonies"` | `"series_unavailable"` | `"internal_error"`),
  `error_type` (`type(exc).__name__` or `None`), `roster_size`, `symphonies_with_series`,
  `symphonies_without_series` (list of `{"symphony_id", "reason"}`), `series_days_min`,
  `series_days_max` (`None` when no series), `lookback_days`, `pairs_evaluated` (all pairs among
  symphonies with a series), `pairs_screened` (cleared `CORRELATION_SCREEN_THRESHOLD`),
  `blocked_uncertainty`, `blocked_stress_too_thin` (stressed correlation `None`),
  `blocked_stress_below_threshold`, `blocked_no_candidate`, `recommendations`,
  `screen_threshold`, `stress_gate_min_aligned_days` (derived from the constants — 181 today —
  never a second literal). Counts are per pair and reconcile: `pairs_screened = blocked_* sum +
  pairs that produced a recommendation before per-candidate dedup`. Never raises: an internal
  failure returns `([], summary)` with `status="internal_error"`.
- [ ] **AC-4 (tick persists the summary; run-scoped recs).** `_retirement_recommender_tick_worker`
  calls `build_recommendations_with_summary(source="composer")`, stamps the summary's `run_id`
  onto every rec, persists recs as today, then ALWAYS persists exactly one
  `RETIREMENT_RUN_SUMMARY` `advisor_observations` row (also on 0 recs and on internal error) with
  `summary["recommendations"]` = the number of rec rows actually persisted. `RETIREMENT_RUN_SUMMARY`
  is NOT added to `_ADVISOR_ROLES` (same convention as `RETIREMENT_RECOMMENDATION`).
  `run_id` is excluded from `_canonical_evidence_snapshot`, so a stable pair still reuses its prior
  explanation (no new nightly LLM spend).
- [ ] **AC-5 (read path).** New `_fetch_retirement_last_run()` returns the latest
  `RETIREMENT_RUN_SUMMARY` raw_response or `None` (raises on DB failure; callers degrade).
  `_fetch_retirement_recommendations` returns recs whose `run_id` equals the latest summary's
  `run_id` when one exists (so a 0-rec "Run now" at 14:00 correctly hides that morning's cards);
  when no summary exists or the latest summary has no `run_id`, it keeps today's latest-calendar-
  date filter (legacy rows). `GET /api/retirement-recommendations` gains an additive `last_run` key
  (`null` when none); `recommendations` is unchanged in shape.
- [ ] **AC-6 (panel shows every run).** The Overview-tab Retirement Recommendations panel renders a
  `data-testid="retirement-last-run"` block ABOVE the cards: run time; source/basis; symphonies
  analysed vs roster; series depth range; pairs screened at the threshold; blocked counts per gate,
  with the stress-gate minimum (`stress_gate_min_aligned_days`) stated when
  `blocked_stress_too_thin > 0`; recommendation count; excluded symphonies by display name with
  reason. Distinct honest states for: never run (`"Not run yet — runs nightly at 03:45, or use Run
  now."`), `series_unavailable`, `insufficient_symphonies`, `internal_error`. All text
  autoescaped, no `| safe`.
- [ ] **AC-7 ("Run now").** `POST /ai-advisor/retirement/run`: CSRF-protected via the existing
  global hook, auth via the global hook, NOT in `_SETTINGS_WRITE_ALLOWLIST`, no `LIVE_EXECUTION`
  read. Single-flight: a module-level lock shared with the 03:45 tick; if a run is in flight →
  `409 {"status": "already_running"}`, else start the same worker on a daemon thread →
  `202 {"status": "started"}`. The scheduled tick skips (logs INFO) when a run is in flight. Panel
  gains a `data-testid="retirement-run-now-btn"` button; `static/ai_advisor.js` POSTs with the
  CSRF header the file already uses, disables the button, writes status via `textContent`, polls
  `GET /api/retirement-recommendations` every 5 s for up to 3 min until `last_run.run_id` changes,
  then reloads the page; on timeout it says the run is still going and to reload later.
- [ ] **AC-8 (no-trade boundary).** `tests/security/test_retirement_action_no_trade_boundary.py`
  Group E gains the new route + worker entry points; a new source scan proves
  `advisors/composer_series_client.py` contains no `/deploy/`, `/trading/`, `go-to-cash`,
  `execute_sell_to_cash`, `LIVE_EXECUTION`, `composer_draft_client`, `save_symphony`, and issues
  only GET requests. Importing `COMPOSER_BASE_URL` / `get_composer_headers` from
  `alpha_bot_execution` is allowed (precedent: `advisors/composer_backtest_client.py`).
- [ ] **AC-9 (replace the byte pin with the invariant it protected).**
  `tests/app/test_retirement_producer_explainer_wiring.py`'s golden-SHA pin on
  `retirement_recommender.py` (Cycle 2b AC-2, "stays LLM-free") is replaced by an AST check that
  the module imports nothing LLM-related (`anthropic`, `advisors.retirement_explainer`,
  `model_config`, `advisor_chat`) — the intent of the pin, without forbidding this cycle's change.
- [ ] **AC-10 (fixture).** `tests/fixtures/composer/symphony_daily_series_v1.json` shaped exactly as
  the documented response (`epoch_ms`, `series`, `deposit_adjusted_series`), with
  `_fixture_provenance: "synthetic-from-docs"` and a note to recapture with `/api-fixture` from a
  network-enabled session. [PM-ASSUMED] no real capture is possible this cycle — the cloud
  container's network policy blocks `api.composer.trade` (verified 2026-10-22).

## Architecture
- **New** `advisors/composer_series_client.py` (AC-1): pure-ish client; `requests.get` patched in
  tests; module-level named constants for timeout, retry backoff, Retry-After cap, epoch-ms sanity
  floor, ET timezone. Off the execution path; imported lazily by the recommender.
- `advisors/retirement_recommender.py` (AC-2/AC-3): `source` seam, `_composer_series_for(...)`
  helper, `build_recommendations_with_summary`, gate-outcome counting inside the existing pair
  loop, `STRESS_GATE_MIN_ALIGNED_DAYS` derived constant. No LLM import (AC-9).
- `app.py` (AC-4/5/7): tick worker + shared single-flight lock, `_fetch_retirement_last_run`,
  run-scoped filter in `_fetch_retirement_recommendations`, `last_run` on the API, `run_id`
  excluded in `_canonical_evidence_snapshot`, new `POST /ai-advisor/retirement/run`,
  `ai_advisor_tab()` passes `retirement_last_run` to the template.
- `templates/ai_advisor.html` (AC-6/7): last-run block + Run-now button, design tokens only.
- `static/ai_advisor.js` (AC-7): `retirementRunNow()` + poll.
- Byte-frozen: `alpha_bot_execution.py`, `math_engine.py`, `advisors/retirement_explainer.py`,
  `advisors/retirement_checklist.py`, `advisors/correlation_diagnostic.py`, `database.py` (no
  migration: `advisor_observations` already stores arbitrary roles).

## Edge Cases
- `epoch_ms` vs seconds: reject values below the epoch-ms floor (< 1e11) as malformed.
- Several points on one ET date: keep the last. Points dated today (ET) are dropped, so a midday
  "Run now" never uses a partial day.
- `v_{t-1} <= 0` or a non-finite value (symphony emptied then re-funded): skip that return, never
  divide by zero or fabricate.
- `epoch_ms` and `deposit_adjusted_series` lengths differ, keys missing, wrong types → `error`.
- Symphony invested recently (e.g. 2026-04-01, ~140 days): its pairs are honestly counted under
  `blocked_stress_too_thin`; older pairs (250-day window → k = 13 stress days) are evaluable.
- Composer down at 03:45: every symphony lands in `symphonies_without_series`, status
  `series_unavailable`, 0 recs, summary still persisted and rendered.
- "Run now" while the 03:45 tick runs (or double-click): 409, no second thread, no duplicate rows.
- Legacy rec rows without `run_id` and no summary yet: panel/API behave exactly as before.
- Display names: excluded-symphony list uses the same `resolve_incumbent_display_name` helper with
  hash fallback.

## Security Considerations
- Read-only Composer GET only; no deploy/trading endpoint reachable (AC-8 scan).
- Credentials never logged; D-1 error strings only (`type(exc).__name__`, HTTP status).
- New POST route: CSRF + auth via the existing global hooks; advisory-only; cannot change
  settings or trade. Single-flight lock prevents request-driven fan-out of Composer calls.
- Template output autoescaped; JS writes via `textContent` only.

## Testing Strategy
- Client: fixture-driven parsing, percent-scale golden values hand-computed from a 5-point series,
  ET-date conversion across the UTC midnight boundary, duplicate-date, today-dropped, non-positive
  value, length mismatch, 429 + Retry-After, 500 retry exhaustion, timeout, D-1 reason strings.
- Recommender: `source="composer"` with a patched fetcher producing a constructed correlated pair
  over 250 days → a recommendation with the new `basis_label`; summary counts for each gate
  outcome; `source="shadow"` unchanged (existing suite); summary on internal error.
- App: tick persists summary on 0 recs / recs / error; run_id stamped and excluded from reuse
  snapshot; API `last_run`; run-scoped filter incl. legacy fallback; route 202/409/CSRF-rejected;
  template render of each panel state (Flask test client, real render, escaped content).
- JS: covered by `tests/js_syntax/test_js_syntax.py` (do not add per-file `node --check` tests).
- Security: AC-8 scans, mutation-checked (inject a forbidden token, confirm the scan fails, revert).
- Run targeted files with `-n0`. Never a bare/uncapped full-tree run.

## Scope Boundaries
- No change to gate thresholds, stress fraction, composite weights, or candidate selection.
- No change to the explainer, approve/reject routes, checklist, or `retirement_decisions`.
- No change to the Correlations tab or `advisors/correlation_diagnostic.py`.
- No shadow↔Composer stitching or fallback mixing within a run.
- No schema migration. No engine (`alpha_bot_execution.py` / `math_engine.py`) diff.
- Out of scope, tracked: recapturing a real Composer series fixture (AC-10 follow-up); surfacing a
  retirement indicator in the global header.
