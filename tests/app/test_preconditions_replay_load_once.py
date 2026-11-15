"""The preconditions route reads its replay cache file once per request, and not at all while the
file is unchanged.

``GET /api/guard-alpha-preconditions`` asks for each held symphony's replay series. All of them
resolve the same portfolio-wide synthetic-history file, which was decoded once per symphony and
once per poll. These tests count the decodes at two seams (``json.load`` on a cache file and
``synthetic_history.load_cached_history``) while the real route runs over the captured excerpt
world (tests/app/_preconditions_replay_world.py), with real files in a per-test ``cache``
directory. Decode counts are asserted before any body check.

Covers the decode-once rule, the reuse of an unchanged file and the change cases that must not
reuse it, and the probe's log lines and cache-hit-only contract.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta

import pytest

import market_calendar
import synthetic_history
from tests.app._preconditions_replay_world import (
    ET,
    FILE_DATE,
    MISS_WORDING,
    NEWEST_DATE,
    OLDER_DATE,
    ROUTE,
    WARNING_TEMPLATE,
    NetworkSpy,
    all_degraded,
    assert_decodes,
    bot_state,
    bump_mtime,
    cache_path,
    chdir_to_tmp,  # noqa: F401  (fixture)
    delete_recent_shadow,
    excerpt,
    golden,
    history_of,
    install_counter,
    nth_trading_day_after,
    replay_of,
    replay_rows,
    restart_daemon,
    run_together,
    server,  # noqa: F401  (fixture)
    shadow_of,
    standard,
    symphony_ids,
    with_dates_trimmed,
    with_one_return_changed,
    with_same_length_return_change,
    write_history,
    write_truncated,
)

pytestmark = pytest.mark.usefixtures("chdir_to_tmp")

POLL_SECONDS = 60.0
WINDOW = synthetic_history.AUTOTUNE_CACHE_MAX_AGE_TRADING_DAYS


@pytest.fixture
def counter(monkeypatch):
    return install_counter(monkeypatch)


def golden_json(name: str) -> dict:
    return json.loads(golden(name))


def misses(caplog) -> list:
    return [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and r.getMessage().startswith(MISS_WORDING)
    ]


def corrupt_warnings(caplog) -> list:
    return [r for r in caplog.records if r.levelno == logging.WARNING and r.msg == WARNING_TEMPLATE]


def probe_offset(day: str, start: date) -> int | None:
    """How many sessions back from ``start`` the backward probe reaches ``day`` (None: never).

    An independent oracle of the probe's walk, used only to prove a test's clock puts a file at
    the edge it claims to sit on (the first candidate is the start date itself).
    """
    cursor, offset = start, 0
    while offset <= WINDOW:
        if cursor.isoformat() == day:
            return offset
        cursor -= timedelta(days=1)
        while not market_calendar.is_trading_day(cursor):
            cursor -= timedelta(days=1)
        offset += 1
    return None


def served_once(world, counter):
    """The first request over a cold daemon: it decodes the file exactly once. Returns it."""
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 1, "the first request over an unchanged file")
    return resp


def untouched_by_engine_cycle(world) -> None:
    """What an engine cycle rewrites between polls: returns and values, never the holdings."""
    state = world.loaded_state()
    for sid in symphony_ids():
        state[sid]["current_return"] += 1.0
        state[sid]["current_value"] += 100.0
    world.save_state(state)


# ---------------------------------------------------------------------------
# AC-1: each cache file is decoded at most once per request
# ---------------------------------------------------------------------------


def test_one_decode_serves_all_twelve_symphonies(monkeypatch, counter):
    world = standard(monkeypatch)
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 1, "one request over 12 held symphonies")
    assert counter.reads_of(world.files[FILE_DATE]) == 1
    body = resp.get_json()
    ids = symphony_ids()
    assert sorted(body["symphonies"]) == ids
    for entry in body["symphonies"].values():
        assert set(entry) == {"replay", "shadow"}
    assert replay_of(body) == replay_rows(world.history, ids), "rows must come from the file"
    assert not all_degraded(body), "the fixture must carry real statistics rows"


def test_one_decode_for_a_single_symphony(monkeypatch, counter):
    sid = symphony_ids()[0]
    world = standard(monkeypatch, ids=[sid])
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 1, "one request over one symphony")
    body = resp.get_json()
    assert list(body["symphonies"]) == [sid]
    assert replay_of(body) == replay_rows(world.history, [sid])


def test_no_symphony_in_the_state_decodes_nothing(monkeypatch, counter):
    world = standard(monkeypatch, ids=[], metadata=True, file=False)
    write_history(cache_path(bot_state(), FILE_DATE), history_of())  # a file the state cannot name
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 0, "a state holding only the five metadata keys")
    assert resp.get_json() == {"symphonies": {}}


def test_holdings_missing_everywhere_decodes_nothing_and_degrades_every_replay(
    monkeypatch, counter
):
    world = standard(monkeypatch, file=False)
    write_history(cache_path(bot_state(), FILE_DATE), history_of())
    world.save_state(bot_state(without_holdings=True))
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 0, "symphonies carrying no current_holdings")
    body = resp.get_json()
    assert sorted(body["symphonies"]) == symphony_ids()
    assert all_degraded(body)
    assert shadow_of(body) == shadow_of(golden_json("full")), "shadow rows are as at base"


def test_a_missing_file_decodes_nothing_and_matches_the_base_body(monkeypatch, counter):
    world = standard(monkeypatch, file=False)
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 0, "no file anywhere in the window")
    assert resp.data == golden("no_file")
    assert all_degraded(resp.get_json())
    assert shadow_of(resp.get_json()) == shadow_of(golden_json("full"))


def test_a_corrupt_newest_file_is_read_once_and_the_older_file_decoded_once(monkeypatch, counter):
    world = standard(monkeypatch)  # the valid file at FILE_DATE
    corrupt = world.write_corrupt(NEWEST_DATE)
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    reads = counter.read_paths_since(mark)
    assert reads[corrupt] == 1, f"the corrupt file was read {reads[corrupt]} times: {dict(reads)}"
    assert reads[world.files[FILE_DATE]] == 1, f"the valid file: {dict(reads)}"
    assert_decodes(counter, mark, 2, "corrupt newest plus valid older, one request")
    assert replay_of(resp.get_json()) == replay_rows(world.history, symphony_ids())


def test_a_corrupt_only_file_is_read_once_and_every_replay_degrades(monkeypatch, counter):
    world = standard(monkeypatch, file=False)
    corrupt = world.write_corrupt(FILE_DATE)
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert counter.reads_of(corrupt, mark) == 1
    assert_decodes(counter, mark, 1, "the only file in the window is corrupt")
    assert all_degraded(resp.get_json())


def test_a_symphony_no_longer_held_has_no_entry_and_the_file_is_decoded_once(monkeypatch, counter):
    stale = symphony_ids()[3]
    world = standard(monkeypatch, stale=(stale,))
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 1, "12 symphonies, one no longer held")
    body = resp.get_json()
    assert stale not in body["symphonies"]
    assert len(body["symphonies"]) == len(symphony_ids()) - 1


def test_three_polls_after_the_first_decode_nothing(monkeypatch, counter):
    world = standard(monkeypatch)
    served_once(world, counter)
    for poll in range(3):
        world.advance(POLL_SECONDS)
        mark = counter.mark()
        assert world.get().status_code == 200
        assert_decodes(counter, mark, 0, f"poll {poll + 1} over the unchanged file")


def test_two_requests_started_together_decode_the_file_once_in_total(monkeypatch, counter, server):
    world = standard(monkeypatch)
    mark = counter.mark()
    requests = run_together(server, [ROUTE, ROUTE])
    assert_decodes(counter, mark, 1, "two overlapping requests")
    assert [r.fetched.status for r in requests] == [200, 200]
    assert counter.reads_of(world.files[FILE_DATE]) == 1


# ---------------------------------------------------------------------------
# AC-2: an unchanged file is reused, a changed one never
# ---------------------------------------------------------------------------


def test_an_engine_cycle_that_keeps_the_holdings_does_not_cause_a_decode(monkeypatch, counter):
    world = standard(monkeypatch)
    first = served_once(world, counter)
    untouched_by_engine_cycle(world)
    mark = counter.mark()
    second = world.get()
    assert_decodes(counter, mark, 0, "state rewritten without a holdings change")
    assert second.data == first.data


def test_three_polls_sixty_seconds_apart_are_byte_identical_and_decode_nothing(
    monkeypatch, counter
):
    world = standard(monkeypatch)
    first = served_once(world, counter)
    for poll in range(3):
        world.advance(POLL_SECONDS)
        mark = counter.mark()
        resp = world.get()
        assert_decodes(counter, mark, 0, f"poll {poll + 1}")
        assert resp.data == first.data, f"poll {poll + 1} changed the body"


def test_a_date_roll_inside_the_window_reuses_the_file(monkeypatch, counter):
    world = standard(monkeypatch)
    first = served_once(world, counter)
    world.set_clock(datetime(2026, 11, 16, 20, 0, tzinfo=ET))
    assert probe_offset(FILE_DATE, date(2026, 11, 16)) == 1
    mark = counter.mark()
    resp = world.get()
    assert_decodes(counter, mark, 0, "the next trading day, file still in the window")
    assert replay_of(resp.get_json()) == replay_of(first.get_json())


def test_a_file_exactly_at_the_edge_of_the_window_is_still_reused(monkeypatch, counter):
    world = standard(monkeypatch)
    first = served_once(world, counter)
    edge = nth_trading_day_after(FILE_DATE, WINDOW)
    assert probe_offset(FILE_DATE, edge.date()) == WINDOW, "the clock must put the file at the edge"
    world.set_clock(edge)
    mark = counter.mark()
    resp = world.get()
    assert_decodes(counter, mark, 0, "file exactly WINDOW trading days back")
    assert replay_of(resp.get_json()) == replay_of(first.get_json())
    assert not all_degraded(resp.get_json())


def test_a_file_one_session_beyond_the_window_is_not_used_and_one_miss_line_is_logged(
    monkeypatch, counter, caplog
):
    caplog.set_level(logging.INFO)
    world = standard(monkeypatch)
    served_once(world, counter)
    beyond = nth_trading_day_after(FILE_DATE, WINDOW + 1)
    assert probe_offset(FILE_DATE, beyond.date()) is None
    world.set_clock(beyond)
    caplog.clear()
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 0, "file beyond the window")
    assert all_degraded(resp.get_json())
    lines = misses(caplog)
    assert len(lines) == 1, f"{len(lines)} miss summaries for one request, expected 1"
    assert f"cache miss for {WINDOW + 1} candidate date(s)" in lines[0].getMessage()


def test_a_newer_file_is_decoded_once_and_its_rows_replace_the_old_ones(monkeypatch, counter):
    world = standard(monkeypatch)
    first = served_once(world, counter)
    newer_history = with_one_return_changed(world.history)
    newer = world.write_file(NEWEST_DATE, newer_history)
    mark = counter.mark()
    resp = world.get()
    assert counter.reads_of(newer, mark) == 1
    assert_decodes(counter, mark, 1, "a newer file for the same holdings")
    rows = replay_of(resp.get_json())
    assert rows == replay_rows(newer_history, symphony_ids())
    assert rows != replay_of(first.get_json()), "the series of the old file was served"


def test_a_file_rewritten_with_a_different_size_is_decoded_again(monkeypatch, counter):
    world = standard(monkeypatch)
    first = served_once(world, counter)
    path = world.files[FILE_DATE]
    original_mtime = os.stat(path).st_mtime_ns
    trimmed = with_dates_trimmed(world.history)
    world.rewrite_in_place(FILE_DATE, trimmed, keep_mtime_bump=False)
    os.utime(path, ns=(original_mtime, original_mtime))  # only the size differs now
    mark = counter.mark()
    resp = world.get()
    assert_decodes(counter, mark, 1, "same path and mtime, new size")
    rows = replay_of(resp.get_json())
    assert rows == replay_rows(trimmed, symphony_ids())
    assert rows != replay_of(first.get_json())


def test_a_file_rewritten_with_the_same_size_and_a_new_mtime_is_decoded_again(monkeypatch, counter):
    world = standard(monkeypatch)
    first = served_once(world, counter)
    path = world.files[FILE_DATE]
    size = os.path.getsize(path)
    edited = with_same_length_return_change(world.history)
    world.rewrite_in_place(FILE_DATE, edited)  # bumps the mtime by 2 s
    assert os.path.getsize(path) == size, "the rewrite must keep the size"
    mark = counter.mark()
    resp = world.get()
    assert_decodes(counter, mark, 1, "same path and size, new mtime")
    rows = replay_of(resp.get_json())
    assert rows == replay_rows(edited, symphony_ids())
    assert rows != replay_of(first.get_json())


def test_changed_holdings_resolve_no_file_and_degrade_every_replay(monkeypatch, counter):
    world = standard(monkeypatch)
    served_once(world, counter)
    state = world.loaded_state()
    state[symphony_ids()[0]]["current_holdings"][0]["allocation"] += 0.001
    world.save_state(state)
    mark = counter.mark()
    resp = world.get()
    assert_decodes(counter, mark, 0, "holdings drifted, so the key names no file")
    assert all_degraded(resp.get_json())


def test_a_deleted_file_is_not_used_and_nothing_is_fetched(monkeypatch, counter, caplog):
    caplog.set_level(logging.INFO)
    spy = NetworkSpy()
    world = standard(monkeypatch)
    served_once(world, counter)
    spy.install(monkeypatch)
    world.delete_file(FILE_DATE)
    caplog.clear()
    mark = counter.mark()
    resp = world.get()
    assert resp.status_code == 200
    assert_decodes(counter, mark, 0, "the held file was deleted")
    assert all_degraded(resp.get_json())
    assert len(misses(caplog)) == 1
    assert spy.calls == [], f"a replay read reached the network: {spy.calls}"


def test_a_file_replaced_by_corrupt_content_logs_one_warning_and_falls_back_to_the_older_file(
    monkeypatch, counter, caplog
):
    world = standard(monkeypatch)
    older_history = with_one_return_changed(world.history)
    older = world.write_file(OLDER_DATE, older_history)
    served_once(world, counter)  # decodes the newer valid file only
    path = world.files[FILE_DATE]
    write_truncated(path, world.history)
    bump_mtime(path)
    caplog.clear()
    mark = counter.mark()
    resp = world.get()
    warnings = corrupt_warnings(caplog)
    assert len(warnings) == 1, f"{len(warnings)} corrupt-file warnings, expected 1"
    assert os.path.basename(warnings[0].args[0]) == os.path.basename(path)
    assert counter.reads_of(path, mark) == 1
    assert counter.reads_of(older, mark) == 1
    assert_decodes(counter, mark, 2, "corrupt replacement plus the older file")
    assert replay_of(resp.get_json()) == replay_rows(older_history, symphony_ids())


def test_a_file_the_autotune_is_part_way_through_writing_is_read_once_then_decoded_when_complete(
    monkeypatch, counter, caplog
):
    world = standard(monkeypatch)
    finished_history = with_one_return_changed(world.history)
    partial = world.write_corrupt(NEWEST_DATE, finished_history)
    mark = counter.mark()
    resp = world.get()
    warnings = corrupt_warnings(caplog)
    assert len(warnings) == 1, f"{len(warnings)} corrupt-file warnings for the partial file"
    assert os.path.basename(warnings[0].args[0]) == os.path.basename(partial)
    assert counter.reads_of(partial, mark) == 1
    assert counter.reads_of(world.files[FILE_DATE], mark) == 1
    assert_decodes(counter, mark, 2, "partial newest file plus the older complete one")
    assert replay_of(resp.get_json()) == replay_rows(world.history, symphony_ids())
    # The write completes: more bytes, a later modification time.
    write_history(partial, finished_history)
    bump_mtime(partial)
    mark = counter.mark()
    resp = world.get()
    assert counter.reads_of(partial, mark) == 1
    assert_decodes(counter, mark, 1, "the completed file")
    assert replay_of(resp.get_json()) == replay_rows(finished_history, symphony_ids())


def test_a_symphony_that_becomes_no_longer_held_between_polls_drops_out_without_a_decode(
    monkeypatch, counter
):
    gone = symphony_ids()[0]
    world = standard(monkeypatch)
    first = served_once(world, counter).get_json()
    delete_recent_shadow(gone, "2026-11-11")
    world.advance(POLL_SECONDS)
    mark = counter.mark()
    resp = world.get()
    assert_decodes(counter, mark, 0, "a symphony went stale between polls")
    body = resp.get_json()
    assert gone not in body["symphonies"]
    for sid, entry in body["symphonies"].items():
        assert entry == first["symphonies"][sid], f"{sid} changed between the polls"
    assert len(body["symphonies"]) == len(first["symphonies"]) - 1


def test_a_restarted_daemon_decodes_the_file_exactly_once(monkeypatch, counter):
    world = standard(monkeypatch)
    served_once(world, counter)
    restart_daemon()
    mark = counter.mark()
    assert world.get().status_code == 200
    assert_decodes(counter, mark, 1, "the first request after a restart")


def test_two_requests_started_together_over_a_warm_file_decode_nothing(
    monkeypatch, counter, server
):
    world = standard(monkeypatch)
    first = served_once(world, counter)
    mark = counter.mark()
    requests = run_together(server, [ROUTE, ROUTE])
    assert_decodes(counter, mark, 0, "two overlapping requests over a warm file")
    for request in requests:
        assert request.fetched.status == 200
        assert request.fetched.data == first.data


def test_returning_to_an_earlier_file_after_another_was_decoded_is_a_cold_request(
    monkeypatch, counter
):
    world = standard(monkeypatch)
    first_state = world.loaded_state()
    first_path = world.files[FILE_DATE]
    served_once(world, counter)
    sid = symphony_ids()[0]
    halved = [
        {**h, "allocation": h["allocation"] / 2} for h in first_state[sid]["current_holdings"]
    ]
    second_state = bot_state(holdings={sid: halved})
    second_history = with_one_return_changed(world.history)
    second_path = world.write_file(FILE_DATE, second_history, state=second_state)
    assert second_path != first_path, "the second holdings state must resolve its own file"
    world.save_state(second_state)
    mark = counter.mark()
    resp = world.get()
    assert counter.reads_of(second_path, mark) == 1
    assert_decodes(counter, mark, 1, "holdings changed to a state with its own file")
    assert replay_of(resp.get_json()) == replay_rows(second_history, symphony_ids())
    world.save_state(first_state)
    mark = counter.mark()
    resp = world.get()
    assert counter.reads_of(first_path, mark) == 1
    assert_decodes(counter, mark, 1, "back to the first file: only one identity is held")
    assert replay_of(resp.get_json()) == replay_rows(history_of(), symphony_ids())


def test_a_newer_file_that_is_deleted_again_sends_the_probe_back_to_a_cold_older_file(
    monkeypatch, counter
):
    world = standard(monkeypatch)
    served_once(world, counter)
    newer = world.write_file(NEWEST_DATE, with_one_return_changed(world.history))
    mark = counter.mark()
    assert world.get().status_code == 200
    assert counter.reads_of(newer, mark) == 1
    assert_decodes(counter, mark, 1, "the newer file replaces the held one")
    world.delete_file(NEWEST_DATE)
    mark = counter.mark()
    resp = world.get()
    assert counter.reads_of(world.files[FILE_DATE], mark) == 1
    assert_decodes(counter, mark, 1, "the older file again: one identity is held")
    assert replay_of(resp.get_json()) == replay_rows(world.history, symphony_ids())


def test_a_corrupt_file_is_read_and_warned_about_once_while_it_stays_unchanged(
    monkeypatch, counter, caplog
):
    world = standard(monkeypatch, file=False)
    older = world.write_file(OLDER_DATE)
    corrupt = world.write_corrupt(FILE_DATE)
    mark = counter.mark()
    first = world.get()
    assert counter.reads_of(corrupt, mark) == 1
    assert counter.reads_of(older, mark) == 1
    assert_decodes(counter, mark, 2, "corrupt newest plus valid older, first request")
    assert len(corrupt_warnings(caplog)) == 1
    caplog.clear()
    for poll in range(4):
        world.advance(POLL_SECONDS)
        mark = counter.mark()
        resp = world.get()
        assert_decodes(counter, mark, 0, f"poll {poll + 1} with both files unchanged")
        assert corrupt_warnings(caplog) == [], f"poll {poll + 1} repeated the warning"
        assert resp.data == first.data, f"poll {poll + 1} changed the body"
    assert replay_of(first.get_json()) == replay_rows(world.history, symphony_ids())


def test_a_corrupt_file_rewritten_valid_is_decoded_and_its_rows_win(monkeypatch, counter):
    world = standard(monkeypatch, file=False)
    older_history = with_one_return_changed(world.history)
    world.write_file(OLDER_DATE, older_history)
    corrupt = world.write_corrupt(FILE_DATE)
    assert world.get().status_code == 200
    world.rewrite_in_place(FILE_DATE, world.history)
    mark = counter.mark()
    resp = world.get()
    assert counter.reads_of(corrupt, mark) == 1
    assert_decodes(counter, mark, 1, "the corrupt file was repaired in place")
    rows = replay_of(resp.get_json())
    assert rows == replay_rows(world.history, symphony_ids())
    assert rows != replay_rows(older_history, symphony_ids())


def test_an_unchanged_corrupt_only_file_is_not_read_again_and_degrades_as_at_base(
    monkeypatch, counter, caplog
):
    world = standard(monkeypatch, file=False)
    corrupt = world.write_corrupt(FILE_DATE)
    mark = counter.mark()
    first = world.get()
    assert counter.reads_of(corrupt, mark) == 1
    assert len(corrupt_warnings(caplog)) == 1
    assert first.data == golden("only_corrupt")
    caplog.clear()
    mark = counter.mark()
    second = world.get()
    assert_decodes(counter, mark, 0, "the unchanged corrupt file")
    assert corrupt_warnings(caplog) == []
    assert second.data == golden("only_corrupt")
    assert all_degraded(second.get_json())


# ---------------------------------------------------------------------------
# AC-8: the probe's log lines and the cache-hit-only contract
# ---------------------------------------------------------------------------

MISS_PATTERN = re.compile(
    r"synthetic_history: cache miss for 11 candidate date\(s\) \(2026-11-15 \.\. \d{4}-\d{2}-\d{2}\)"
    r" while walking back from 2026-11-15 -- expected under the weekly cache cadence,"
    r" not an anomaly"
)


def test_a_cold_cache_never_fetches_and_never_reaches_the_network(monkeypatch, counter):
    spy = NetworkSpy()
    world = standard(monkeypatch, file=False)
    spy.install(monkeypatch)
    resp = world.get()
    assert resp.status_code == 200
    assert spy.calls == [], f"a replay read reached the network: {spy.calls}"
    assert all_degraded(resp.get_json())


def test_all_eleven_candidates_missing_logs_one_miss_line_in_the_existing_wording(
    monkeypatch, caplog
):
    caplog.set_level(logging.INFO)
    world = standard(monkeypatch, file=False)
    resp = world.get()
    assert resp.status_code == 200
    lines = misses(caplog)
    assert len(lines) == 1, f"{len(lines)} miss summaries for one request, expected 1"
    assert MISS_PATTERN.fullmatch(lines[0].getMessage()), lines[0].getMessage()
    assert corrupt_warnings(caplog) == []


def test_the_first_corrupt_warning_keeps_base_wording_and_the_walk_continues_to_older_files(
    monkeypatch, counter, caplog
):
    world = standard(monkeypatch, file=False)
    world.write_file(OLDER_DATE)
    corrupt = world.write_corrupt(FILE_DATE)
    first = world.get()
    warnings = corrupt_warnings(caplog)
    assert len(warnings) == 1, f"{len(warnings)} warnings on the first request"
    assert os.path.basename(warnings[0].args[0]) == os.path.basename(corrupt)
    assert replay_of(first.get_json()) == replay_rows(world.history, symphony_ids())
    caplog.clear()
    for _ in range(2):
        world.advance(POLL_SECONDS)
        assert world.get().status_code == 200
    assert corrupt_warnings(caplog) == [], "a poll logged a repeat warning for an unchanged file"


def test_a_hit_on_the_current_date_logs_nothing_from_the_probe(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    world = standard(monkeypatch, file=False)
    world.write_file(NEWEST_DATE)
    caplog.clear()
    resp = world.get()
    assert resp.status_code == 200
    assert not all_degraded(resp.get_json())
    probe_lines = [r for r in caplog.records if r.getMessage().startswith("synthetic_history:")]
    assert probe_lines == [], [r.getMessage() for r in probe_lines]


def test_two_overlapping_requests_over_missing_files_log_one_miss_line_each(
    monkeypatch, caplog, server
):
    caplog.set_level(logging.INFO)
    standard(monkeypatch, file=False)
    caplog.clear()
    run_together(server, [ROUTE, ROUTE])
    lines = misses(caplog)
    assert len(lines) == 2, f"{len(lines)} miss summaries for two requests, expected 2"
    per_thread = Counter(r.threadName for r in lines)
    assert max(per_thread.values()) == 1, f"a request logged more than one line: {per_thread}"
    assert corrupt_warnings(caplog) == []


def test_a_file_deleted_after_a_decode_makes_the_next_poll_degrade_without_fetching(
    monkeypatch, counter
):
    spy = NetworkSpy()
    world = standard(monkeypatch)
    served_once(world, counter)
    spy.install(monkeypatch)
    world.delete_file(FILE_DATE)
    world.advance(POLL_SECONDS)
    resp = world.get()
    assert resp.status_code == 200
    assert spy.calls == [], f"the poll reached the network: {spy.calls}"
    assert all_degraded(resp.get_json())


def test_two_overlapping_requests_over_missing_files_never_fetch(monkeypatch, server):
    spy = NetworkSpy()
    standard(monkeypatch, file=False)
    spy.install(monkeypatch)
    requests = run_together(server, [ROUTE, ROUTE])
    assert spy.calls == [], f"an overlapping request reached the network: {spy.calls}"
    assert [r.fetched.status for r in requests] == [200, 200]


def test_a_date_roll_past_the_window_logs_exactly_one_miss_line_and_no_warning(
    monkeypatch, counter, caplog
):
    caplog.set_level(logging.INFO)
    world = standard(monkeypatch)
    served_once(world, counter)
    world.set_clock(nth_trading_day_after(FILE_DATE, WINDOW + 1))
    caplog.clear()
    resp = world.get()
    assert resp.status_code == 200
    lines = misses(caplog)
    assert len(lines) == 1, f"{len(lines)} miss summaries for one request, expected 1"
    assert f"cache miss for {WINDOW + 1} candidate date(s)" in lines[0].getMessage()
    assert corrupt_warnings(caplog) == []


def test_the_seeded_world_is_internally_consistent(monkeypatch):
    """The fixture's own claims, so a drifted helper fails here and not as a vague route diff."""
    world = standard(monkeypatch)
    assert len(symphony_ids()) == 12
    assert cache_path(bot_state(), FILE_DATE) == cache_path(world.loaded_state(), FILE_DATE)
    captured_hash = excerpt()["provenance"]["bot_state_current_holdings_md5"]
    assert os.path.basename(cache_path(bot_state(), FILE_DATE)) == (
        f"synthetic_history_v4_{FILE_DATE}_{captured_hash}.json"
    ), "the excerpt's holdings must hash to the captured file name"
