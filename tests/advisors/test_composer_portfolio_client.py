"""Read-only Composer portfolio client: statistics, value series, daily holdings.

Module under test: advisors.composer_portfolio_client.

Every request is served by the URL router in tests/advisors/_composer_http_router.py from the
captured records under tests/fixtures/composer/tenure-review/. No test makes a live call.

Contract pinned here (the implementer reads this file, not the plan):

- Four functions, each returning a frozen result and never raising:
    read_symphony_statistics(account_id) -> StatisticsRead(failure, symphonies)
    read_symphony_value_series(account_id, symphony_id) -> SeriesRead(failure, values)
    read_symphony_definition(symphony_id) -> DefinitionRead(failure, definition)
    read_daily_holdings(account_id, start: date, end: date)
        -> HoldingsRead(days, failed_spans[FailedSpan(start, end, failure)])
- ``failure`` is None on success, else one of refused (401, 403), not_found (404),
  rate_limited (429), unavailable (5xx, any requests exception), unreadable (a 200 whose body
  is not the expected JSON shape), empty (a series of three empty arrays) or rejected (any
  other status). A failed read carries an empty ``symphonies``/``values``/``days``.
- Reads are not retried, each carries the engine credential headers and a 15 second timeout,
  and each request is followed by one sleep of COMPOSER_READ_SPACING_SECONDS (via
  ``time.sleep``, called as ``time.sleep(...)`` so the tests' patch of the ``time`` module
  attribute holds), whether the request was answered or raised: the sleep runs in a
  ``finally``, so a failed read never lets the next request follow at once.
  COMPOSER_READ_SPACING_SECONDS is 3.0: 20 requests a minute, under the 25 a minute Composer
  documents, which leaves room for the engine's own per-minute read.
- Reads never follow a redirect (``allow_redirects=False``: a followed redirect would carry the
  key header to another host), so a 3xx reply is a rejected read, the definition read
  included. The account id is one percent-quoted path segment (no safe characters), so an id
  holding "/", "?" or "#" stays one segment. A symphony id is refused before any URL is built
  unless it is a plain id segment: "." and ".." (which a URL normaliser turns into another
  route), the empty id and any id holding a path, query, fragment, percent or space
  character read unreadable and cost no request.
- After a holdings span answered 429 no further span is requested; that span and every later
  span of the range are listed as failed spans of class rate_limited, in order, without a gap.
- A holdings row whose ``symbol`` is not a string (a list, an object, a number or null) makes
  its span unreadable; nothing raises out of the client.
- Every reply, whatever its status, logs one INFO line on this module's logger carrying the
  endpoint kind (statistics, series or holdings) and the HTTP status and nothing else: no
  account or symphony id, no response body, no header. A request that raised logs a WARNING
  with the exception type name only. No log line at any level carries an exception message, a
  response body, a URL or a credential.
- Every public function has a docstring and fully annotated parameters and return.
"""

from __future__ import annotations

import ast
import inspect
import logging
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

import pytest
import requests

import alpha_bot_execution
from advisors import composer_portfolio_client as cpc
from tests.advisors import _composer_http_router as h
from tests.advisors import _tenure_review_builders as b

REPO = Path(__file__).resolve().parents[2]

STATUS_CLASSES = [
    (401, "refused"),
    (403, "refused"),
    (404, "not_found"),
    (429, "rate_limited"),
    (500, "unavailable"),
    (502, "unavailable"),
    (503, "unavailable"),
    (504, "unavailable"),
    (400, "rejected"),
    (409, "rejected"),
    (422, "rejected"),
    (301, "rejected"),
    (302, "rejected"),
    (307, "rejected"),
]
EXCEPTION_FACTORIES = [
    pytest.param(lambda: requests.Timeout("LEAKMARK-timeout"), id="Timeout"),
    pytest.param(lambda: requests.ConnectTimeout("LEAKMARK-connect"), id="ConnectTimeout"),
    pytest.param(lambda: requests.ReadTimeout("LEAKMARK-read"), id="ReadTimeout"),
    pytest.param(lambda: requests.ConnectionError("LEAKMARK-conn"), id="ConnectionError"),
    pytest.param(
        lambda: requests.exceptions.ChunkedEncodingError("LEAKMARK-chunk"),
        id="ChunkedEncodingError",
    ),
]


@pytest.fixture
def router(monkeypatch):
    return h.ComposerRouter().install(monkeypatch)


_MISSING = object()

RECENT_START, RECENT_END = date(2026, 9, 20), date(2026, 10, 19)  # exactly 30 days
THREE_SPAN_START, THREE_SPAN_END = date(2026, 4, 6), date(2026, 6, 19)  # 75 days: 3 spans

# One call per read kind, each against ids the other tests also use. The id of the
# symphony-level reads is the oldest captured one, so a leak of it into a log is detectable.
READ_CALLS = {
    "statistics": lambda: cpc.read_symphony_statistics(h.ACCOUNT_ID),
    "series": lambda: cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID),
    "definition": lambda: cpc.read_symphony_definition(h.OLDEST_ID),
    "holdings": lambda: cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END),
}


def _serve(router, kind, reply):
    """Answer every request of one read kind with ``reply``."""
    if kind == "statistics":
        router.statistics_default = reply
    elif kind == "series":
        router.set_series(h.OLDEST_ID, reply)
    elif kind == "definition":
        router.definition_by_symphony[h.OLDEST_ID] = reply
    else:
        router.set_all_holdings(reply)


def _serve_success(router, kind):
    """Make the kind's request succeed with its captured record (statistics, series and
    holdings succeed by default; the definition read needs the captured tree served)."""
    if kind == "definition":
        router.definition_by_symphony[h.OLDEST_ID] = h.reply_from_record(
            "case4_definition_nOyb55RMGVCKPiYXv7TI.json"
        )


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_read_constants_match_the_documented_limits():
    assert cpc.COMPOSER_READ_TIMEOUT_SECONDS == 15
    assert cpc.COMPOSER_READ_SPACING_SECONDS == 3.0
    assert cpc.HOLDINGS_REQUEST_SPAN_DAYS == 30


def test_the_spacing_constant_cites_the_per_minute_limit_and_not_a_per_second_one():
    lines = (REPO / "advisors" / "composer_portfolio_client.py").read_text("utf-8").splitlines()
    index = next(
        i for i, line in enumerate(lines) if line.startswith("COMPOSER_READ_SPACING_SECONDS")
    )
    comment: list[str] = []
    for line in reversed(lines[:index]):
        if not line.startswith("#"):
            break
        comment.append(line)
    text = " ".join(reversed(comment))

    assert comment, "the constant carries a comment"
    assert re.search(r"\b25\b.*\bminute\b", text), "the 25 a minute figure is cited"
    assert "per second" not in text, "Composer's stricter documented limit is per minute"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


def test_statistics_request_shape(router):
    cpc.read_symphony_statistics(h.ACCOUNT_ID)

    assert len(router.requests) == 1
    request = router.requests[0]
    assert request.method == "GET"
    assert request.kind == "statistics"
    assert request.match["account"] == h.ACCOUNT_ID
    assert request.headers == alpha_bot_execution.get_composer_headers()
    assert request.timeout == cpc.COMPOSER_READ_TIMEOUT_SECONDS
    assert request.kwargs.get("allow_redirects") is False


def test_statistics_success_keeps_every_captured_symphony_with_its_id_name_and_start(router):
    result = cpc.read_symphony_statistics(h.ACCOUNT_ID)

    assert result.failure is None
    expected = {e["id"]: e for e in h.statistics_body()["symphonies"]}
    got = {e["id"]: e for e in result.symphonies}
    assert len(result.symphonies) == len(expected)
    assert set(got) == set(expected)
    for sid, entry in expected.items():
        assert got[sid]["name"] == entry["name"]
        assert got[sid]["invested_since"] == entry["invested_since"]


def test_statistics_of_an_account_with_no_symphonies_is_a_success_with_an_empty_roster(router):
    emptied = h.statistics_body()
    emptied["symphonies"] = []  # the empty-result edge of the captured record
    router.statistics_default = h.Reply(body=emptied)

    result = cpc.read_symphony_statistics(h.ACCOUNT_ID)

    assert result.failure is None
    assert result.symphonies == []


@pytest.mark.parametrize(("status", "failure"), STATUS_CLASSES)
def test_statistics_status_maps_to_its_failure_class_with_one_request_and_no_retry(
    router, status, failure
):
    router.statistics_default = h.error_reply(status)

    result = cpc.read_symphony_statistics(h.ACCOUNT_ID)

    assert result.failure == failure
    assert not result.symphonies
    assert len(router.requests) == 1, "a failed read must not be retried"


@pytest.mark.parametrize("make_exc", EXCEPTION_FACTORIES)
def test_statistics_transport_failures_are_unavailable_and_never_raise(router, make_exc):
    router.statistics_default = h.raising(make_exc())

    result = cpc.read_symphony_statistics(h.ACCOUNT_ID)

    assert result.failure == "unavailable"
    assert not result.symphonies
    assert len(router.requests) == 1


def _statistics_unreadable_cases():
    def entry_with(**changes):
        body = h.statistics_body()
        entry = body["symphonies"][0]
        for key, value in changes.items():
            if value is _MISSING:
                del entry[key]
            else:
                entry[key] = value
        return h.Reply(body=body)

    return {
        "non_json_html": h.non_json_200(),
        "empty_body": h.Reply(status=200, body=""),
        "json_null": h.Reply(status=200, body="null", content_type="application/json"),
        "json_list": h.Reply(body=[{"id": "x"}]),
        "no_symphonies_key": h.Reply(body={"data": []}),
        "symphonies_is_an_object": h.Reply(body={"symphonies": {"a": 1}}),
        "symphonies_is_null": h.Reply(body={"symphonies": None}),
        "entry_is_not_an_object": h.Reply(body={"symphonies": ["x"]}),
        "entry_without_id": entry_with(id=_MISSING),
        "entry_with_blank_id": entry_with(id=""),
        "entry_with_numeric_id": entry_with(id=123),
    }


@pytest.mark.parametrize("case", sorted(_statistics_unreadable_cases()))
def test_statistics_200_that_is_not_the_expected_json_is_unreadable(router, case):
    router.statistics_default = _statistics_unreadable_cases()[case]

    result = cpc.read_symphony_statistics(h.ACCOUNT_ID)

    assert result.failure == "unreadable"
    assert not result.symphonies


def test_statistics_read_is_followed_by_the_spacing_sleep_after_success_and_after_failure(
    router,
):
    cpc.read_symphony_statistics(h.ACCOUNT_ID)
    router.statistics_default = h.error_reply(429)
    cpc.read_symphony_statistics(h.ACCOUNT_ID)
    router.statistics_default = h.non_json_200()
    cpc.read_symphony_statistics(h.ACCOUNT_ID)

    assert len(router.requests) == 3
    h.assert_reads_paced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    assert router.sleeps == [cpc.COMPOSER_READ_SPACING_SECONDS] * 3


# ---------------------------------------------------------------------------
# Value series
# ---------------------------------------------------------------------------


def test_series_request_shape(router):
    cpc.read_symphony_value_series(h.ACCOUNT_ID, h.MID_ID)

    assert len(router.requests) == 1
    request = router.requests[0]
    assert request.method == "GET"
    assert request.kind == "series"
    assert request.match == {"account": h.ACCOUNT_ID, "sid": h.MID_ID}
    assert request.headers == alpha_bot_execution.get_composer_headers()
    assert request.timeout == cpc.COMPOSER_READ_TIMEOUT_SECONDS
    assert request.kwargs.get("allow_redirects") is False


@pytest.mark.parametrize("symphony_id", h.CAPTURED_SERIES_IDS)
def test_series_values_are_the_deposit_adjusted_levels_keyed_by_the_utc_date(router, symphony_id):
    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, symphony_id)

    assert result.failure is None
    assert result.values == h.series_points(symphony_id)


def test_series_uses_deposit_adjusted_levels_not_the_raw_series_where_they_differ(router):
    raw = h.record_body(h.series_record_name(h.MID_ID))["series"]
    dates = h.series_dates(h.MID_ID)

    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.MID_ID)

    assert all(result.values[d] != level for d, level in zip(dates, raw, strict=True)), (
        "the capture differs on every point, so any raw-series level is a defect"
    )


@pytest.mark.parametrize("symphony_id", h.CAPTURED_SERIES_IDS)
def test_series_dates_are_all_nyse_trading_days_and_start_on_the_invested_since_date(
    router, symphony_id
):
    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, symphony_id)

    days = sorted(result.values)
    first, last = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    sessions = {d.isoformat() for d in b.nyse_days(first, last)}
    assert set(days) <= sessions
    invested = {e["id"]: e["invested_since"] for e in h.statistics_body()["symphonies"]}
    assert days[0] == invested[symphony_id]


@pytest.mark.parametrize("offset_ms", [0, 86_399_000], ids=["midnight_utc", "last_second_of_day"])
def test_series_date_is_the_utc_calendar_date_of_the_epoch_whatever_the_host_zone(
    router, offset_ms
):
    body = h.record_body(h.series_record_name(h.MID_ID))
    body["epoch_ms"] = [ms + offset_ms for ms in body["epoch_ms"]]
    router.set_series(h.MID_ID, h.Reply(body=body))

    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.MID_ID)

    assert sorted(result.values) == h.series_dates(h.MID_ID)


def test_series_null_level_is_never_turned_into_zero(router):
    body = h.record_body(h.series_record_name(h.MID_ID))
    body["deposit_adjusted_series"][5] = None  # one absent level inside a captured series
    router.set_series(h.MID_ID, h.Reply(body=body))
    dates = h.series_dates(h.MID_ID)

    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.MID_ID)

    assert result.failure is None
    assert result.values.get(dates[5]) is None
    others = {d: v for d, v in result.values.items() if d != dates[5]}
    expected = {d: v for d, v in h.series_points(h.MID_ID).items() if d != dates[5]}
    assert others == expected


def test_series_of_three_empty_arrays_is_the_empty_failure(router):
    router.set_series(
        h.OLDEST_ID, h.reply_from_record("case7a_series_nonexistent_symphony_200_empty.json")
    )

    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID)

    assert result.failure == "empty"
    assert not result.values


def _series_unreadable_cases():
    def mutated(fn):
        body = h.record_body(h.series_record_name(h.MID_ID))
        fn(body)
        return h.Reply(body=body)

    return {
        "non_json_html": h.non_json_200(),
        "empty_body": h.Reply(status=200, body=""),
        "json_null": h.Reply(status=200, body="null", content_type="application/json"),
        "json_list": h.Reply(body=[1, 2, 3]),
        "deposit_adjusted_series_missing": mutated(lambda b_: b_.pop("deposit_adjusted_series")),
        "epoch_ms_missing": mutated(lambda b_: b_.pop("epoch_ms")),
        "arrays_of_different_length": mutated(lambda b_: b_["epoch_ms"].pop()),
        "epoch_ms_not_an_array": mutated(lambda b_: b_.update(epoch_ms="x")),
        "levels_not_an_array": mutated(lambda b_: b_.update(deposit_adjusted_series={"a": 1})),
    }


@pytest.mark.parametrize("case", sorted(_series_unreadable_cases()))
def test_series_200_that_is_not_the_expected_json_is_unreadable(router, case):
    router.set_series(h.MID_ID, _series_unreadable_cases()[case])

    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.MID_ID)

    assert result.failure == "unreadable"
    assert not result.values


@pytest.mark.parametrize(("status", "failure"), STATUS_CLASSES)
def test_series_status_maps_to_its_failure_class_with_one_request_and_no_retry(
    router, status, failure
):
    router.set_series(h.OLDEST_ID, h.error_reply(status))

    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID)

    assert result.failure == failure
    assert not result.values
    assert len(router.requests) == 1


def test_series_captured_unknown_account_404_is_not_found(router):
    router.set_series(
        h.OLDEST_ID, h.reply_from_record("case7a_series_nonexistent_account_404.json")
    )

    assert cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID).failure == "not_found"


def test_series_captured_invalid_credentials_401_is_refused(router):
    router.set_series(
        h.OLDEST_ID, h.reply_from_record("case7b_series_invalid_credentials_401.json")
    )

    assert cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID).failure == "refused"


@pytest.mark.parametrize("make_exc", EXCEPTION_FACTORIES)
def test_series_transport_failures_are_unavailable_and_never_raise(router, make_exc):
    router.set_series(h.OLDEST_ID, h.raising(make_exc()))

    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID)

    assert result.failure == "unavailable"
    assert not result.values
    assert len(router.requests) == 1


def test_series_read_is_followed_by_the_spacing_sleep_after_success_and_after_429(router):
    cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID)
    router.set_series(h.MID_ID, h.error_reply(429))
    cpc.read_symphony_value_series(h.ACCOUNT_ID, h.MID_ID)

    h.assert_reads_paced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    assert router.sleeps == [cpc.COMPOSER_READ_SPACING_SECONDS] * 2


# ---------------------------------------------------------------------------
# Daily holdings
# ---------------------------------------------------------------------------


def _span_params(request):
    return (
        date.fromisoformat(request.query["start_date"]),
        date.fromisoformat(request.query["end_date"]),
    )


def test_holdings_one_span_request_shape_matches_the_captured_request(router):
    captured = h.load_record("case3_holdings_recent_2026-09-20_2026-10-19.json")["_request"]

    cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert len(router.requests) == 1
    request = router.requests[0]
    assert request.method == "GET"
    assert request.kind == "holdings"
    assert request.match["account"] == h.ACCOUNT_ID
    assert request.query == captured["query"]
    assert request.query["include_aggregate_account_holdings"] == "false"
    assert request.headers == alpha_bot_execution.get_composer_headers()
    assert request.timeout == cpc.COMPOSER_READ_TIMEOUT_SECONDS
    assert request.kwargs.get("allow_redirects") is False


def test_holdings_result_is_date_then_symphony_then_ticker_to_value(router):
    router.set_all_holdings(h.Reply(body=h.holdings_recent_body()))

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert result.failed_spans == []
    assert result.days == h.holdings_days(h.holdings_recent_body())
    assert any(
        "USD" in tickers for by_symphony in result.days.values() for tickers in by_symphony.values()
    ), "cash rows are kept"


@pytest.mark.parametrize("total_days", [1, 29, 30, 31, 59, 60, 61, 90, 830])
def test_holdings_range_is_split_into_contiguous_spans_of_at_most_the_span_length(
    router, total_days
):
    start = date(2026, 1, 5)
    end = start + timedelta(days=total_days - 1)
    router.set_all_holdings(h.Reply(body=h.holdings_emptied_body()))

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, start, end)

    spans = [_span_params(r) for r in router.requests]
    assert len(spans) == -(-total_days // cpc.HOLDINGS_REQUEST_SPAN_DAYS)
    assert spans[0][0] == start
    assert spans[-1][1] == end
    for first, last in spans:
        assert first <= last
        assert (last - first).days + 1 <= cpc.HOLDINGS_REQUEST_SPAN_DAYS
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:], strict=False):
        assert next_start == prev_end + timedelta(days=1)
    assert result.days == {}
    assert result.failed_spans == []


def test_holdings_a_range_of_31_days_is_a_full_span_and_a_one_day_span(router):
    router.set_all_holdings(h.Reply(body=h.holdings_emptied_body()))

    cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END + timedelta(days=1))

    assert [_span_params(r) for r in router.requests] == [
        (RECENT_START, RECENT_END),
        (RECENT_END + timedelta(days=1), RECENT_END + timedelta(days=1)),
    ]


def test_holdings_spans_are_merged_without_filtering_the_days_a_response_returns(router):
    older, recent = h.holdings_older_body(), h.holdings_recent_body()
    replies = [h.Reply(body=older), h.Reply(body=recent)]
    router.holdings_handler = lambda req, i: replies[i]

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, date(2026, 4, 6), date(2026, 6, 4))

    assert len(router.requests) == 2
    expected = h.holdings_days(older)
    for day, by_symphony in h.holdings_days(recent).items():
        assert day not in expected, "the two captures share no date"
        expected[day] = by_symphony
    assert result.days == expected
    assert result.failed_spans == []
    in_older = {sid for by_symphony in h.holdings_days(older).values() for sid in by_symphony}
    for day, by_symphony in h.holdings_days(recent).items():
        assert set(result.days[day]) == set(by_symphony), "each span keeps its own membership"
    assert set(h.holdings_days(recent)[sorted(h.holdings_days(recent))[0]]) - in_older, (
        "fixture sanity: the recent span lists a symphony the older span does not"
    )


def test_holdings_a_failed_middle_span_is_listed_and_the_other_spans_are_kept(router):
    older, recent = h.holdings_older_body(), h.holdings_recent_body()
    replies = {0: h.Reply(body=older), 1: h.error_reply(500), 2: h.Reply(body=recent)}
    router.holdings_handler = lambda req, i: replies[i]
    start = date(2026, 4, 6)

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, start, start + timedelta(days=89))

    assert len(router.requests) == 3, "a failed span must not stop the later spans"
    assert [(s.start, s.end, s.failure) for s in result.failed_spans] == [
        (start + timedelta(days=30), start + timedelta(days=59), "unavailable")
    ]
    kept = h.holdings_days(older)
    kept.update(h.holdings_days(recent))
    assert result.days == kept


@pytest.mark.parametrize(("status", "failure"), STATUS_CLASSES)
def test_holdings_every_status_failure_is_listed_with_its_class_and_no_retry(
    router, status, failure
):
    router.set_all_holdings(h.error_reply(status))

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert result.days == {}
    assert [(s.start, s.end, s.failure) for s in result.failed_spans] == [
        (RECENT_START, RECENT_END, failure)
    ]
    assert len(router.requests) == 1


@pytest.mark.parametrize("make_exc", EXCEPTION_FACTORIES)
def test_holdings_transport_failures_are_unavailable_spans_and_never_raise(router, make_exc):
    router.set_all_holdings(h.raising(make_exc()))

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert result.days == {}
    assert [s.failure for s in result.failed_spans] == ["unavailable"]


def _holdings_unreadable_cases():
    def mutated(fn):
        body = h.holdings_recent_body()
        fn(body)
        return h.Reply(body=body)

    def first_symphony(body):
        return next(iter(body["by_symphony"]))

    def first_day_rows(body):
        sid = first_symphony(body)
        return body["by_symphony"][sid][next(iter(body["by_symphony"][sid]))]

    return {
        "non_json_html": h.non_json_200(),
        "empty_body": h.Reply(status=200, body=""),
        "json_list": h.Reply(body=[1]),
        "by_symphony_missing": mutated(lambda b_: b_.pop("by_symphony")),
        "by_symphony_is_a_list": mutated(lambda b_: b_.update(by_symphony=[])),
        "symphony_days_not_an_object": mutated(
            lambda b_: b_["by_symphony"].update({first_symphony(b_): []})
        ),
        "day_rows_not_a_list": mutated(
            lambda b_: b_["by_symphony"][first_symphony(b_)].update(
                {"2026-09-20": {"symbol": "VIXY", "value": 1.0}}
            )
        ),
        "row_without_symbol": mutated(lambda b_: first_day_rows(b_)[0].pop("symbol")),
        "row_without_value": mutated(lambda b_: first_day_rows(b_)[0].pop("value")),
    }


@pytest.mark.parametrize("case", sorted(_holdings_unreadable_cases()))
def test_holdings_200_that_is_not_the_expected_json_is_an_unreadable_span(router, case):
    router.set_all_holdings(_holdings_unreadable_cases()[case])

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert result.days == {}
    assert [(s.start, s.end, s.failure) for s in result.failed_spans] == [
        (RECENT_START, RECENT_END, "unreadable")
    ]


def test_holdings_a_response_with_no_symphonies_is_a_success_with_no_days(router):
    router.set_all_holdings(h.Reply(body=h.holdings_emptied_body()))

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert result.days == {}
    assert result.failed_spans == []


def test_every_holdings_span_is_followed_by_the_spacing_sleep(router):
    router.set_all_holdings(h.Reply(body=h.holdings_emptied_body()))

    cpc.read_daily_holdings(h.ACCOUNT_ID, date(2026, 4, 6), date(2026, 6, 19))

    assert len(router.requests) == 3
    h.assert_reads_paced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    assert router.sleeps == [cpc.COMPOSER_READ_SPACING_SECONDS] * 3


def test_holdings_after_a_429_span_make_no_further_request_and_list_every_remaining_span(router):
    older = h.holdings_older_body()
    replies = {0: h.Reply(body=older), 1: h.error_reply(429)}
    router.holdings_handler = lambda req, i: replies[i]
    start = date(2026, 4, 6)
    span = cpc.HOLDINGS_REQUEST_SPAN_DAYS

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, start, start + timedelta(days=3 * span - 1))

    assert len(router.requests) == 2, "nothing is requested after a 429"
    assert result.days == h.holdings_days(older)
    assert [(s.start, s.end, s.failure) for s in result.failed_spans] == [
        (start + timedelta(days=span), start + timedelta(days=2 * span - 1), "rate_limited"),
        (start + timedelta(days=2 * span), start + timedelta(days=3 * span - 1), "rate_limited"),
    ]


def test_holdings_a_429_on_the_first_span_fails_the_whole_range_after_one_request(router):
    router.set_all_holdings(h.error_reply(429))
    start = date(2026, 4, 6)

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, start, start + timedelta(days=89))

    assert len(router.requests) == 1
    assert result.days == {}
    assert [s.failure for s in result.failed_spans] == ["rate_limited"] * 3
    assert result.failed_spans[0].start == start
    assert result.failed_spans[-1].end == start + timedelta(days=89)


NON_STRING_SYMBOLS = [
    pytest.param(["VIXY"], id="list"),
    pytest.param({"ticker": "VIXY"}, id="object"),
    pytest.param(5, id="integer"),
    pytest.param(1.5, id="float"),
    pytest.param(True, id="boolean"),
    pytest.param(None, id="null"),
]


@pytest.mark.parametrize("symbol", NON_STRING_SYMBOLS)
def test_holdings_a_symbol_that_is_not_a_string_makes_the_span_unreadable_and_never_raises(
    router, symbol
):
    body = h.holdings_recent_body()
    first = next(iter(body["by_symphony"].values()))
    first[next(iter(first))][0]["symbol"] = symbol  # a type edge of one captured holding row
    router.set_all_holdings(h.Reply(body=body))

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert result.days == {}
    assert [(s.start, s.end, s.failure) for s in result.failed_spans] == [
        (RECENT_START, RECENT_END, "unreadable")
    ]


def test_holdings_a_string_symbol_in_the_long_form_is_kept_as_it_is(router):
    body = h.holdings_recent_body()
    first = next(iter(body["by_symphony"].values()))
    first[next(iter(first))][0]["symbol"] = "EQUITIES::VIXY//USD"  # the long form statistics use
    router.set_all_holdings(h.Reply(body=body))

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert result.failed_spans == []
    assert result.days == h.holdings_days(body)


# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------


def test_definition_request_shape(router):
    cpc.read_symphony_definition(h.BACKTEST_ID)

    assert len(router.requests) == 1
    request = router.requests[0]
    assert request.method == "GET"
    assert request.kind == "score"
    assert request.match == {"sid": h.BACKTEST_ID}
    assert request.query == {}
    assert request.headers == alpha_bot_execution.get_composer_headers()
    assert request.timeout == cpc.COMPOSER_READ_TIMEOUT_SECONDS
    assert request.kwargs.get("allow_redirects") is False


def test_definition_success_keeps_the_captured_tree_whole(router):
    result = cpc.read_symphony_definition(h.BACKTEST_ID)

    assert result.failure is None
    assert result.definition == h.definition_body()


@pytest.mark.parametrize(("status", "failure"), STATUS_CLASSES)
def test_definition_status_maps_to_its_failure_class_with_one_request_and_no_retry(
    router, status, failure
):
    router.definition_by_symphony[h.BACKTEST_ID] = h.error_reply(status)

    result = cpc.read_symphony_definition(h.BACKTEST_ID)

    assert result.failure == failure, "the status is kept; a 429 is not an empty definition"
    assert not result.definition
    assert len(router.requests) == 1


@pytest.mark.parametrize("make_exc", EXCEPTION_FACTORIES)
def test_definition_transport_failures_are_unavailable_and_never_raise(router, make_exc):
    router.definition_by_symphony[h.BACKTEST_ID] = h.raising(make_exc())

    result = cpc.read_symphony_definition(h.BACKTEST_ID)

    assert result.failure == "unavailable"
    assert not result.definition
    assert len(router.requests) == 1


@pytest.mark.parametrize(
    "make_reply",
    [
        pytest.param(h.non_json_200, id="non_json_html"),
        pytest.param(lambda: h.Reply(status=200, body=""), id="empty_body"),
        pytest.param(
            lambda: h.Reply(status=200, body="null", content_type="application/json"),
            id="json_null",
        ),
        pytest.param(lambda: h.Reply(body=[{"step": "root"}]), id="json_list"),
    ],
)
def test_definition_200_that_is_not_a_json_object_is_unreadable(router, make_reply):
    router.definition_by_symphony[h.BACKTEST_ID] = make_reply()

    result = cpc.read_symphony_definition(h.BACKTEST_ID)

    assert result.failure == "unreadable"
    assert not result.definition


def test_definition_read_is_followed_by_the_spacing_sleep_after_success_and_after_429(router):
    cpc.read_symphony_definition(h.BACKTEST_ID)
    router.definition_by_symphony[h.BACKTEST_ID] = h.error_reply(429)
    cpc.read_symphony_definition(h.BACKTEST_ID)

    h.assert_reads_paced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    assert router.sleeps == [cpc.COMPOSER_READ_SPACING_SECONDS] * 2


# ---------------------------------------------------------------------------
# Spacing holds whether or not a request was answered
# ---------------------------------------------------------------------------

RAISED = [
    pytest.param(lambda: requests.ConnectionError("LEAKMARK-reset"), id="ConnectionError"),
    pytest.param(lambda: requests.Timeout("LEAKMARK-timeout"), id="Timeout"),
    pytest.param(
        lambda: requests.exceptions.ChunkedEncodingError("LEAKMARK-chunk"), id="ChunkedEncoding"
    ),
    pytest.param(lambda: RuntimeError("LEAKMARK-unexpected"), id="unexpected_error"),
]


@pytest.mark.parametrize("make_exc", RAISED)
@pytest.mark.parametrize("kind", sorted(READ_CALLS))
def test_a_read_that_raises_is_still_followed_by_the_spacing_sleep(router, kind, make_exc):
    _serve(router, kind, h.raising(make_exc()))

    result = READ_CALLS[kind]()

    assert len(router.requests) == 1
    assert (getattr(result, "failure", None) or result.failed_spans[0].failure) == "unavailable"
    h.assert_reads_paced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    assert router.sleeps == [cpc.COMPOSER_READ_SPACING_SECONDS]


@pytest.mark.parametrize("make_exc", RAISED)
def test_every_holdings_span_that_raises_is_followed_by_the_spacing_sleep(router, make_exc):
    router.set_all_holdings(h.raising(make_exc()))

    result = cpc.read_daily_holdings(h.ACCOUNT_ID, THREE_SPAN_START, THREE_SPAN_END)

    assert len(router.requests) == 3, "a failed span does not stop the later spans"
    assert [s.failure for s in result.failed_spans] == ["unavailable"] * 3
    h.assert_reads_paced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    h.assert_requests_spaced(router, cpc.COMPOSER_READ_SPACING_SECONDS)
    assert router.sleeps == [cpc.COMPOSER_READ_SPACING_SECONDS] * 3


# ---------------------------------------------------------------------------
# URL paths and redirects
# ---------------------------------------------------------------------------

AWKWARD_ID = "ab/../cd?x=1 #frag%2F"


def test_the_account_id_is_one_percent_quoted_path_segment_on_all_three_reads(router):
    router.set_all_holdings(h.Reply(body=h.holdings_emptied_body()))

    cpc.read_symphony_statistics(AWKWARD_ID)
    cpc.read_symphony_value_series(AWKWARD_ID, h.OLDEST_ID)
    cpc.read_daily_holdings(AWKWARD_ID, RECENT_START, RECENT_END)

    assert router.violations == [], "an unquoted id splits the path into another route"
    assert [r.kind for r in router.requests] == ["statistics", "series", "holdings"]
    for request in router.requests:
        assert request.match["account"] == quote(AWKWARD_ID, safe="")
        assert request.query.get("x") is None, "a raw ? would start a query string"


NOT_A_PLAIN_ID_SEGMENT = [
    pytest.param(".", id="single_dot"),
    pytest.param("..", id="double_dot"),
    pytest.param("", id="empty"),
    pytest.param("ab/cd", id="slash"),
    pytest.param("ab?x=1", id="query"),
    pytest.param("ab#frag", id="fragment"),
    pytest.param("ab%2Fcd", id="percent_escape"),
    pytest.param("ab cd", id="space"),
    pytest.param("ab\\cd", id="backslash"),
    pytest.param("ab\ncd", id="newline"),
    pytest.param(AWKWARD_ID, id="awkward_mix"),
]


@pytest.mark.parametrize("symphony_id", NOT_A_PLAIN_ID_SEGMENT)
def test_a_symphony_id_that_is_not_a_plain_id_segment_is_refused_before_any_request(
    router, symphony_id
):
    series = cpc.read_symphony_value_series(h.ACCOUNT_ID, symphony_id)
    definition = cpc.read_symphony_definition(symphony_id)

    assert series.failure == "unreadable"
    assert not series.values
    assert definition.failure == "unreadable"
    assert not definition.definition
    assert router.requests == [], "a refused id builds no URL, so nothing is sent"
    assert router.violations == []


def test_a_refused_symphony_id_does_not_stop_the_next_read(router):
    cpc.read_symphony_value_series(h.ACCOUNT_ID, "..")

    result = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.MID_ID)

    assert result.failure is None
    assert [r.match["sid"] for r in router.requests] == [h.MID_ID]


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_answer_is_a_rejected_read_that_is_neither_followed_nor_retried(router, status):
    router.statistics_default = h.redirect_reply(status)
    router.set_series(h.OLDEST_ID, h.redirect_reply(status))
    router.definition_by_symphony[h.OLDEST_ID] = h.redirect_reply(status)
    router.set_all_holdings(h.redirect_reply(status))

    stats = cpc.read_symphony_statistics(h.ACCOUNT_ID)
    series = cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID)
    definition = cpc.read_symphony_definition(h.OLDEST_ID)
    holdings = cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert stats.failure == series.failure == definition.failure == "rejected"
    assert [s.failure for s in holdings.failed_spans] == ["rejected"]
    assert len(router.requests) == 4
    assert all(r.kwargs.get("allow_redirects") is False for r in router.requests)


# ---------------------------------------------------------------------------
# Logging and import boundary
# ---------------------------------------------------------------------------


def test_logs_carry_the_exception_type_and_never_its_message_or_a_credential(router, caplog):
    caplog.set_level(logging.DEBUG)
    router.statistics_default = h.raising(requests.ConnectionError("LEAKMARK-stats"))
    router.set_series(h.OLDEST_ID, h.raising(requests.Timeout("LEAKMARK-series")))
    router.set_all_holdings(h.raising(requests.ReadTimeout("LEAKMARK-holdings")))

    cpc.read_symphony_statistics(h.ACCOUNT_ID)
    cpc.read_symphony_value_series(h.ACCOUNT_ID, h.OLDEST_ID)
    cpc.read_daily_holdings(h.ACCOUNT_ID, RECENT_START, RECENT_END)

    assert "LEAKMARK" not in caplog.text
    assert h.SERVICE_SECRET not in caplog.text
    assert h.SERVICE_KEY_ID not in caplog.text
    for name in ("ConnectionError", "Timeout", "ReadTimeout"):
        assert name in caplog.text


LEAK = "LEAKMARK-reply-body-5521"
CLIENT_LOGGER = "advisors.composer_portfolio_client"


@pytest.mark.parametrize("status", [200, 400, 401, 404, 429, 500])
@pytest.mark.parametrize("kind", sorted(READ_CALLS))
def test_every_reply_logs_one_info_line_with_the_endpoint_kind_and_the_status_only(
    router, caplog, kind, status
):
    caplog.set_level(logging.INFO)
    _serve_success(router, kind)
    if status != 200:
        _serve(router, kind, h.marked_error_reply(status, LEAK))

    READ_CALLS[kind]()

    records = [r for r in caplog.records if r.name == CLIENT_LOGGER and r.levelno == logging.INFO]
    assert len(router.requests) == 1
    assert len(records) == 1, f"one INFO line per reply, got {len(records)}"
    line = records[0].getMessage()
    assert kind in line.lower()
    assert re.search(rf"\b{status}\b", line), line
    for forbidden in (
        h.ACCOUNT_ID,
        h.OLDEST_ID,
        LEAK,
        h.SERVICE_KEY_ID,
        h.SERVICE_SECRET,
        "authorization",
    ):
        assert forbidden.lower() not in line.lower(), line


def test_each_holdings_span_reply_logs_its_own_info_line(router, caplog):
    caplog.set_level(logging.INFO)
    router.set_all_holdings(h.Reply(body=h.holdings_emptied_body()))

    cpc.read_daily_holdings(h.ACCOUNT_ID, date(2026, 4, 6), date(2026, 6, 19))

    info = [r for r in caplog.records if r.name == CLIENT_LOGGER and r.levelno == logging.INFO]
    assert len(router.requests) == 3
    assert len(info) == 3


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 502, 400, 422])
def test_no_log_line_of_an_error_reply_carries_its_body_a_credential_or_an_id(
    router, caplog, status
):
    caplog.set_level(logging.DEBUG)
    for kind in READ_CALLS:
        _serve(router, kind, h.marked_error_reply(status, LEAK))

    for call in READ_CALLS.values():
        call()

    assert len(router.requests) == len(READ_CALLS)
    assert any(r.name == CLIENT_LOGGER for r in caplog.records), "the capture saw the client"
    text = caplog.text
    assert "LEAKMARK" not in text
    for secret in (
        h.SERVICE_SECRET,
        h.SERVICE_KEY_ID,
        alpha_bot_execution.get_composer_headers()["authorization"],
        h.ACCOUNT_ID,
        h.OLDEST_ID,
    ):
        assert secret not in text


def test_a_200_reply_that_is_not_json_logs_neither_its_text_nor_a_credential(router, caplog):
    caplog.set_level(logging.DEBUG)
    for kind in READ_CALLS:
        _serve(router, kind, h.Reply(status=200, body=f"<html>{LEAK}</html>"))

    for call in READ_CALLS.values():
        call()

    assert len(router.requests) == len(READ_CALLS)
    assert "LEAKMARK" not in caplog.text
    assert h.SERVICE_SECRET not in caplog.text
    assert h.SERVICE_KEY_ID not in caplog.text


def test_every_public_function_has_a_docstring_and_annotated_parameters_and_return():
    public = [
        fn
        for name, fn in inspect.getmembers(cpc, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == cpc.__name__
    ]

    assert {fn.__name__ for fn in public} >= {
        "read_symphony_statistics",
        "read_symphony_value_series",
        "read_symphony_definition",
        "read_daily_holdings",
    }
    for fn in public:
        assert (fn.__doc__ or "").strip(), f"{fn.__name__} needs a docstring"
        signature = inspect.signature(fn)
        assert signature.return_annotation is not inspect.Signature.empty, fn.__name__
        for parameter in signature.parameters.values():
            assert parameter.annotation is not inspect.Parameter.empty, (
                f"{fn.__name__}({parameter.name}) needs an annotation"
            )


HOLDINGS_RECORDS = [
    "case3_holdings_recent_2026-09-20_2026-10-19.json",
    "case3_holdings_older_2026-04-06_2026-05-05.json",
]


def test_the_statistics_and_holdings_records_are_subsets_that_declare_what_they_keep():
    """The committed records are the symphonies and days the tests distinguish, not whole
    responses: each record's own trim note matches its data, and the data is smaller than the
    request it answers."""
    statistics = h.load_record("case1_statistics_roth.json")
    roster = [entry["id"] for entry in statistics["response_body"]["symphonies"]]
    trimmed = statistics["_trimmed"]
    assert trimmed["symphonies_kept"] == len(roster) < trimmed["symphonies_in_response"]
    assert set(trimmed["symphonies_dropped_ids"]).isdisjoint(roster)

    for name in HOLDINGS_RECORDS:
        record = h.load_record(name)
        by_symphony = record["response_body"]["by_symphony"]
        query = record["_request"]["query"]
        span_days = (
            date.fromisoformat(query["end_date"]) - date.fromisoformat(query["start_date"])
        ).days + 1
        kept_days = record["_trimmed"]["days_kept"]
        assert {day for days in by_symphony.values() for day in days} == set(kept_days), name
        assert {len(days) for days in by_symphony.values()} == {len(kept_days)}, name
        assert len(kept_days) < span_days, name
        assert (
            record["_trimmed"]["symphonies_kept"]
            == len(by_symphony)
            < (record["_trimmed"]["symphonies_in_response"])
        ), name
        assert set(by_symphony) <= set(roster), name


def test_the_client_imports_exactly_the_base_url_and_the_header_builder_from_the_engine():
    tree = ast.parse((REPO / "advisors" / "composer_portfolio_client.py").read_text("utf-8"))
    from_engine: set[str] = set()
    plain_engine_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "alpha_bot_execution":
            from_engine |= {alias.name for alias in node.names}
        if isinstance(node, ast.Import):
            plain_engine_import |= any(a.name == "alpha_bot_execution" for a in node.names)

    assert not plain_engine_import
    assert from_engine == {"COMPOSER_BASE_URL", "get_composer_headers"}
