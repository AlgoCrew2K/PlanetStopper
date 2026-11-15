"""Concurrent preconditions requests share one decode, on the real threaded server.

A request that reaches a cache file another request is already decoding waits for that decode and
answers from its result; it never reads the file itself. The tests hold the first decode at a gate
(``DecodeCounter.hold_first_decode``), start more requests while it is held, prove each one has
reached the file's existence check (the probe's own unchanged step), give them a moment to settle,
and only then release the gate. A request that answered before the release did not wait, and a
decode count above one means a request read the file itself. Every join is bounded, so a hang
fails the test instead of stalling the run.
"""

from __future__ import annotations

import json
import logging
import os
import time

import pytest

from tests.app._preconditions_replay_world import (
    FILE_DATE,
    OLDER_DATE,
    ROUTE,
    SETTLE_SECONDS,
    WARNING_TEMPLATE,
    NetworkSpy,
    Request,
    all_degraded,
    assert_decodes,
    chdir_to_tmp,  # noqa: F401  (fixture)
    fetch,
    golden,
    install_counter,
    join_all,
    replay_of,
    replay_rows,
    run_together,
    server,  # noqa: F401  (fixture)
    standard,
    symphony_ids,
    watch_arrivals,
    with_dates_trimmed,
)

pytestmark = pytest.mark.usefixtures("chdir_to_tmp")

PERFORMANCE_PATH = "/api/performance?scope=aggregate&days=60d"


@pytest.fixture
def counter(monkeypatch):
    return install_counter(monkeypatch)


@pytest.fixture
def arrivals(monkeypatch):
    return watch_arrivals(monkeypatch)


def start_while_held(server, gate, arrivals, count: int) -> list[Request]:
    """Start ``count`` more requests while ``gate`` holds a decode, and wait until each has
    reached a cache file's existence check and had a moment to register as a waiter."""
    assert gate.entered.wait(10), "the first request never reached its decode"
    arrivals.reset()
    requests = [Request(server) for _ in range(count)]
    for request in requests:
        request.start()
    assert arrivals.wait_for_threads_besides(gate.owner, count), (
        f"only some of the {count} later requests reached the cache file"
    )
    time.sleep(SETTLE_SECONDS)
    return requests


def test_a_second_request_waits_for_the_decode_in_flight_and_shares_its_result(
    monkeypatch, counter, arrivals, server
):
    world = standard(monkeypatch)
    gate = counter.hold_first_decode()
    first = Request(server)
    first.start()
    (second,) = start_while_held(server, gate, arrivals, 1)
    answered_early = second.finished
    gate.release.set()
    join_all([first, second])
    assert not answered_early, "the second request answered while the first decode was held"
    assert not gate.timed_out
    assert_decodes(counter, (0, 0), 1, "two overlapping requests")
    assert counter.reads_of(world.files[FILE_DATE]) == 1
    for request in (first, second):
        assert request.fetched.status == 200
        assert request.fetched.data == golden("full")


def test_three_requests_started_while_a_decode_is_held_share_it(
    monkeypatch, counter, arrivals, server
):
    standard(monkeypatch)
    gate = counter.hold_first_decode()
    first = Request(server)
    first.start()
    later = start_while_held(server, gate, arrivals, 2)
    early = [r.finished for r in later]
    gate.release.set()
    join_all([first, *later])
    assert not any(early), "a later request answered before the held decode was released"
    assert_decodes(counter, (0, 0), 1, "three overlapping requests")
    for request in (first, *later):
        assert request.fetched.data == golden("full")


def test_a_corrupt_file_is_read_once_in_total_and_both_requests_move_to_the_older_file(
    monkeypatch, counter, arrivals, server, caplog
):
    world = standard(monkeypatch, file=False)
    older = world.write_file(OLDER_DATE)
    corrupt = world.write_corrupt(FILE_DATE)
    gate = counter.hold_first_decode()
    first = Request(server)
    first.start()
    (second,) = start_while_held(server, gate, arrivals, 1)
    gate.release.set()
    join_all([first, second])
    assert counter.reads_of(corrupt) == 1, f"the corrupt file was read {counter.reads} times"
    assert counter.reads_of(older) == 1, "the older valid file must be decoded once in total"
    assert_decodes(counter, (0, 0), 2, "corrupt newest plus valid older, two requests")
    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and r.msg == WARNING_TEMPLATE
    ]
    assert len(warnings) == 1, f"{len(warnings)} corrupt-file warnings, expected 1"
    assert os.path.basename(warnings[0].args[0]) == os.path.basename(corrupt)
    for request in (first, second):
        assert request.fetched.status == 200
        assert request.fetched.data == golden("corrupt_newest_valid_older")
    # A later request neither reads the unchanged corrupt file again nor warns about it.
    caplog.clear()
    mark = counter.mark()
    later = fetch(server)
    assert later.status == 200
    assert_decodes(counter, mark, 0, "a later request while both files are unchanged")
    assert [r for r in caplog.records if r.msg == WARNING_TEMPLATE] == []


def test_a_failed_shared_decode_degrades_both_requests_and_the_next_request_recovers(
    monkeypatch, counter, arrivals, server
):
    world = standard(monkeypatch)
    gate = counter.hold_first_decode(fail=True)
    first = Request(server)
    first.start()
    (second,) = start_while_held(server, gate, arrivals, 1)
    gate.release.set()
    join_all([first, second])
    assert not gate.timed_out
    assert len(counter.loader_calls) == 1, (
        f"the waiting request read the file itself: {counter.loader_calls}"
    )
    for request in (first, second):
        assert request.fetched.status == 200, "a failed shared decode must never be a 500"
        body = json.loads(request.fetched.data)
        assert sorted(body["symphonies"]) == symphony_ids()
        assert all_degraded(body), "every replay row must be the degraded row"
    # The fault was not a corrupt file, so it is not remembered: the next request decodes.
    mark = counter.mark()
    recovered = fetch(server)
    assert recovered.status == 200
    assert_decodes(counter, mark, 1, "the request after the failed decode")
    assert counter.reads_of(world.files[FILE_DATE]) == 1
    assert recovered.data == golden("full")


def test_a_request_that_finds_the_file_changed_never_answers_from_the_old_decode(
    monkeypatch, counter, arrivals, server
):
    world = standard(monkeypatch)
    gate = counter.hold_first_decode(after_read=True)  # the old content is decoded, then held
    first = Request(server)
    first.start()
    assert gate.entered.wait(10), "the first request never finished its decode"
    new_history = with_dates_trimmed(world.history)
    world.rewrite_in_place(FILE_DATE, new_history)
    second = Request(server)
    second.start()
    join_all([second], timeout=15)  # must not wait on the held decode of the OLD file
    expected = replay_rows(new_history, symphony_ids())
    assert replay_of(json.loads(second.fetched.data)) == expected, (
        "the second request answered from the old content"
    )
    gate.release.set()
    join_all([first])
    assert first.fetched.status == 200
    later = fetch(server)
    assert replay_of(json.loads(later.data)) == expected, (
        "a later request was served the old file's series"
    )


def test_a_cold_decode_in_flight_does_not_change_what_performance_answers(
    monkeypatch, counter, server
):
    standard(monkeypatch)
    alone = fetch(server, PERFORMANCE_PATH)
    assert alone.status == 200, f"{PERFORMANCE_PATH} answered {alone.status} alone"
    gate = counter.hold_first_decode()
    held = Request(server)
    held.start()
    assert gate.entered.wait(10), "the preconditions request never reached its decode"
    during = fetch(server, PERFORMANCE_PATH)
    assert not held.finished, "the decode finished before the concurrent request did"
    gate.release.set()
    join_all([held])
    assert during.status == 200
    assert during.data == alone.data, "the concurrent /api/performance body differs from alone"


def test_two_overlapping_requests_over_missing_files_decode_nothing_and_fetch_nothing(
    monkeypatch, counter, server
):
    spy = NetworkSpy()
    standard(monkeypatch, file=False)
    spy.install(monkeypatch)
    requests = run_together(server, [ROUTE, ROUTE])
    assert_decodes(counter, (0, 0), 0, "two requests over missing files")
    assert spy.calls == [], f"a request reached the network: {spy.calls}"
    for request in requests:
        assert request.fetched.status == 200
        assert request.fetched.data == golden("no_file")
