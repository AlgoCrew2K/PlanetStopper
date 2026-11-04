"""Round 3 (CR-3 / PIPE-1): paging of one SIP batch is bounded.

A server that keeps returning a next_page_token must not keep the job requesting. The cap is the
module constant ``regime_label_job.MAX_PAGES_PER_BATCH``. A cut-off batch, or one whose token comes
back a second time, is a fetch failure for every ticker of that batch and yields no bars at all
(never a partial label). Tests derive the cap from the module; HTTP is a scripted requests.get.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
import requests

import synthetic_history
from tests.regime_label_job import _sip as S

NEWEST = S.NEWEST_ELIGIBLE
START = date(2026, 9, 22)
END = datetime(2026, 11, 4, 4, 10, tzinfo=UTC)
BASE_URL = "https://data.example.test/v2"


class RunawayRequests(BaseException):
    """Raised past a request ceiling so a missing cap fails the test instead of hanging it.

    A BaseException so no `except Exception` in the job can turn it into a handled failure."""


_REQUEST_CEILING = 500


class Scripted:
    """requests.get stand-in replaying `script` in order, repeating the last item."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, url=None, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if len(self.calls) > _REQUEST_CEILING:
            raise RunawayRequests(f"more than {_REQUEST_CEILING} requests for one batch")
        return self.script[min(len(self.calls) - 1, len(self.script) - 1)]


@pytest.fixture()
def job():
    import regime_label_job as mod

    return mod


@pytest.fixture(autouse=True)
def _wire(monkeypatch, job):
    monkeypatch.setattr(synthetic_history, "get_alpaca_headers", lambda: {"APCA-API-KEY-ID": "k"})
    monkeypatch.setattr(synthetic_history, "ALPACA_BASE_URL", BASE_URL)
    monkeypatch.setattr(job.time, "sleep", lambda s: None)


def _ok(token: str | None = None, ticker: str = "AAA") -> S.Resp:
    return S.Resp(200, S.page_body({ticker: {NEWEST: 10.0}}, token))


def _fetch(job, monkeypatch, http, tickers=("AAA",)):
    monkeypatch.setattr(requests, "get", http)
    return job.fetch_sip_daily_closes(list(tickers), START, END)


def _distinct_pages(count: int) -> list:
    """`count` pages with distinct tokens; the last one carries no token."""
    return [_ok(f"tok{i}") for i in range(count - 1)] + [_ok(None)]


def test_a_cap_exists_and_leaves_room_for_a_real_multi_page_response(job):
    assert isinstance(job.MAX_PAGES_PER_BATCH, int)
    assert job.MAX_PAGES_PER_BATCH >= 2


def test_a_server_returning_a_fresh_token_forever_is_cut_off_and_fails_the_batch(job, monkeypatch):
    cap = job.MAX_PAGES_PER_BATCH
    http = Scripted([_ok(f"tok{i}") for i in range(cap + 50)])

    sip = _fetch(job, monkeypatch, http, tickers=("AAA", "BBB"))

    assert len(http.calls) <= cap, "no request beyond the page cap"
    assert set(sip.failed) == {"AAA", "BBB"}
    assert all(isinstance(text, str) and text for text in sip.failed.values())
    assert sip.closes == {}, "a cut-off batch never yields a partial label"


def test_a_batch_ending_exactly_at_the_cap_still_succeeds(job, monkeypatch):
    cap = job.MAX_PAGES_PER_BATCH
    http = Scripted(_distinct_pages(cap))

    sip = _fetch(job, monkeypatch, http)

    assert len(http.calls) == cap
    assert sip.failed == {}
    assert sip.closes == {"AAA": {NEWEST: 10.0}}


def test_one_page_beyond_the_cap_fails_the_batch(job, monkeypatch):
    cap = job.MAX_PAGES_PER_BATCH
    http = Scripted(_distinct_pages(cap + 1))

    sip = _fetch(job, monkeypatch, http)

    assert len(http.calls) <= cap
    assert set(sip.failed) == {"AAA"}
    assert sip.closes == {}


def test_a_token_returned_a_second_time_fails_the_batch_at_once(job, monkeypatch):
    http = Scripted([_ok("same"), _ok("same")])

    sip = _fetch(job, monkeypatch, http)

    assert len(http.calls) <= 2, "the repeat is seen on page two; no third request"
    assert set(sip.failed) == {"AAA"}
    assert sip.closes == {}


def test_a_token_cycle_back_to_an_earlier_token_fails_the_batch(job, monkeypatch):
    http = Scripted([_ok(t) for t in ("a", "b", "a", "b", "a", "b")])

    sip = _fetch(job, monkeypatch, http)

    assert len(http.calls) <= 3, "token a coming back is caught on the third page"
    assert set(sip.failed) == {"AAA"}
    assert sip.closes == {}


def test_a_cut_off_batch_fails_only_its_own_tickers(job, monkeypatch):
    first_batch = [f"A{i:03d}" for i in range(job.SIP_BATCH_SIZE)]
    other = "Z999"

    def route(url=None, **kwargs):
        params = kwargs["params"]
        if len(params.get("page_token", "")) > _REQUEST_CEILING:
            raise RunawayRequests("page chain exceeded the ceiling")
        if other in params["symbols"].split(","):
            return _ok(None, ticker=other)
        return _ok(params.get("page_token", "") + "x", ticker="A000")

    monkeypatch.setattr(requests, "get", route)

    sip = job.fetch_sip_daily_closes([*first_batch, other], START, END)

    assert set(sip.failed) == set(first_batch)
    assert sip.closes == {other: {NEWEST: 10.0}}
