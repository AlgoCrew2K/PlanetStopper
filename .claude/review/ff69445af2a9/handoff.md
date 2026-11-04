# Handoff: C2 batch A RED (quant-test-writer rg-c2a-red), RED SHA ff69445af2a9, round 1

Plan: feature-plans/regime-label-producer.md; approach: feature-plans/approach/regime-label-producer.md (### C2).
Stub: regime_label_job.py at repo root (committed 3d53fbef; every function body raises NotImplementedError; constants, dataclasses and imports are real). Replace the bodies; keep the names and shapes.

## Contract the tests pin (beyond the approach text)
- `target_session(start: aware datetime) -> date`: first NYSE trading day whose 09:30 ET open is strictly after `start` (09:30:00 exactly rolls to the next day).
- `window_sessions(D) -> list[date]`: ONE parameter (the approach text has a second `newest_eligible` parameter; it is dropped here, and sessions later than `newest_eligible` are excluded in `compute_label`). 31 NYSE sessions oldest first: the 30 strictly before D plus one earlier, contiguous in trading days.
- `fetch_sip_daily_closes(tickers, start: date, end: aware UTC datetime) -> SipCloses(closes, failed)`.
  - `start` is the oldest fetched session; the request `start` param is its midnight ET as `%Y-%m-%dT%H:%M:%SZ`. `end` is formatted `%Y-%m-%dT%H:%M:%SZ`.
  - GET `f"{synthetic_history.ALPACA_BASE_URL}/stocks/bars"` (lazy import, so tests can patch the module attributes `ALPACA_BASE_URL` and `get_alpaca_headers`), kwargs `headers`, `params`, `timeout=REQUEST_TIMEOUT_SECONDS`, `allow_redirects=False`. params: `symbols` (comma list, unique, at most SIP_BATCH_SIZE per request, batches in the order of the sorted unique tickers), `timeframe=1Day`, `adjustment=split`, `feed=sip`, `limit=10000`, `start`, `end`, and `page_token` only on later pages.
  - Retry 429/500/502/503/504 and transport errors up to MAX_ATTEMPTS attempts: sleeps 1.0 then 2.0 (doubling, cap RETRY_AFTER_CAP_SECONDS) when there is no Retry-After; an integer Retry-After clamps into [0, cap]. Any other non-200 status (400, 401, 403, 404, 422) fails at once with no sleep. One short pause (0 < s <= 1.0) between pages.
  - A failed page fails the whole batch: every ticker of that batch is in `failed` as `str(status)` or the exception class name and has no usable closes. A 200 omitting a ticker is NOT a failure (no entry in `failed`, no closes).
  - `closes[ticker][ET bar date] = close`.
  - Calls `timing_log.emit("alpaca", "GET", "/v2/stocks/bars", status=<int> | error=<ClassName>, ms=<int>)` once per attempt; no symbols, keys or bodies.
- `compute_label(holdings, closes, D, newest_eligible) -> LabelComputation(label, reason, window_first, window_last, window_count, series)`: series = the 20 most recent computable sessions among `window_sessions(D)[1:]`, as fractions, oldest first; computable = every holding has a close on s and on the previous session, and s <= newest_eligible; a bad close (NaN, inf, <= 0) gives a NaN return kept in the series; fewer than 20 computable gives (None, "insufficient_history"); classifier None gives (None, "no_classifier_label"); window_first/last are the dates of the first/last series session; window_count = 20 whenever a series was formed. Never fill a missing close; never mutate inputs.
- `run_regime_label_job`: `newest_eligible = market_calendar.most_recent_completed_session(job_start - SIP_END_LAG)`; request end = `job_start - SIP_END_LAG`, fixed once (the clock may move later); request start = `window_sessions(D)[0]`; a symphony with a ticker in `sip.failed` gets reason `price_fetch_failed` and `detail` = the failed text; the outcome fields `label`, `reason`, `window_first`, `window_last` mirror `compute_label`; the row is `database.save_regime_label(id, label, D.isoformat())`.

## Files (run each with `-n0`, explicit path)
tests/regime_label_job/{test_target_session,test_label_computation,test_sip_fetch,test_replay_parity,test_fixture_shapes}.py, helper tests/regime_label_job/_sip.py.
RED at this SHA: 123 failed (all NotImplementedError), 15 passed (14 fixture-shape validators + 1 replay-only precondition).
Also keep green: tests/autotuner/test_ac4_regime_conditional_exit_ticks.py (S-2.9's existing leg).

## Fixtures
Captured: tests/fixtures/alpaca/stock_bars_captured/ (PM). Derived: tests/fixtures/alpaca/stock_bars_derived/ (PROVENANCE.md; validated by test_fixture_shapes.py). Golden series: tests/fixtures/math/regime_classifier/.

## Notes
- The tests were run against a throwaway full model (scratchpad, not committed): 137 pass. 13 of 15 planted faults are caught (zero-fill, percent, equal-weight, partial bar, oldest-20, calendar-day window, 5-minute lag, date-only end, feed=iex, no paging, partial batch kept, retry on 403, unclamped Retry-After). The 2 surviving mutants (UTC date instead of ET date in `target_session`; UTC date of a bar stamp) are equivalent for midnight-ET stamps and 04:30 UTC starts.
- S-5.11 and the job-level S-2.4/S-2.12/S-2.9 tests drive `run_regime_label_job`, so they also need batch B's orchestration.
- Do not edit tests; a test you believe wrong goes back to rg-c2a-red by message.
