# regime_label_job

> Nightly job that computes one regime label per live symphony from SIP daily closes and writes it to `regime_label_cache`, so the live trailing-stop exit count varies by regime.

**Source:** `regime_label_job.py`
**Last updated:** 2026-11-03

## Overview

Before this module, `database.save_regime_label` had no production caller, so `regime_label_cache` stayed empty and the live engine always resolved no label (the base 3 confirm ticks) while the autotuner replay already used the regime-conditional counts 2/5/3. This job is now the sole production writer of the cache. It runs from the daily 04:30 slot of `app.run_scheduler` (see [app.md](app.md)), off the 1-minute engine path, and is lazily imported by the daemon worker. It calls only the Alpaca market-data host (`synthetic_history.ALPACA_BASE_URL`) and the local state DB; it has no order, Composer or `LIVE_EXECUTION` surface (pinned by `tests/security/test_regime_label_job_no_trade_boundary.py`).

Design basis: the label is classified from SIP consolidated daily closes (`feed=sip`, `adjustment=split`), close to close, allocation-weighted over a symphony's current holdings. This is not the same input the replay uses (see Known Gaps).

Status at this head: the job has not yet run in production. The first-night summary line is the evidence still owed.

## API Reference

### `run_regime_label_job(load_symphonies, *, clock=None) -> RegimeLabelRun`
Labels every stored symphony for the session whose open follows the run. `load_symphonies` returns `{symphony_id: bot_state entry}` (the daemon passes the symphony entries of the stored state, metadata keys excluded). `clock` is a test seam returning the current UTC datetime.

Behavior:
- Target session is `target_session(started)`. When the run starts inside a trading session it writes nothing and logs one line, `regime_label summary session=none seen=0 labelled=0 reason=session_in_progress`.
- Prices are fetched with `end = started - SIP_END_LAG` (20 minutes), because the SIP feed refuses a request ending inside its last 15 minutes.
- One roster-order pass decides, writes and logs each symphony before the next is written; the summary line comes last. One symphony's failure never stops the others.
- No write happens at or after the target session's open for a pre-open start (`after_open`).
- A load failure returns `RegimeLabelRun(session, (), <exception class name>)` and logs the summary.

### `target_session(start) -> date | None`
First NYSE trading day whose 09:30 ET open is strictly after `start`; `None` when `start` is inside a session.

### `window_sessions(session) -> list[date]`
The 31 NYSE sessions before `session` (30 plus the one before them), oldest first.

### `fetch_sip_daily_closes(tickers, start, end) -> SipCloses`
Batched (`SIP_BATCH_SIZE=30`) split-adjusted SIP daily closes, `{ticker: {ET bar date: close}}`. A failed batch marks every ticker in it in `SipCloses.failed` with the failure text. Each batch is bounded to `MAX_PAGES_PER_BATCH=5` requests; a repeated page token fails the batch (`RepeatedPageToken`), as does exceeding the cap (`PageLimitExceeded`). Retries use `MAX_ATTEMPTS=3`, `REQUEST_TIMEOUT_SECONDS=30.0` and `RETRY_AFTER_CAP_SECONDS=30.0`, copied from `advisors/composer_series_client`.

### `compute_label(holdings, closes, session, newest_eligible) -> LabelComputation`
Takes the 20 most recent computable sessions (`REGIME_WINDOW_SESSIONS`, from `regime_classifier.MIN_LABEL_SERIES_LENGTH`) among the 30 sessions strictly before `session`. A session is computable only when every holding has closes on that day and the previous session, and the day is not after `newest_eligible`. Fewer than 20 gives `insufficient_history`; the series goes to `regime_classifier.classify_regime`.

## Types

- `SymphonyOutcome(symphony_id, name, label, reason, window_first, window_last, window_count, detail)`: `label` is `None` when nothing was written; `reason` is one of the values below.
- `RegimeLabelRun(session, outcomes, failure)`.
- `SipCloses(closes, failed)`, `LabelComputation(label, reason, window_first, window_last, window_count, series)`.

Reasons: `insufficient_history`, `no_holdings`, `price_fetch_failed`, `no_classifier_label`, `write_failed`, `after_open`, `unexpected_error`, and `session_in_progress` (the in-session summary line only).

The write is `database.save_regime_label(symphony_id, label, session.isoformat())`, one row per symphony (upsert). The engine reads it with `database.get_cached_regime_label(symphony_id, session_date=<current ET date>)`, which returns the label only on an exact `as_of_date` match (see [database.md](database.md)).

## Known Gaps

- Live and replay do not match exactly. The replay's regime input is the first holding's last 1-minute IEX tick, positional matching across holdings, a window taken from the dates present in its history, a fold-head default, and a synthetic history built from `feed=iex`. Moving the replay onto this job's SIP basis is a follow-up math cycle.
- A ticker the SIP endpoint rejects with a 400 rather than omitting would fail its whole 30-ticker batch; unconfirmed for the stored holdings. The first run's summary line settles it.
- PIPE-3: the repeated-token check sits outside the parse `try`. CR-5: `_log_summary` annotates `Iterable` but needs `Sequence`.

## Internal Dependencies

- `database` (`save_regime_label`), `market_calendar` (sessions, `most_recent_completed_session`), `regime_classifier` (`classify_regime`, `MIN_LABEL_SERIES_LENGTH`), `synthetic_history` (`ALPACA_BASE_URL`), `timing_log`.
