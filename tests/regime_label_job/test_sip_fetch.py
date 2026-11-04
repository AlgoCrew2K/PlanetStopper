"""RED tests -- regime_label_job.fetch_sip_daily_closes and the SIP request's date bounds (AC-2, AC-5).

Contract (approach "SIP request"):
  fetch_sip_daily_closes(tickers, start: date, end: aware-UTC datetime) -> SipCloses
    closes  {ticker: {ET bar date: close}} for every bar the 200 pages returned
    failed  {ticker: "<HTTP status>" | "<exception class name>"} for every ticker of a failed batch
  GET {synthetic_history.ALPACA_BASE_URL}/stocks/bars, params symbols (<= SIP_BATCH_SIZE per request),
  timeframe=1Day, adjustment=split, feed=sip, limit=10000, start (midnight ET of `start` as RFC 3339 UTC),
  end (explicit UTC timestamp), page_token on later pages; headers synthetic_history.get_alpaca_headers();
  timeout=REQUEST_TIMEOUT_SECONDS; allow_redirects=False. MAX_ATTEMPTS on 429/500/502/503/504 and transport
  errors; any other non-200 status fails at once. A page failing after its attempts fails the WHOLE batch.
  A 200 that omits a ticker is NOT a failure: the ticker simply has no bars.

All HTTP is a scripted stand-in for requests.get; time.sleep is recorded, never slept.
"""

from __future__ import annotations

import copy
import logging
import os
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest
import requests

import synthetic_history
from tests.regime_label_job import _sip as S

D = S.TARGET_D
NEWEST = S.NEWEST_ELIGIBLE
HEADERS = {
    "APCA-API-KEY-ID": "PKSENTINELFETCHKEY81",
    "APCA-API-SECRET-KEY": "SENTINELFETCHSECRET99",
}
BASE_URL = "https://data.example.test/v2"
START = date(2026, 9, 22)
END = datetime(2026, 11, 4, 4, 10, tzinfo=UTC)


class Scripted:
    """requests.get stand-in replaying `script` in order (Resp or exception), repeating the last."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, url=None, **kwargs):
        self.calls.append({"url": url, **kwargs})
        item = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return item

    @property
    def params(self) -> list[dict]:
        return [c["params"] for c in self.calls]


@pytest.fixture()
def job():
    import regime_label_job as mod

    return mod


@pytest.fixture()
def sleeps(job, monkeypatch):
    recorded: list[float] = []
    monkeypatch.setattr(job.time, "sleep", lambda s: recorded.append(float(s)))
    return recorded


@pytest.fixture(autouse=True)
def _wire(monkeypatch):
    monkeypatch.setattr(synthetic_history, "get_alpaca_headers", lambda: dict(HEADERS))
    monkeypatch.setattr(synthetic_history, "ALPACA_BASE_URL", BASE_URL)

    def forbidden(*a, **k):
        raise AssertionError("the job may only GET")

    for verb in ("post", "put", "patch", "delete", "head", "request"):
        monkeypatch.setattr(requests, verb, forbidden)


def _ok(closes: dict, token: str | None = None) -> S.Resp:
    return S.Resp(200, S.page_body(closes, token))


def _one(closes: dict | None = None) -> dict:
    return closes if closes is not None else {"AAA": {NEWEST: 10.0}}


def _fetch(job, monkeypatch, http, tickers=("AAA",), start=START, end=END):
    monkeypatch.setattr(requests, "get", http)
    return job.fetch_sip_daily_closes(list(tickers), start, end)


# ---------------------------------------------------------------------------
# Request shape (feed=sip, 1Day, split-adjusted, explicit bounds)
# ---------------------------------------------------------------------------


def test_request_is_a_get_of_sip_daily_split_adjusted_bars(job, monkeypatch, sleeps):
    http = Scripted([_ok(_one())])

    _fetch(job, monkeypatch, http, tickers=("AAA", "BBB"))

    assert len(http.calls) == 1
    call = http.calls[0]
    assert call["url"] == f"{BASE_URL}/stocks/bars"
    params = call["params"]
    assert params["feed"] == "sip"
    assert params["timeframe"] == "1Day"
    assert params["adjustment"] == "split"
    assert params["limit"] == 10000
    assert set(params["symbols"].split(",")) == {"AAA", "BBB"}
    assert "page_token" not in params
    assert call["headers"] == HEADERS
    assert call["timeout"] == job.REQUEST_TIMEOUT_SECONDS
    assert call["allow_redirects"] is False


@pytest.mark.parametrize(
    "start",
    [date(2026, 9, 22), date(2026, 11, 2)],
    ids=["edt-start", "est-start"],
)
def test_start_is_midnight_et_of_the_first_session_as_rfc3339_utc(job, monkeypatch, sleeps, start):
    http = Scripted([_ok(_one())])

    _fetch(job, monkeypatch, http, start=start)

    assert http.params[0]["start"] == S.bar_stamp(start)


def test_end_is_the_explicit_utc_timestamp_it_was_given_never_a_date_only_value(
    job, monkeypatch, sleeps
):
    http = Scripted([_ok(_one())])

    _fetch(job, monkeypatch, http, end=END)

    end = http.params[0]["end"]
    assert end == END.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert len(end) > len("2026-11-04"), "a date-only end is refused by the SIP feed (D-12)"
    assert end.endswith("Z")


def test_every_page_of_a_batch_carries_the_same_bounds(job, monkeypatch, sleeps):
    http = Scripted([_ok(_one(), "tok1"), _ok({"AAA": {NEWEST - timedelta(days=1): 9.0}})])

    _fetch(job, monkeypatch, http)

    assert len(http.calls) == 2
    for key in ("start", "end", "feed", "timeframe", "adjustment", "symbols", "limit"):
        assert http.params[0][key] == http.params[1][key]


# ---------------------------------------------------------------------------
# Bar dates and closes
# ---------------------------------------------------------------------------


def test_captured_sip_page_maps_each_bar_to_its_et_session_date(job, monkeypatch, sleeps):
    captured = S.load_captured("sip_1day")
    http = Scripted([S.Resp(captured["status"], captured["body"])])

    sip = _fetch(job, monkeypatch, http, tickers=("QQQ", "DULL", "STPZ"))

    assert sip.failed == {}
    for sym, bars in captured["body"]["bars"].items():
        # dates parsed independently: UTC stamp -> ET date; EDT stamps are 04:00Z and EST ones 05:00Z
        assert sip.closes[sym] == {S.bar_date(b["t"]): b["c"] for b in bars}
    assert date(2026, 11, 3) in sip.closes["QQQ"], "a 05:00Z (EST) stamp is the 2026-11-03 session"
    assert date(2026, 10, 1) in sip.closes["QQQ"], "a 04:00Z (EDT) stamp is the 2026-10-01 session"


# ---------------------------------------------------------------------------
# Paging
# ---------------------------------------------------------------------------


def test_paged_response_is_followed_with_next_page_token_and_pages_are_merged(
    job, monkeypatch, sleeps
):
    captured = S.load_captured("sip_1day")
    paged = S.load_captured("sip_1day_paged")
    token = paged["body"]["next_page_token"]
    first_page = S.closes_from_body(paged["body"])
    full = S.closes_from_body(captured["body"])
    rest = {
        sym: {d: c for d, c in table.items() if d not in first_page.get(sym, {})}
        for sym, table in full.items()
    }
    http = Scripted([S.Resp(200, paged["body"]), _ok(rest)])

    sip = _fetch(job, monkeypatch, http, tickers=("QQQ", "DULL", "STPZ"))

    assert len(http.calls) == 2
    assert "page_token" not in http.params[0]
    assert http.params[1]["page_token"] == token
    assert sip.failed == {}
    assert sip.closes == full
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.0, "one short pause between the two pages"


def test_a_later_page_failing_fails_the_whole_batch_whatever_page_one_returned(
    job, monkeypatch, sleeps
):
    first = _ok({"AAA": {NEWEST: 10.0}, "BBB": {NEWEST: 20.0}}, "tok1")
    http = Scripted([first, S.Resp(500, {"message": "x"})])

    sip = _fetch(job, monkeypatch, http, tickers=("AAA", "BBB", "CCC"))

    assert set(sip.failed) == {"AAA", "BBB", "CCC"}, (
        "every ticker of the batch, those with data included"
    )
    assert set(sip.failed.values()) == {"500"}
    for t in sip.failed:
        assert not sip.closes.get(t)


# ---------------------------------------------------------------------------
# Batches (S-5.5)
# ---------------------------------------------------------------------------


def _tickers(n: int) -> list[str]:
    return [f"T{i:02d}" for i in range(n)]


def _table(tickers) -> dict:
    return {t: {NEWEST: 10.0 + i} for i, t in enumerate(tickers)}


def test_thirty_one_tickers_are_requested_in_two_batches_of_at_most_the_batch_size(
    job, monkeypatch, sleeps
):
    tickers = _tickers(31)
    http = Scripted([_ok(_table(tickers[:30])), _ok(_table(tickers[30:]))])

    sip = _fetch(job, monkeypatch, http, tickers=tickers)

    batches = [p["symbols"].split(",") for p in http.params]
    assert [len(b) for b in batches] == [job.SIP_BATCH_SIZE, 1]
    assert sorted(t for b in batches for t in b) == sorted(tickers), (
        "each ticker requested exactly once"
    )
    assert sip.failed == {}
    assert set(sip.closes) == set(tickers)


def test_duplicate_tickers_are_requested_once(job, monkeypatch, sleeps):
    http = Scripted([_ok(_one())])
    _fetch(job, monkeypatch, http, tickers=("AAA", "AAA", "AAA"))
    assert http.params[0]["symbols"] == "AAA"


@pytest.mark.parametrize(
    ("failure", "text"),
    [
        (requests.exceptions.ConnectionError("boom"), "ConnectionError"),
        (requests.exceptions.ReadTimeout("slow"), "ReadTimeout"),
        (S.Resp(500, {"message": "x"}), "500"),
        (S.Resp(503, {"message": "x"}), "503"),
        (S.Resp(429, {"message": "x"}), "429"),
    ],
    ids=["connection-error", "timeout", "http-500", "http-503", "http-429"],
)
def test_second_batch_failing_after_its_attempts_marks_only_its_tickers(
    job, monkeypatch, sleeps, failure, text
):
    tickers = _tickers(31)
    first = _ok(_table(tickers[:30]))
    http = Scripted([first, failure])

    sip = _fetch(job, monkeypatch, http, tickers=tickers)

    second_batch = [t for t in tickers if t not in sip.closes or t in sip.failed]
    assert sip.failed == {tickers[30]: text}
    assert set(sip.closes) >= set(tickers[:30])
    assert not sip.closes.get(tickers[30])
    assert len(http.calls) == 1 + job.MAX_ATTEMPTS, (
        "the first batch once, the failing batch MAX_ATTEMPTS times"
    )
    assert second_batch == [tickers[30]]
    assert len(sleeps) >= job.MAX_ATTEMPTS - 1, "a pause between the failing batch's attempts"


def test_failure_text_is_the_status_or_the_exception_class_only_never_a_message(
    job, monkeypatch, sleeps
):
    secret = "SECRET-MESSAGE-TEXT-ZZZ"
    sip = _fetch(job, monkeypatch, Scripted([requests.exceptions.ConnectionError(secret)]))
    assert sip.failed == {"AAA": "ConnectionError"}
    assert secret not in repr(sip)
    sip = _fetch(job, monkeypatch, Scripted([S.Resp(500, {"message": secret})]))
    assert secret not in repr(sip)


# ---------------------------------------------------------------------------
# Retry and refusal semantics (S-5.12)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_a_transient_status_is_retried_and_a_later_200_succeeds(job, monkeypatch, sleeps, status):
    http = Scripted([S.Resp(status, {}), _ok(_one())])

    sip = _fetch(job, monkeypatch, http)

    assert len(http.calls) == 2
    assert sip.failed == {}
    assert sip.closes["AAA"] == {NEWEST: 10.0}


def test_a_transport_error_is_retried_and_a_later_200_succeeds(job, monkeypatch, sleeps):
    http = Scripted([requests.exceptions.ConnectionError("x"), _ok(_one())])
    sip = _fetch(job, monkeypatch, http)
    assert len(http.calls) == 2
    assert sip.failed == {}


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_any_other_non_200_status_fails_at_once_without_a_retry(job, monkeypatch, sleeps, status):
    http = Scripted([S.Resp(status, {"message": "refused"}), _ok(_one())])

    sip = _fetch(job, monkeypatch, http, tickers=("AAA", "BBB"))

    assert len(http.calls) == 1
    assert sip.failed == {"AAA": str(status), "BBB": str(status)}
    assert sleeps == []


def test_the_captured_recent_sip_refusal_fails_the_batch_with_status_403_only(
    job, monkeypatch, sleeps
):
    captured = S.load_captured("sip_1day_recent_refused")
    assert captured["status"] == 403
    http = Scripted([S.Resp(captured["status"], captured["body"])])

    sip = _fetch(job, monkeypatch, http, tickers=("QQQ", "DULL"))

    assert sip.failed == {"QQQ": "403", "DULL": "403"}
    assert not any(sip.closes.get(t) for t in ("QQQ", "DULL"))
    assert captured["body"]["message"] not in repr(sip)


def test_backoff_without_retry_after_starts_at_one_second_and_doubles(job, monkeypatch, sleeps):
    http = Scripted([S.Resp(503, {}), S.Resp(503, {}), _ok(_one())])
    _fetch(job, monkeypatch, http)
    assert sleeps == [1.0, 2.0]


@pytest.mark.parametrize(
    ("header", "delay"),
    [("7", 7.0), ("0", 0.0), ("-5", 0.0)],
)
def test_retry_after_seconds_is_honoured_and_never_negative(
    job, monkeypatch, sleeps, header, delay
):
    http = Scripted([S.Resp(429, {}, {"Retry-After": header}), _ok(_one())])
    _fetch(job, monkeypatch, http)
    assert sleeps == [delay]


def test_retry_after_is_clamped_to_the_cap(job, monkeypatch, sleeps):
    http = Scripted([S.Resp(429, {}, {"Retry-After": "86400"}), _ok(_one())])
    _fetch(job, monkeypatch, http)
    assert sleeps == [job.RETRY_AFTER_CAP_SECONDS]


def test_total_sleep_is_bounded_by_the_cap_per_retry(job, monkeypatch, sleeps):
    http = Scripted([S.Resp(429, {}, {"Retry-After": "99999"})])
    _fetch(job, monkeypatch, http)
    assert len(http.calls) == job.MAX_ATTEMPTS
    assert sum(sleeps) <= (job.MAX_ATTEMPTS - 1) * job.RETRY_AFTER_CAP_SECONDS


# ---------------------------------------------------------------------------
# 200 omitting a ticker is a missing bar, not a failure (S-2.8, S-5.5 second case)
# ---------------------------------------------------------------------------


def test_a_200_that_omits_a_requested_ticker_gives_it_no_bars_and_no_failure(
    job, monkeypatch, sleeps
):
    captured = S.load_captured("sip_1day_missing_symbol")
    http = Scripted([S.Resp(captured["status"], captured["body"])])

    sip = _fetch(job, monkeypatch, http, tickers=("QQQ", "ZZZQX"))

    assert sip.failed == {}
    assert not sip.closes.get("ZZZQX"), "no zero or placeholder is invented for an unpriced ticker"
    assert sip.closes["QQQ"] == S.closes_from_body(captured["body"])["QQQ"]
    assert len(http.calls) == 1


# ---------------------------------------------------------------------------
# Timing lines (one per attempt, no ids or symbols)
# ---------------------------------------------------------------------------


def test_each_attempt_writes_one_alpaca_timing_line_without_symbols_or_keys(
    job, monkeypatch, sleeps, timing_records, timing_clock, caplog
):
    caplog.set_level(logging.DEBUG)
    http = Scripted(
        [S.Resp(503, {}), requests.exceptions.ConnectionError("T00 broke"), _ok(_table(["T00"]))]
    )

    _fetch(job, monkeypatch, http, tickers=("T00",))

    lines = timing_records.of("alpaca")
    assert len(lines) == 3
    for line in lines:
        assert line.words == ("GET", "/v2/stocks/bars")
        assert "ms" in line.fields
    assert [ln.fields.get("status") for ln in lines] == ["503", None, "200"]
    assert lines[1].fields.get("error") == "ConnectionError"
    for text in timing_records.lines:
        for secret in ("T00", *HEADERS.values()):
            assert secret not in text


# ---------------------------------------------------------------------------
# The job's request bounds (date math, D-12): driven through run_regime_label_job
# ---------------------------------------------------------------------------


def _golden(key="mean_reverting"):
    closes = S.closes_from_body(S.load_derived(f"sip_1day_golden_{key}.json")["body"])
    prefix = {"mean_reverting": "MRV", "trending": "TRN", "high_vol": "HVL"}[key]
    return closes, S.holdings_of((f"{prefix}A", 0.6), (f"{prefix}B", 0.4))


def _run(job, monkeypatch, closes, holdings, clock, *, serve_all=False):
    stub = S.AlpacaStub(closes, serve_all=serve_all)
    monkeypatch.setattr(requests, "get", stub)
    monkeypatch.setattr(job.time, "sleep", lambda s: None)
    entry = {"name": "Bounds", "current_holdings": holdings}
    return job.run_regime_label_job(lambda: {"xR4tQ9mZ2bL7vN1cK8pW": entry}, clock=clock), stub


def test_job_end_bound_is_twenty_minutes_before_job_start_as_explicit_utc(job, monkeypatch):
    closes, holdings = _golden()

    run, stub = _run(job, monkeypatch, closes, holdings, lambda: S.JOB_START)

    assert stub.calls, "the job must request prices"
    for params in (c["params"] for c in stub.calls):
        end = datetime.fromisoformat(params["end"].replace("Z", "+00:00"))
        assert end.tzinfo is not None
        assert S.JOB_START - end >= timedelta(minutes=15), (
            "the SIP feed refuses an end inside 15 minutes"
        )
        assert end == S.JOB_START - job.SIP_END_LAG
        assert len(params["end"]) > len("2026-11-04")
        assert params["feed"] == "sip"
        assert params["timeframe"] == "1Day"
    assert run.session == D


def test_job_start_bound_is_midnight_et_of_the_oldest_window_session(job, monkeypatch):
    closes, holdings = _golden()

    _, stub = _run(job, monkeypatch, closes, holdings, lambda: S.JOB_START)

    assert stub.calls[0]["params"]["start"] == S.bar_stamp(job.window_sessions(D)[0])


def test_the_end_bound_is_fixed_at_job_start_even_when_the_clock_moves(job, monkeypatch):
    closes, holdings = _golden()
    ticks = iter([S.JOB_START] + [S.JOB_START + timedelta(minutes=40)] * 50)

    run, stub = _run(job, monkeypatch, closes, holdings, lambda: next(ticks))

    assert run.session == D
    ends = {c["params"]["end"] for c in stub.calls}
    assert ends == {(S.JOB_START - job.SIP_END_LAG).strftime("%Y-%m-%dT%H:%M:%SZ")}


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 11, 7, 4, 30, tzinfo=UTC),  # Saturday, EST
        datetime(2026, 10, 27, 4, 30, tzinfo=UTC),  # Tuesday, EDT
    ],
    ids=["saturday-est", "tuesday-edt"],
)
def test_job_requests_start_from_the_window_of_the_session_derived_from_its_start_instant(
    job, monkeypatch, instant
):
    sessions_needed = S.window_candidates(job.target_session(instant))
    closes = {
        t: S.closes_from_returns(S.random_returns(len(sessions_needed) - 1, i), sessions_needed)
        for i, t in enumerate(("AAA", "BBB"))
    }
    holdings = S.holdings_of(("AAA", 0.6), ("BBB", 0.4))

    run, stub = _run(job, monkeypatch, closes, holdings, lambda: instant)

    target = run.session
    assert target == job.target_session(instant)
    assert stub.calls[0]["params"]["start"] == S.bar_stamp(sessions_needed[0])


# ---------------------------------------------------------------------------
# S-2.4: bars dated D or later never enter, even when the source returns them
# ---------------------------------------------------------------------------


def test_bars_dated_d_and_after_returned_by_the_source_never_enter_the_stored_label(
    job, monkeypatch
):
    closes, holdings = _golden()
    clean_run, _ = _run(job, monkeypatch, copy.deepcopy(closes), holdings, lambda: S.JOB_START)
    clean_label = clean_run.outcomes[0].label
    assert clean_label is not None, "precondition"

    polluted = copy.deepcopy(closes)
    for ticker in polluted:
        polluted[ticker][D] = polluted[ticker][NEWEST] * 40.0
        polluted[ticker][D + timedelta(days=1)] = polluted[ticker][NEWEST] * 0.01

    run, _ = _run(job, monkeypatch, polluted, holdings, lambda: S.JOB_START, serve_all=True)

    out = run.outcomes[0]
    assert out.label == clean_label
    assert out.window_last == NEWEST < D
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        rows = conn.execute("SELECT label, as_of_date FROM regime_label_cache").fetchall()
    finally:
        conn.close()
    assert rows == [(clean_label, D.isoformat())]


def test_a_session_whose_bar_was_incomplete_at_the_end_bound_is_never_used(job, monkeypatch):
    # The job starts at 16:10 ET on 2026-11-04, after the close (D is 2026-11-05). The request end is
    # 15:50 ET, before that session's close, so the newest COMPLETED session at the end bound is
    # 2026-11-03; a 2026-11-04 bar the source returns may be partial and is not a close.
    start = datetime(2026, 11, 4, 16, 10, tzinfo=S.ET).astimezone(UTC)
    closes, holdings = _golden()
    clean_run, _ = _run(job, monkeypatch, copy.deepcopy(closes), holdings, lambda: start)
    assert clean_run.session == date(2026, 11, 5)
    assert clean_run.outcomes[0].label is not None, (
        "precondition: 20 computable sessions through 2026-11-03"
    )

    partial = copy.deepcopy(closes)
    for ticker in partial:
        partial[ticker][date(2026, 11, 4)] = partial[ticker][NEWEST] * 25.0

    run, _ = _run(job, monkeypatch, partial, holdings, lambda: start, serve_all=True)

    assert run.outcomes[0].window_last == NEWEST
    assert run.outcomes[0].label == clean_run.outcomes[0].label


@pytest.mark.parametrize(
    "start_et",
    [datetime(2026, 11, 4, 9, 30), datetime(2026, 11, 4, 10, 0), datetime(2026, 11, 4, 15, 59, 59)],
    ids=["at-open", "mid-session", "last-second"],
)
def test_a_start_inside_a_trading_session_requests_nothing_and_writes_nothing(
    job, monkeypatch, start_et
):
    start = start_et.replace(tzinfo=S.ET).astimezone(UTC)
    closes, holdings = _golden()

    run, stub = _run(job, monkeypatch, closes, holdings, lambda: start)

    assert stub.calls == [], "no price request for a run that targets nothing"
    assert run.session is None
    assert run.outcomes == ()
    conn = sqlite3.connect(os.environ["DB_PATH"])
    try:
        assert conn.execute("SELECT COUNT(*) FROM regime_label_cache").fetchone() == (0,)
    finally:
        conn.close()


_SEEDED_ID = "xR4tQ9mZ2bL7vN1cK8pW"


def _row(conn_path):
    conn = sqlite3.connect(conn_path)
    try:
        return conn.execute(
            "SELECT symphony_id, label, as_of_date, updated_at FROM regime_label_cache"
        ).fetchall()
    finally:
        conn.close()


def test_a_mid_session_start_leaves_the_sessions_existing_rows_untouched(job, monkeypatch):
    import database

    database.save_regime_label(_SEEDED_ID, "trending", date(2026, 11, 4).isoformat())
    before = _row(os.environ["DB_PATH"])
    closes, holdings = _golden()
    start = datetime(2026, 11, 4, 10, 0, tzinfo=S.ET).astimezone(UTC)

    run, stub = _run(job, monkeypatch, closes, holdings, lambda: start)

    assert run.session is None
    assert stub.calls == []
    assert _row(os.environ["DB_PATH"]) == before, "label, date and updated_at all intact"


def test_a_start_after_the_close_writes_the_next_sessions_row_dated_that_session(job, monkeypatch):
    closes, holdings = _golden()
    start = datetime(2026, 11, 4, 16, 5, tzinfo=S.ET).astimezone(UTC)

    run, _ = _run(job, monkeypatch, closes, holdings, lambda: start)

    assert run.session == date(2026, 11, 5)
    rows = _row(os.environ["DB_PATH"])
    assert [(r[0], r[2]) for r in rows] == [(_SEEDED_ID, "2026-11-05")]
    assert rows[0][1] == run.outcomes[0].label is not None
