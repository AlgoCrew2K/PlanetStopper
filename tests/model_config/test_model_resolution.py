"""Model resolution: each knob names the newest active model of its family.

The resolver lists the Models API through the caller's SDK client, so every
test drives a real ``anthropic.Anthropic`` over the in-process HTTP fake and
reads model IDs from the fixtures, never from literals. Clock-dependent tests
patch ``model_config.time`` (the module must ``import time`` and call
``time.monotonic()``).

Patch targets and forms the production module must keep:
- ``model_config.time.monotonic`` is the only clock the cache age reads.
- Environment overrides are read from ``os.environ`` on every call.
- ``resolve(knob, client)`` lists through ``client.models.list(...)`` only.
"""

from __future__ import annotations

import ast
import contextvars
import dataclasses
import logging
import pathlib
import threading
import time
import types
from unittest.mock import MagicMock

import anthropic
import httpx
import pytest
from anthropic import _base_client as sdk_base_client

import model_config
from model_config import Knob
from tests._anthropic_http_fake import FAKE_API_KEY, AnthropicHttpFake, load_fixture

pytestmark = pytest.mark.usefixtures("unresolved_models")

DAY = 24 * 60 * 60
ENV_VAR = {Knob.SUGGESTION: "ADVISOR_SUGGESTION_MODEL", Knob.SYNTHESIS: "ADVISOR_SYNTHESIS_MODEL"}
LINE = {Knob.SUGGESTION: "fable", Knob.SYNTHESIS: "opus"}
EXPECT_KEY = {Knob.SUGGESTION: "suggestion", Knob.SYNTHESIS: "synthesis"}
KNOBS = [
    pytest.param(Knob.SUGGESTION, id="suggestion"),
    pytest.param(Knob.SYNTHESIS, id="synthesis"),
]
CAPTURED = load_fixture("models/page_captured.json")
EDGE_CASES = load_fixture("models/edge_cases.json")["cases"]
THREAD_KINDS = ("request", "scheduler", "frontrunner")
SETTLE_SECONDS = 0.3
UNWAITED_SECONDS = 5  # a throttled listing returns well before the 30 s Retry-After used below


def newest_id(records, line):
    """Oracle: the newest active record of a line, by release date."""
    eligible = [
        r for r in records if r.get("line") == line and r.get("lifecycle", "active") == "active"
    ]
    return max(eligible, key=lambda r: r["created_at"])["id"]


def record_by_id(records, model_id):
    return next(r for r in records if r["id"] == model_id)


class FakeClock:
    def __init__(self, start=5000.0):
        self.now = start

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake_clock = FakeClock()
    monkeypatch.setattr(
        model_config,
        "time",
        types.SimpleNamespace(monotonic=fake_clock.monotonic, sleep=time.sleep),
    )
    return fake_clock


def captured_fake():
    return AnthropicHttpFake(pages=[CAPTURED])


def first_resolution(fake, knob=Knob.SUGGESTION):
    """Resolve once against the captured page and check the baseline later assertions compare to."""
    choice = model_config.resolve(knob, fake.client)
    assert choice.source == "resolved"
    assert choice.model_id == newest_id(CAPTURED["data"], LINE[knob])
    return choice


def failure_type_name(make_outcome):
    """The exception type the SDK raises for a scripted listing failure, probed on a separate fake."""
    probe = AnthropicHttpFake()
    probe.queue_listing_outcome(make_outcome())
    try:
        probe.client.models.list()
    except Exception as exc:
        return type(exc).__name__
    raise AssertionError("the probed outcome did not fail")


def error_outcome(name):
    return lambda: load_fixture(f"errors/{name}.json")


READ_TIMEOUT_DETAIL = "read-timeout-detail-4f2a"
CONNECT_ERROR_DETAIL = "connect-error-detail-91bc"
LISTING_FAILURES = [
    pytest.param(error_outcome("401_authentication"), id="401"),
    pytest.param(error_outcome("403_permission"), id="403"),
    pytest.param(error_outcome("429_rate_limit"), id="429"),
    pytest.param(error_outcome("500_api_error"), id="500"),
    pytest.param(error_outcome("503_unavailable"), id="503"),
    pytest.param(error_outcome("529_overloaded"), id="529"),
    pytest.param(lambda: httpx.ReadTimeout(READ_TIMEOUT_DETAIL), id="read-timeout"),
    pytest.param(lambda: httpx.ConnectError(CONNECT_ERROR_DETAIL), id="connection-error"),
]


def warnings_logged(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def assert_logged_type_only(caplog, expected_type_name, *forbidden_fragments):
    records = warnings_logged(caplog)
    assert len(records) == 1, f"one WARNING record per failed listing attempt, got {len(records)}"
    record = records[0]
    message = record.getMessage()
    assert expected_type_name in message
    assert record.exc_info is None and record.exc_text is None, (
        "a traceback would carry the exception text"
    )
    # A body fragment can legitimately be part of the type name (the 529 body says
    # "Overloaded", the SDK type is OverloadedError), so judge the text around the name.
    remainder = message.replace(expected_type_name, "")
    for fragment in (FAKE_API_KEY, *forbidden_fragments):
        assert fragment not in remainder


def call_concurrently(fake, call, count):
    """Run ``call(i)`` on ``count`` threads while one listing is held in flight.

    Returns ``(results, listing_sequences_while_held)``.
    """
    gate = fake.hold_listings()
    results, failures = [], []

    def runner(index):
        try:
            results.append(call(index))
        except BaseException as exc:  # surfaced below, never swallowed
            failures.append(exc)

    threads = [
        threading.Thread(target=runner, args=(i,), daemon=True, name=f"{THREAD_KINDS[i % 3]}-{i}")
        for i in range(count)
    ]
    try:
        for thread in threads:
            thread.start()
        assert fake.listing_started.wait(5), "no thread reached the Models API"
        time.sleep(
            SETTLE_SECONDS
        )  # let the other threads reach the cache check while the first listing is held
        sequences_while_held = fake.listing_sequences
    finally:
        gate.set()
    for thread in threads:
        thread.join(10)
    assert not any(t.is_alive() for t in threads), "a thread never returned"
    assert not failures, failures
    return results, sequences_while_held


def returns_within(seconds, fn, *args):
    """Call ``fn`` on a thread and return its result; fail when it blocks past the deadline."""
    box = []
    thread = threading.Thread(target=lambda: box.append(fn(*args)), daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), f"{getattr(fn, '__name__', fn)} blocked for more than {seconds}s"
    return box[0]


def page_listed(order):
    """The captured page with its records in the API's order or reversed (oldest listed first)."""
    data = list(CAPTURED["data"])
    if order == "reversed":
        data.reverse()
    return {**CAPTURED, "data": data, "first_id": data[0]["id"], "last_id": data[-1]["id"]}


LISTING_ORDERS = [
    pytest.param("as captured", id="as-captured"),
    pytest.param("reversed", id="oldest-listed-first"),
]


class TestFamilySelection:
    @pytest.mark.parametrize("order", LISTING_ORDERS)
    @pytest.mark.parametrize("knob", KNOBS)
    def test_each_knob_resolves_the_newest_active_model_of_its_own_line(self, knob, order):
        page = page_listed(order)
        records = page["data"]
        other = Knob.SYNTHESIS if knob is Knob.SUGGESTION else Knob.SUGGESTION
        expected = newest_id(records, LINE[knob])
        if order == "reversed":
            first_of_line = next(r["id"] for r in records if r.get("line") == LINE[knob])
            assert first_of_line != expected, (
                "fixture precondition: the first listed record of the line is not its newest"
            )
        assert expected != records[0]["id"], (
            "fixture precondition: the newest record overall belongs to a third line"
        )
        assert expected != newest_id(records, LINE[other]), (
            "fixture precondition: the two lines differ"
        )
        fake = AnthropicHttpFake(pages=[page])

        choice = model_config.resolve(knob, fake.client)

        assert choice.model_id == expected
        assert record_by_id(records, choice.model_id)["line"] == LINE[knob]
        assert choice.source == "resolved"
        assert choice.display_name == record_by_id(records, expected)["display_name"]

    def test_a_fable_site_never_names_the_newer_opus_line_model(self):
        records = CAPTURED["data"]
        newest_opus = record_by_id(records, newest_id(records, "opus"))
        newest_fable = record_by_id(records, newest_id(records, "fable"))
        assert newest_opus["created_at"] > newest_fable["created_at"], "fixture precondition"

        choice = model_config.resolve(Knob.SUGGESTION, captured_fake().client)

        assert choice.model_id == newest_fable["id"]

    @pytest.mark.parametrize("knob", KNOBS)
    @pytest.mark.parametrize("case", ["null_line_fable_id", "other_line_fable_id"])
    def test_family_comes_from_the_line_field_never_from_the_id_text(self, case, knob):
        fake = AnthropicHttpFake(pages=[EDGE_CASES[case]["page"]])

        choice = model_config.resolve(knob, fake.client)

        assert choice.model_id == EDGE_CASES[case]["expect"][EXPECT_KEY[knob]]

    @pytest.mark.parametrize("knob", KNOBS)
    @pytest.mark.parametrize("case", ["deprecated_newest", "retired_newest"])
    def test_a_model_that_is_not_active_is_never_chosen(self, case, knob):
        page = EDGE_CASES[case]["page"]
        newest_by_date = max(
            (r for r in page["data"] if r["line"] == LINE[knob]), key=lambda r: r["created_at"]
        )
        fake = AnthropicHttpFake(pages=[page])

        choice = model_config.resolve(knob, fake.client)

        assert choice.model_id == EDGE_CASES[case]["expect"][EXPECT_KEY[knob]]
        if knob is Knob.SUGGESTION:
            assert (
                newest_by_date["lifecycle"] != "active" and choice.model_id != newest_by_date["id"]
            )

    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_record_with_the_epoch_release_date_ranks_below_every_dated_record(self, knob):
        page = EDGE_CASES["epoch_created_at"]["page"]
        assert page["data"][0]["created_at"].startswith("1970-01-01"), (
            "fixture precondition: epoch record listed first"
        )
        fake = AnthropicHttpFake(pages=[page])

        choice = model_config.resolve(knob, fake.client)

        assert choice.model_id == EDGE_CASES["epoch_created_at"]["expect"][EXPECT_KEY[knob]]

    def test_equal_release_dates_keep_the_model_the_api_lists_first(self):
        page = EDGE_CASES["equal_created_at"]["page"]
        tied = [r for r in page["data"] if r["line"] == "fable"]
        assert len(tied) == 2 and tied[0]["created_at"] == tied[1]["created_at"], (
            "fixture precondition"
        )
        reversed_page = {**page, "data": list(reversed(page["data"]))}

        for expected_first, listing in ((tied[0]["id"], page), (tied[1]["id"], reversed_page)):
            for _ in range(3):  # the same winner on every resolution of the same list
                model_config._reset_registry()
                chosen = model_config.resolve(
                    Knob.SUGGESTION, AnthropicHttpFake(pages=[listing]).client
                )
                assert chosen.model_id == expected_first
        assert EDGE_CASES["equal_created_at"]["expect"]["suggestion"] == tied[0]["id"]

    def test_the_newest_model_may_sit_on_a_later_page(self):
        pages = load_fixture("models/paged.json")["pages"]
        everything = [r for page in pages for r in page["data"]]
        expected = newest_id(everything, "fable")
        assert expected not in [r["id"] for r in pages[0]["data"]], (
            "fixture precondition: newest is on page two"
        )
        fake = AnthropicHttpFake(pages=pages)

        choice = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert choice.model_id == expected
        first, second = fake.listing_requests
        assert "after_id" not in first.query
        assert second.query["after_id"] == pages[0]["last_id"]
        assert fake.listing_sequences == 1, "reading the second page is part of one listing"
        assert fake.misuse == []

    def test_listing_requests_a_bounded_page_size_and_a_finite_timeout(self):
        fake = captured_fake()

        model_config.resolve(Knob.SUGGESTION, fake.client)

        assert fake.listing_requests, "no listing request was made"
        request = fake.listing_requests[0]
        assert 1 <= int(request.query["limit"]) <= 1000, "the Models API accepts 1 to 1000 per page"
        assert 0 < request.timeout["read"] <= 10, (
            "the listing holds the single-flight lock that request threads wait on, so it is capped at 10 s"
        )

    @pytest.mark.parametrize("knob", KNOBS)
    def test_when_no_record_carries_line_the_family_is_the_id_prefix(self, knob):
        page = load_fixture("models/no_line.json")
        assert all("line" not in r for r in page["data"]), "fixture precondition"
        impostors = [
            r
            for r in page["data"]
            if "fable" in r["id"] and not r["id"].startswith("claude-fable-")
        ]
        assert impostors and impostors[0]["created_at"] == max(
            r["created_at"] for r in page["data"]
        ), "fixture precondition: a newer id contains fable without the prefix"
        fake = AnthropicHttpFake(pages=[page])

        choice = model_config.resolve(knob, fake.client)

        assert choice.model_id == page["expect"][EXPECT_KEY[knob]]

    @pytest.mark.parametrize("knob", KNOBS)
    def test_a_missing_lifecycle_key_counts_as_active(self, knob):
        page = load_fixture("models/no_lifecycle.json")
        assert all("lifecycle" not in r for r in page["data"]), "fixture precondition"
        fake = AnthropicHttpFake(pages=[page])

        choice = model_config.resolve(knob, fake.client)

        assert choice.model_id == page["expect"][EXPECT_KEY[knob]]
        assert choice.source == "resolved"

    def test_a_family_absent_from_the_listing_is_unavailable_never_another_familys_model(self):
        page = load_fixture("models/no_fable.json")
        fake = AnthropicHttpFake(pages=[page])

        suggestion = model_config.resolve(Knob.SUGGESTION, fake.client)
        synthesis = model_config.resolve(Knob.SYNTHESIS, fake.client)

        assert suggestion.source == "unavailable"
        assert suggestion.model_id == ""
        assert suggestion.badge_text == model_config.MODEL_UNAVAILABLE_TEXT
        assert synthesis.model_id == page["expect"]["synthesis"]

    def test_an_empty_listing_leaves_both_families_unavailable(self):
        fake = AnthropicHttpFake(
            pages=[{"data": [], "has_more": False, "first_id": None, "last_id": None}]
        )

        choices = [
            model_config.resolve(knob, fake.client) for knob in (Knob.SUGGESTION, Knob.SYNTHESIS)
        ]

        assert fake.listing_sequences >= 1, (
            "an empty page is a listing that succeeded with nothing in it"
        )
        assert [c.source for c in choices] == ["unavailable", "unavailable"]
        assert [c.model_id for c in choices] == ["", ""]

    def test_a_client_whose_listing_is_empty_gives_an_unavailable_choice_without_raising(self):
        client = MagicMock()
        client.with_options.return_value = client  # the resolver may derive a per-call client

        choice = model_config.resolve(Knob.SUGGESTION, client)

        assert client.models.list.called, "the listing was attempted through the caller's client"
        assert choice.source == "unavailable" and choice.model_id == ""


class TestChoiceValue:
    def test_the_unavailable_text_is_the_agreed_wording(self):
        assert model_config.MODEL_UNAVAILABLE_TEXT == "model unavailable"

    def test_badge_text_is_the_display_name_for_a_resolved_model(self):
        choice = model_config.resolve(Knob.SUGGESTION, captured_fake().client)

        assert choice.badge_text == choice.display_name
        assert (
            choice.display_name == record_by_id(CAPTURED["data"], choice.model_id)["display_name"]
        )

    def test_badge_text_is_the_raw_override_for_an_override(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR[Knob.SUGGESTION], "operator-chosen-model-x")

        choice = model_config.resolve(Knob.SUGGESTION, captured_fake().client)

        assert choice.badge_text == "operator-chosen-model-x"

    def test_badge_text_says_model_unavailable_when_nothing_resolved(self):
        fake = AnthropicHttpFake()
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        choice = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert choice.badge_text == model_config.MODEL_UNAVAILABLE_TEXT

    def test_a_choice_cannot_be_edited_after_it_is_returned(self):
        choice = model_config.resolve(Knob.SUGGESTION, captured_fake().client)

        with pytest.raises(dataclasses.FrozenInstanceError):
            choice.model_id = "something-else"  # type: ignore[misc]


class TestNeverResolved:
    def test_a_failed_first_listing_is_unavailable_with_one_type_only_log(self, caplog):
        fake = AnthropicHttpFake()
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))
        caplog.set_level(logging.DEBUG)
        caplog.clear()

        choice = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert choice.source == "unavailable" and choice.model_id == ""
        body = load_fixture("errors/503_unavailable.json")["body"]
        assert_logged_type_only(
            caplog,
            failure_type_name(error_outcome("503_unavailable")),
            body["error"]["message"],
            body["request_id"],
        )

    def test_an_unexpected_client_exception_is_unavailable_and_logged_by_type_only(self, caplog):
        client = MagicMock()
        client.with_options.return_value = client  # the resolver may derive a per-call client
        client.models.list.side_effect = RuntimeError("boom-detail-77ce")
        caplog.set_level(logging.DEBUG)
        caplog.clear()

        choice = model_config.resolve(Knob.SUGGESTION, client)

        assert choice.source == "unavailable"
        assert_logged_type_only(caplog, "RuntimeError", "boom-detail-77ce")

    def test_a_listing_that_fails_on_a_later_page_is_a_failed_listing_not_a_partial_result(self):
        pages = load_fixture("models/paged.json")["pages"]
        fake = AnthropicHttpFake(pages=pages)
        fake.queue_listing_outcome(pages[0])
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        choice = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert len(fake.listing_requests) == 2, "page two was requested and failed"
        assert choice.source == "unavailable", "the newest model may be on the page that failed"
        assert choice.model_id == ""

    def test_registry_reset_forgets_every_resolution(self):
        fake = captured_fake()
        model_config.resolve(Knob.SUGGESTION, fake.client)
        assert fake.listing_sequences == 1

        model_config._reset_registry()

        assert model_config.peek(Knob.SUGGESTION).source == "unavailable"
        model_config.resolve(Knob.SUGGESTION, fake.client)
        assert fake.listing_sequences == 2


class TestRelisting:
    def test_resolution_is_reused_inside_a_day_and_relisted_after_it(self, clock):
        fake = captured_fake()
        first = first_resolution(fake, Knob.SUGGESTION)
        newer_page = load_fixture("models/relisted_newer_fable.json")
        newer_id = newest_id(newer_page["data"], "fable")
        assert newer_id != first.model_id, "fixture precondition"
        fake.set_pages([newer_page])

        clock.advance(DAY - 1)
        inside_the_day = model_config.resolve(Knob.SUGGESTION, fake.client)
        assert inside_the_day.model_id == first.model_id
        assert fake.listing_sequences == 1, "no listing before the day is up"

        clock.advance(2)
        after_the_day = model_config.resolve(Knob.SUGGESTION, fake.client)
        assert fake.listing_sequences == 2, (
            "exactly one new listing once the last success is older than a day"
        )
        assert after_the_day.model_id == newer_id

    def test_an_opus_family_resolution_follows_the_same_daily_refresh(self, clock):
        fake = captured_fake()
        first = first_resolution(fake, Knob.SYNTHESIS)
        newer_page = load_fixture("models/relisted_newer_opus.json")
        fake.set_pages([newer_page])

        clock.advance(DAY + 1)
        refreshed = model_config.resolve(Knob.SYNTHESIS, fake.client)

        assert refreshed.model_id == newest_id(newer_page["data"], "opus") != first.model_id

    @pytest.mark.parametrize("make_outcome", LISTING_FAILURES)
    def test_a_failed_relisting_keeps_the_last_resolved_model_and_logs_the_type_only(
        self, make_outcome, clock, caplog
    ):
        fake = captured_fake()
        first = first_resolution(fake, Knob.SUGGESTION)
        clock.advance(DAY + 1)
        fake.queue_listing_outcome(make_outcome())
        caplog.set_level(logging.DEBUG)
        caplog.clear()

        again = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert fake.listing_sequences == 2, "the due relisting was attempted"
        assert again.model_id == first.model_id
        assert again.source == "resolved"
        failure_body = make_outcome()
        fragments = [READ_TIMEOUT_DETAIL, CONNECT_ERROR_DETAIL]
        if isinstance(failure_body, dict):
            fragments += [
                failure_body["body"]["error"]["message"],
                failure_body["body"]["request_id"],
            ]
        assert_logged_type_only(caplog, failure_type_name(make_outcome), *fragments)

    def test_a_relisting_that_fails_on_a_later_page_keeps_the_last_resolved_model(self, clock):
        pages = load_fixture("models/paged.json")["pages"]
        fake = captured_fake()
        first = first_resolution(fake, Knob.SUGGESTION)
        clock.advance(DAY + 1)
        fake.set_pages(pages)
        fake.queue_listing_outcome(pages[0])
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        again = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert again.model_id == first.model_id, "page one alone holds an older fable-line model"

    def test_failures_keep_the_last_model_for_the_process_lifetime_and_every_call_retries(
        self, clock
    ):
        fake = captured_fake()
        first = first_resolution(fake, Knob.SUGGESTION)
        clock.advance(DAY + 1)

        for attempt in range(2):  # the next call after a failed attempt attempts the listing again
            fake.queue_listing_outcome(load_fixture("errors/500_api_error.json"))
            assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == first.model_id
            assert fake.listing_sequences == 2 + attempt

        clock.advance(10 * DAY)
        fake.queue_listing_outcome(load_fixture("errors/500_api_error.json"))
        assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == first.model_id
        assert fake.listing_sequences == 4

        newer_page = load_fixture("models/relisted_newer_fable.json")
        fake.set_pages([newer_page])
        recovered = model_config.resolve(Knob.SUGGESTION, fake.client)
        assert recovered.model_id == newest_id(newer_page["data"], "fable")
        assert fake.listing_sequences == 5

    def test_a_failed_attempt_does_not_restart_the_day(self, clock):
        fake = captured_fake()
        model_config.resolve(Knob.SUGGESTION, fake.client)
        clock.advance(DAY + 1)
        fake.queue_listing_outcome(load_fixture("errors/500_api_error.json"))
        model_config.resolve(Knob.SUGGESTION, fake.client)
        newer_page = load_fixture("models/relisted_newer_fable.json")
        fake.set_pages([newer_page])

        recovered = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert recovered.model_id == newest_id(newer_page["data"], "fable"), (
            "age counts from the last success"
        )


def invalidate_behind_a_due_relisting(fake, clock, knob, model_id):
    """A due ``resolve`` holds the listing; a 404 for ``model_id`` arrives meanwhile.

    Returns the answer of the ``invalidate`` that waited for the listing.
    """
    clock.advance(DAY + 1)
    gate = fake.hold_listings()
    answers, failures = [], []

    def resolve_due():
        try:
            model_config.resolve(knob, fake.make_client())
        except BaseException as exc:  # surfaced below, never swallowed
            failures.append(exc)

    def invalidate_stale():
        try:
            answers.append(model_config.invalidate(knob, model_id, fake.make_client()))
        except BaseException as exc:  # surfaced below, never swallowed
            failures.append(exc)

    lister = threading.Thread(target=resolve_due, daemon=True)
    invalidator = threading.Thread(target=invalidate_stale, daemon=True)
    try:
        lister.start()
        assert fake.listing_started.wait(5), "the relisting never started"
        invalidator.start()
        time.sleep(SETTLE_SECONDS)  # the 404 arrives while the relisting holds the lock
    finally:
        gate.set()
    lister.join(10)
    invalidator.join(10)
    assert not (lister.is_alive() or invalidator.is_alive()), "a thread never returned"
    assert not failures, failures
    return answers[0]


class TestConcurrentResolution:
    def test_ten_concurrent_first_calls_share_one_listing_sequence(self):
        fake = captured_fake()
        expected = newest_id(CAPTURED["data"], "fable")

        results, sequences_while_held = call_concurrently(
            fake, lambda i: model_config.resolve(Knob.SUGGESTION, fake.make_client()), 10
        )

        assert sequences_while_held == 1, "waiting threads must not start listings of their own"
        assert fake.listing_sequences == 1
        assert {r.model_id for r in results} == {expected} and len(results) == 10

    def test_ten_concurrent_calls_when_a_relisting_is_due_share_one_new_listing(self, clock):
        fake = captured_fake()
        model_config.resolve(Knob.SUGGESTION, fake.client)
        newer_page = load_fixture("models/relisted_newer_fable.json")
        fake.set_pages([newer_page])
        clock.advance(DAY + 1)

        results, sequences_while_held = call_concurrently(
            fake, lambda i: model_config.resolve(Knob.SUGGESTION, fake.make_client()), 10
        )

        assert sequences_while_held == 2 and fake.listing_sequences == 2
        assert {r.model_id for r in results} == {newest_id(newer_page["data"], "fable")}

    def test_concurrent_callers_share_one_failed_attempt_and_the_next_call_lists_again(self):
        fake = AnthropicHttpFake(pages=[CAPTURED])
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        results, sequences_while_held = call_concurrently(
            fake, lambda i: model_config.resolve(Knob.SUGGESTION, fake.make_client()), 5
        )

        assert sequences_while_held == 1 and fake.listing_sequences == 1
        assert [r.source for r in results] == ["unavailable"] * 5

        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))
        assert model_config.resolve(Knob.SUGGESTION, fake.client).source == "unavailable"
        assert fake.listing_sequences == 2, (
            "a call after a failed attempt attempts the listing again"
        )

        later = model_config.resolve(Knob.SUGGESTION, fake.client)
        assert later.model_id == newest_id(CAPTURED["data"], "fable")
        assert fake.listing_sequences == 3


class TestOverrides:
    @pytest.mark.parametrize("value", [None, "", "   "], ids=["absent", "empty", "spaces"])
    def test_an_absent_empty_or_blank_override_resolves_the_family(self, value, monkeypatch):
        if value is None:
            monkeypatch.delenv(ENV_VAR[Knob.SUGGESTION], raising=False)
        else:
            monkeypatch.setenv(ENV_VAR[Knob.SUGGESTION], value)
        fake = captured_fake()

        choice = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert choice.model_id == newest_id(CAPTURED["data"], "fable")
        assert choice.source == "resolved"

    def test_an_override_is_used_verbatim_across_families_and_never_listed(self, monkeypatch):
        opus_id = newest_id(CAPTURED["data"], "opus")
        monkeypatch.setenv(ENV_VAR[Knob.SUGGESTION], opus_id)
        fake = captured_fake()

        choice = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert choice.model_id == opus_id
        assert choice.source == "override"
        assert choice.env_var == ENV_VAR[Knob.SUGGESTION]
        assert fake.requests == [], "an override is neither listed nor validated"

    def test_an_override_the_api_does_not_list_is_still_returned_verbatim(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR[Knob.SYNTHESIS], "operator-chosen-model-x")
        fake = captured_fake()

        choice = model_config.resolve(Knob.SYNTHESIS, fake.client)

        assert (choice.model_id, choice.source) == ("operator-chosen-model-x", "override")
        assert fake.requests == []

    def test_an_override_needs_no_listing_while_the_models_api_is_failing(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR[Knob.SUGGESTION], newest_id(CAPTURED["data"], "opus"))
        fake = AnthropicHttpFake()
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        choice = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert choice.source == "override" and choice.model_id
        assert fake.requests == []

    @pytest.mark.parametrize("knob", KNOBS)
    def test_each_knob_reads_only_its_own_variable(self, knob, monkeypatch):
        other = Knob.SYNTHESIS if knob is Knob.SUGGESTION else Knob.SUGGESTION
        monkeypatch.setenv(ENV_VAR[other], "operator-chosen-model-x")
        fake = captured_fake()

        choice = model_config.resolve(knob, fake.client)

        assert choice.source == "resolved"
        assert choice.model_id == newest_id(CAPTURED["data"], LINE[knob])
        assert model_config.resolve(other, fake.client).model_id == "operator-chosen-model-x"

    def test_the_override_is_read_on_every_call_without_a_restart(self, monkeypatch):
        fake = captured_fake()
        resolved = first_resolution(fake, Knob.SUGGESTION)
        monkeypatch.setenv(ENV_VAR[Knob.SUGGESTION], "operator-chosen-model-x")
        assert (
            model_config.resolve(Knob.SUGGESTION, fake.client).model_id == "operator-chosen-model-x"
        )
        monkeypatch.delenv(ENV_VAR[Knob.SUGGESTION])

        back = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert back.model_id == resolved.model_id and back.source == "resolved"
        assert fake.listing_sequences == 1, "dropping the override reuses the cached resolution"


class TestRunPin:
    @pytest.mark.parametrize(
        "resolved_before_block", [False, True], ids=["first call in block", "cached before block"]
    )
    def test_a_run_keeps_the_model_it_started_with_across_a_relisting(
        self, resolved_before_block, clock
    ):
        fake = captured_fake()
        if resolved_before_block:
            model_config.resolve(Knob.SUGGESTION, fake.client)
        newer_page = load_fixture("models/relisted_newer_fable.json")

        with model_config.pinned_run():
            first = first_resolution(fake, Knob.SUGGESTION)
            fake.set_pages([newer_page])
            clock.advance(DAY + 1)
            inside = model_config.resolve(Knob.SUGGESTION, fake.client)
            assert inside.model_id == first.model_id, "every call in a run names one model"
            assert fake.listing_sequences == 1

        after = model_config.resolve(Knob.SUGGESTION, fake.client)
        assert after.model_id == newest_id(newer_page["data"], "fable") != first.model_id

    def test_a_context_outside_the_run_is_not_pinned(self, clock):
        fake = captured_fake()
        newer_page = load_fixture("models/relisted_newer_fable.json")

        with model_config.pinned_run():
            first = first_resolution(fake, Knob.SUGGESTION)
            fake.set_pages([newer_page])
            clock.advance(DAY + 1)
            elsewhere = contextvars.Context().run(
                model_config.resolve, Knob.SUGGESTION, fake.client
            )

        assert elsewhere.model_id == newest_id(newer_page["data"], "fable") != first.model_id

    def test_a_nested_run_reuses_the_outer_pin(self, clock):
        fake = captured_fake()
        newer_page = load_fixture("models/relisted_newer_fable.json")

        with model_config.pinned_run():
            first = first_resolution(fake, Knob.SUGGESTION)
            fake.set_pages([newer_page])
            clock.advance(DAY + 1)
            with model_config.pinned_run():
                assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == first.model_id
            assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == first.model_id

    def test_each_family_is_pinned_independently(self, clock):
        fake = captured_fake()
        fake_newer_opus = load_fixture("models/relisted_newer_opus.json")

        with model_config.pinned_run():
            fable = model_config.resolve(Knob.SUGGESTION, fake.client)
            fake.set_pages([fake_newer_opus])
            clock.advance(DAY + 1)
            opus_first_seen = model_config.resolve(Knob.SYNTHESIS, fake.client)
            assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == fable.model_id

        assert opus_first_seen.model_id == newest_id(fake_newer_opus["data"], "opus")

    def test_an_unavailable_result_is_not_pinned_for_the_run(self):
        fake = captured_fake()
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        with model_config.pinned_run():
            assert model_config.resolve(Knob.SUGGESTION, fake.client).source == "unavailable"
            recovered = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert recovered.model_id == newest_id(CAPTURED["data"], "fable")

    def test_the_pin_ends_with_the_block_even_when_the_block_raises(self, clock):
        fake = captured_fake()
        newer_page = load_fixture("models/relisted_newer_fable.json")

        with pytest.raises(RuntimeError):
            with model_config.pinned_run():
                model_config.resolve(Knob.SUGGESTION, fake.client)
                raise RuntimeError("run failed")

        fake.set_pages([newer_page])
        clock.advance(DAY + 1)
        assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == newest_id(
            newer_page["data"], "fable"
        )


class TestInvalidate:
    def test_a_model_the_messages_api_stopped_serving_is_replaced_by_one_new_listing(self):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)
        newer_page = load_fixture("models/relisted_newer_fable.json")
        fake.set_pages([newer_page])

        replacement = model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.client)

        assert fake.listing_sequences == 2
        assert replacement.model_id == newest_id(newer_page["data"], "fable") != stale.model_id
        assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == replacement.model_id
        assert fake.listing_sequences == 2

    def test_a_listing_that_names_the_same_model_returns_that_same_choice(self):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)

        replacement = model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.client)

        assert fake.listing_sequences == 2
        assert replacement.model_id == stale.model_id, (
            "the caller compares ids to decide whether to resend"
        )

    def test_a_late_404_for_an_already_replaced_model_keeps_the_current_resolution(self):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)
        newer_page = load_fixture("models/relisted_newer_fable.json")
        fake.set_pages([newer_page])
        replacement = model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.client)
        assert replacement.model_id != stale.model_id and fake.listing_sequences == 2

        late = model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.client)

        assert late.model_id == replacement.model_id, (
            "a 404 for the old model says nothing about the new one"
        )
        assert model_config.peek(Knob.SUGGESTION).model_id == replacement.model_id
        assert fake.listing_sequences == 2, (
            "no new listing for a model that is no longer the resolution"
        )

    def test_a_failed_listing_after_a_404_is_unavailable_and_never_the_dropped_model(self):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        replacement = model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.client)

        assert replacement.source == "unavailable" and replacement.model_id == ""
        assert model_config.peek(Knob.SUGGESTION).source == "unavailable"
        assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == stale.model_id
        assert fake.listing_sequences == 3, "the next call lists again"

    def test_concurrent_404s_share_one_new_listing(self):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)
        newer_page = load_fixture("models/relisted_newer_fable.json")
        fake.set_pages([newer_page])

        results, sequences_while_held = call_concurrently(
            fake,
            lambda i: model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.make_client()),
            5,
        )

        assert sequences_while_held == 2 and fake.listing_sequences == 2
        assert {r.model_id for r in results} == {newest_id(newer_page["data"], "fable")}

    @pytest.mark.parametrize("knob", KNOBS)
    def test_concurrent_404s_share_one_relisting_that_names_the_same_model(self, knob):
        fake = captured_fake()
        stale = first_resolution(fake, knob)

        results, sequences_while_held = call_concurrently(
            fake, lambda i: model_config.invalidate(knob, stale.model_id, fake.make_client()), 5
        )

        assert sequences_while_held == 2 and fake.listing_sequences == 2, (
            "every waiting 404 shares the relisting that finished while it waited, "
            "whether or not that relisting named a different model"
        )
        assert fake.max_concurrent_listings == 1
        assert len(results) == 5 and len(set(results)) == 1, "every caller gets the same answer"
        assert results[0].model_id == stale.model_id and results[0] == model_config.peek(knob)

    def test_concurrent_404s_share_one_relisting_that_fails(self):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)
        for _ in range(5):  # the Models API stays down for as long as anyone asks
            fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        results, sequences_while_held = call_concurrently(
            fake,
            lambda i: model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.make_client()),
            5,
        )

        assert sequences_while_held == 2 and fake.listing_sequences == 2, (
            "during an outage the waiting 404s must not each hold the listing lock for an "
            "attempt of their own"
        )
        assert len(results) == 5 and len(set(results)) == 1, "every caller gets the same answer"
        assert results[0].source == "unavailable" and results[0].model_id == ""
        assert model_config.peek(Knob.SUGGESTION).source == "unavailable"

    def test_a_failed_relisting_shared_by_concurrent_404s_does_not_stop_the_next_call_listing(self):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)
        fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        results, _ = call_concurrently(
            fake,
            lambda i: model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.make_client()),
            5,
        )

        assert fake.listing_sequences == 2
        assert {r.source for r in results} == {"unavailable"}
        later = model_config.resolve(Knob.SUGGESTION, fake.client)
        assert later.model_id == stale.model_id
        assert fake.listing_sequences == 3, "a shared failed attempt is not remembered as success"

    def test_concurrent_404s_for_both_families_share_one_relisting(self):
        fake = captured_fake()
        knobs = (Knob.SUGGESTION, Knob.SYNTHESIS)
        stale = {knob: first_resolution(fake, knob) for knob in knobs}
        assert fake.listing_sequences == 1, "fixture precondition: one listing serves both families"

        def invalidate_own_family(i):
            knob = knobs[i % 2]
            return knob, model_config.invalidate(knob, stale[knob].model_id, fake.make_client())

        results, sequences_while_held = call_concurrently(fake, invalidate_own_family, 6)

        assert sequences_while_held == 2 and fake.listing_sequences == 2, (
            "one listing refreshes both families, so a 404 for either shares it"
        )
        for knob in knobs:
            answers = {choice for owner, choice in results if owner is knob}
            assert answers == {model_config.peek(knob)}
            assert model_config.peek(knob).model_id == stale[knob].model_id

    @pytest.mark.parametrize("relisting", ["names-the-same-model", "fails"])
    def test_a_404_behind_a_due_relisting_that_ends_without_a_newer_model_shares_it(
        self, clock, relisting
    ):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)
        if relisting == "fails":
            fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

        answer = invalidate_behind_a_due_relisting(fake, clock, Knob.SUGGESTION, stale.model_id)

        assert fake.listing_sequences == 2, (
            "the relisting that finished while the 404 waited is the one the 404 shares"
        )
        assert answer == model_config.peek(Knob.SUGGESTION)

    @pytest.mark.parametrize("knob", KNOBS)
    def test_each_404_that_arrives_after_a_listing_finished_lists_again(self, knob):
        fake = captured_fake()
        stale = first_resolution(fake, knob)

        first = model_config.invalidate(knob, stale.model_id, fake.client)
        second = model_config.invalidate(knob, stale.model_id, fake.client)

        assert fake.listing_sequences == 3, "only concurrent 404s share a listing, not later ones"
        assert first.model_id == second.model_id == stale.model_id

    def test_inside_a_run_the_replacement_takes_the_pinned_entrys_place(self):
        fake = captured_fake()
        newer_page = load_fixture("models/relisted_newer_fable.json")

        with model_config.pinned_run():
            stale = first_resolution(fake, Knob.SUGGESTION)
            fake.set_pages([newer_page])
            replacement = model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.client)
            later = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert replacement.model_id == newest_id(newer_page["data"], "fable")
        assert later.model_id == replacement.model_id
        assert fake.listing_sequences == 2

    def test_a_404_for_a_run_pinned_model_never_drops_the_newer_registry_model(self, clock):
        fake = captured_fake()
        newer_page = load_fixture("models/relisted_newer_fable.json")
        newer_id = newest_id(newer_page["data"], "fable")

        with model_config.pinned_run():
            pinned = first_resolution(fake, Knob.SUGGESTION)
            fake.set_pages([newer_page])
            clock.advance(DAY + 1)
            # A context outside the run (another thread starts with an empty context) relists.
            outside = contextvars.Context().run(
                model_config.resolve, Knob.SUGGESTION, fake.make_client()
            )
            assert outside.model_id == newer_id != pinned.model_id, "fixture precondition"
            assert fake.listing_sequences == 2
            fake.queue_listing_outcome(load_fixture("errors/503_unavailable.json"))

            answer = model_config.invalidate(Knob.SUGGESTION, pinned.model_id, fake.client)

            assert answer.model_id == newer_id, "the pin takes the registry model, not a listing"
            assert fake.listing_sequences == 2, "the 404 was for a model the registry had dropped"
            inside = model_config.resolve(Knob.SUGGESTION, fake.client)
            assert inside.model_id == newer_id, "the run now uses the registry model"

        after = model_config.peek(Knob.SUGGESTION)
        assert after.source == "resolved" and after.model_id == newer_id
        assert model_config.resolve(Knob.SUGGESTION, fake.client).model_id == newer_id
        assert fake.listing_sequences == 2, (
            "the registry entry stays fresh: a 404 for a pinned model does not expire it"
        )

    def test_a_404_waits_for_a_listing_in_flight_before_deciding_whether_to_drop_the_model(
        self, clock
    ):
        fake = captured_fake()
        stale = first_resolution(fake, Knob.SUGGESTION)
        newer_page = load_fixture("models/relisted_newer_fable.json")
        fake.set_pages([newer_page])
        clock.advance(DAY + 1)
        gate = fake.hold_listings()
        answers, failures = [], []

        def resolve_due():
            model_config.resolve(Knob.SUGGESTION, fake.make_client())

        def invalidate_stale():
            try:
                answers.append(
                    model_config.invalidate(Knob.SUGGESTION, stale.model_id, fake.make_client())
                )
            except BaseException as exc:  # surfaced below, never swallowed
                failures.append(exc)

        lister = threading.Thread(target=resolve_due, daemon=True)
        invalidator = threading.Thread(target=invalidate_stale, daemon=True)
        try:
            lister.start()
            assert fake.listing_started.wait(5), "the relisting never started"
            invalidator.start()
            time.sleep(SETTLE_SECONDS)  # the 404 arrives while the relisting holds the lock
            while_listing = model_config.peek(Knob.SUGGESTION)
        finally:
            gate.set()
        lister.join(10)
        invalidator.join(10)

        assert not (lister.is_alive() or invalidator.is_alive()), "a thread never returned"
        assert not failures, failures
        assert while_listing.model_id == stale.model_id, (
            "a 404 must not drop the entry before it holds the listing lock: the listing in "
            "flight may be about to replace it"
        )
        newer_id = newest_id(newer_page["data"], "fable")
        assert answers[0].model_id == newer_id
        assert model_config.peek(Knob.SUGGESTION).model_id == newer_id
        assert fake.listing_sequences == 2, "the 404 shared the listing already in flight"


class TestPeek:
    def test_peek_of_an_unresolved_family_is_unavailable_and_makes_no_request(self):
        fake = captured_fake()

        choice = model_config.peek(Knob.SUGGESTION)

        assert choice.source == "unavailable" and choice.model_id == ""
        assert choice.badge_text == model_config.MODEL_UNAVAILABLE_TEXT
        assert fake.requests == []

    def test_peek_returns_the_cached_resolution_without_listing_even_when_a_relisting_is_due(
        self, clock
    ):
        fake = captured_fake()
        resolved = first_resolution(fake, Knob.SUGGESTION)
        clock.advance(10 * DAY)

        peeked = model_config.peek(Knob.SUGGESTION)

        assert (peeked.model_id, peeked.display_name, peeked.source) == (
            resolved.model_id,
            resolved.display_name,
            "resolved",
        )
        assert fake.listing_sequences == 1

    @pytest.mark.parametrize(
        "resolved_first", [True, False], ids=["cached name", "nothing resolved"]
    )
    def test_peek_never_waits_for_a_listing_in_flight(self, resolved_first, clock):
        fake = captured_fake()
        resolved = first_resolution(fake) if resolved_first else None
        clock.advance(DAY + 1)
        gate = fake.hold_listings()
        listing = threading.Thread(
            target=model_config.resolve, args=(Knob.SUGGESTION, fake.make_client()), daemon=True
        )
        try:
            listing.start()
            assert fake.listing_started.wait(5)

            choice = returns_within(2, model_config.peek, Knob.SUGGESTION)
        finally:
            gate.set()
            listing.join(10)

        if resolved_first:
            assert choice.model_id == resolved.model_id
        else:
            assert choice.source == "unavailable"

    def test_peek_reports_an_override_without_listing(self, monkeypatch):
        monkeypatch.setenv(ENV_VAR[Knob.SYNTHESIS], "operator-chosen-model-x")
        fake = captured_fake()

        choice = model_config.peek(Knob.SYNTHESIS)

        assert (choice.model_id, choice.source, choice.badge_text) == (
            "operator-chosen-model-x",
            "override",
            "operator-chosen-model-x",
        )
        assert fake.requests == []


FAILURE_FACTORIES = [pytest.param(p.values[0], id=p.id) for p in LISTING_FAILURES]


class TestProductionShapedClient:
    """The listing runs with the SDK default retry setting, as the application client does."""

    @pytest.fixture
    def sdk_sleeps(self, monkeypatch):
        """Record, and skip, every wait the SDK makes between its own retries."""
        sleeps = []

        class RecordingTime:
            def __getattr__(self, name):
                return getattr(time, name)

            @staticmethod
            def sleep(seconds):
                sleeps.append(seconds)

        monkeypatch.setattr(sdk_base_client, "time", RecordingTime())
        return sleeps

    @pytest.fixture
    def resolver_sleeps(self, monkeypatch):
        """Record, and skip, every wait the resolver itself makes through ``model_config.time``."""
        sleeps = []
        monkeypatch.setattr(
            model_config,
            "time",
            types.SimpleNamespace(monotonic=time.monotonic, sleep=sleeps.append),
        )
        return sleeps

    @pytest.fixture
    def production_client(self):
        fake = AnthropicHttpFake()
        client = fake.make_client(max_retries=anthropic.DEFAULT_MAX_RETRIES)
        assert client.max_retries == anthropic.DEFAULT_MAX_RETRIES > 0, "fixture precondition"
        return fake, client

    def test_the_application_client_keeps_the_sdk_default_retry_setting(self, monkeypatch):
        import ai_advisor

        monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_API_KEY)

        assert ai_advisor._build_client().max_retries == anthropic.DEFAULT_MAX_RETRIES

    @pytest.mark.parametrize("make_outcome", FAILURE_FACTORIES)
    def test_a_failed_listing_is_one_http_attempt_and_waits_for_no_sdk_backoff(
        self, make_outcome, production_client, sdk_sleeps, resolver_sleeps
    ):
        fake, client = production_client
        for _ in range(anthropic.DEFAULT_MAX_RETRIES + 1):
            fake.queue_listing_outcome(make_outcome())

        choice = returns_within(UNWAITED_SECONDS, model_config.resolve, Knob.SUGGESTION, client)

        assert choice.source == "unavailable"
        assert len(fake.listing_requests) == 1, (
            "the lock is held for one attempt; the retry policy is the next call, not the SDK"
        )
        assert sdk_sleeps == [], "no wait for the SDK backoff while the listing lock is held"
        assert resolver_sleeps == [], "the resolver adds no wait of its own while it holds the lock"

    def test_a_retry_after_header_is_not_waited_out(
        self, production_client, sdk_sleeps, resolver_sleeps
    ):
        fake, client = production_client
        # Captured 429 body; the header is the documented standard one, added by hand.
        throttled = {**load_fixture("errors/429_rate_limit.json"), "headers": {"retry-after": "30"}}
        for _ in range(anthropic.DEFAULT_MAX_RETRIES + 1):
            fake.queue_listing_outcome(dict(throttled))

        # The deadline catches any wait, the recorders name the two places a wait could come from.
        choice = returns_within(UNWAITED_SECONDS, model_config.resolve, Knob.SUGGESTION, client)

        assert choice.source == "unavailable"
        assert len(fake.listing_requests) == 1
        assert sdk_sleeps == [], "a Retry-After of up to 60 s must not hold the listing lock"
        assert resolver_sleeps == [], "the resolver must not sleep out the Retry-After itself"

    def test_a_successful_listing_through_the_default_client_reads_each_page_once(self, sdk_sleeps):
        pages = load_fixture("models/paged.json")["pages"]
        everything = [r for page in pages for r in page["data"]]
        fake = AnthropicHttpFake(pages=pages)
        client = fake.make_client(max_retries=anthropic.DEFAULT_MAX_RETRIES)

        choice = model_config.resolve(Knob.SUGGESTION, client)

        assert choice.model_id == newest_id(everything, "fable")
        assert len(fake.listing_requests) == len(pages)
        assert fake.misuse == [] and sdk_sleeps == []


class TestListingFailureDiagnostics:
    @pytest.mark.parametrize("make_outcome", FAILURE_FACTORIES)
    def test_a_failed_listing_leaves_a_debug_record_with_the_exception_detail(
        self, make_outcome, caplog
    ):
        expected_type = failure_type_name(make_outcome)
        fake = AnthropicHttpFake()
        fake.queue_listing_outcome(make_outcome())
        caplog.set_level(logging.DEBUG)
        caplog.clear()

        model_config.resolve(Knob.SUGGESTION, fake.client)

        debug = [
            r
            for r in caplog.records
            if r.name == model_config.__name__ and r.levelno == logging.DEBUG and r.exc_info
        ]
        assert debug, "full detail belongs in a DEBUG record carrying exc_info"
        assert debug[0].exc_info[0].__name__ == expected_type
        assert len(warnings_logged(caplog)) == 1, "the WARNING stays one type-only record"

    def test_an_unexpected_client_exception_leaves_a_debug_record_with_its_traceback(self, caplog):
        client = MagicMock()
        client.with_options.return_value = client  # the resolver may derive a per-call client
        client.models.list.side_effect = RuntimeError("boom-detail-77ce")
        caplog.set_level(logging.DEBUG)
        caplog.clear()

        model_config.resolve(Knob.SUGGESTION, client)

        debug = [
            r
            for r in caplog.records
            if r.name == model_config.__name__ and r.levelno == logging.DEBUG and r.exc_info
        ]
        assert debug and debug[0].exc_info[0] is RuntimeError


class TestModuleDocumentation:
    def test_the_module_docstring_describes_the_resolver_and_both_knobs(self):
        doc = model_config.__doc__ or ""

        assert "untouched" not in doc.lower(), (
            "ADVISOR_SYNTHESIS_MODEL is now read by this module (Knob.SYNTHESIS)"
        )
        assert "ADVISOR_SYNTHESIS_MODEL" in doc
        assert "Models API" in doc, "the module resolves model IDs from the Models API"


class TestSecurity:
    def test_the_resolver_imports_no_anthropic_code_and_no_application_module(self):
        source = (pathlib.Path(model_config.__file__)).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not imported & {
            "anthropic",
            "ai_advisor",
            "database",
            "app",
            "advisors",
            "requests",
            "httpx",
        }

    def test_resolution_sends_only_model_listing_requests_with_no_secret_in_the_url(self):
        fake = captured_fake()

        model_config.resolve(Knob.SUGGESTION, fake.client)
        model_config.resolve(Knob.SYNTHESIS, fake.client)

        assert fake.listing_requests and fake.listing_requests == fake.requests
        assert all(r.method == "GET" for r in fake.requests)
        assert all(FAKE_API_KEY not in str(r.query) for r in fake.requests)
        assert fake.misuse == []

    def test_a_choice_never_carries_the_clients_credentials(self):
        fake = captured_fake()

        choice = model_config.resolve(Knob.SUGGESTION, fake.client)

        assert choice.source == "resolved"
        assert FAKE_API_KEY not in repr(choice)
