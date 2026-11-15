"""The preconditions route keeps no decoded cache content between requests.

Between requests the daemon may hold only the held file identity's per-symphony return lists and
the identities (path, size, modification time) of corrupt files, never a parsed history and never
a failure carrying the file's text. ``tracemalloc`` measures it: the traced memory retained after a
request completes and garbage is collected, against the traced peak of one cold request, on a
production-shaped file (12 symphonies, 250 dates, 10 ticks per date, written at test time from the
excerpt's real tick records; a 71 MB file would slow ``tracemalloc`` far too much). A warm-up
request on a state with no symphony runs first, so lazy imports fall outside the window.
"""

from __future__ import annotations

import gc
import logging
import os
import time
import tracemalloc

import pytest

from tests.app._preconditions_replay_world import (
    FILE_DATE,
    REPO,
    SETTLE_SECONDS,
    Request,
    assert_decodes,
    bot_state,
    chdir_to_tmp,  # noqa: F401  (fixture)
    fetch,
    generate_file,
    install_counter,
    join_all,
    link_or_copy,
    restart_daemon,
    server,  # noqa: F401  (fixture)
    standard,
    truncate_copy,
    watch_arrivals,
)

pytestmark = pytest.mark.usefixtures("chdir_to_tmp")

RETAINED_SHARE = 0.05  # of the traced peak of one cold request
PAIR_PEAK_RATIO = 1.25
TICK_COUNTS = (10, 11, 12)  # three files of different sizes


@pytest.fixture(scope="module")
def shaped_files(tmp_path_factory):
    """Three production-shaped files of different sizes (so each has its own identity)."""
    root = tmp_path_factory.mktemp("shaped")
    paths = []
    for ticks in TICK_COUNTS:
        path = root / f"history_{ticks}.json"
        generate_file(path, ticks_per_date=ticks)
        paths.append(path)
    yield paths
    for path in paths:
        path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def truncated_shaped_file(shaped_files, tmp_path_factory):
    path = tmp_path_factory.mktemp("shaped_truncated") / "history.json"
    truncate_copy(shaped_files[0], path)
    yield path
    path.unlink(missing_ok=True)


def traced(call) -> tuple[int, int]:
    """(bytes still allocated after ``call`` and a collection, the peak above the start)."""
    gc.collect()
    tracemalloc.start()
    try:
        before, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        call()
        gc.collect()
        after, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return after - before, peak - before


def warmed_world(monkeypatch, source):
    """A world over ``source`` after a warm-up request on a state with no symphony."""
    world = standard(monkeypatch, file=False)
    full_state = world.loaded_state()
    world.save_state(bot_state(ids=[], metadata=True))
    assert world.get().status_code == 200
    world.save_state(full_state)
    world.place_file(FILE_DATE, source)
    restart_daemon()
    return world


def request_ok(world) -> None:
    assert world.get().status_code == 200


def cold_reference(world) -> tuple[int, int]:
    """(the traced peak, the retained bytes) of one cold request over the world's file."""
    restart_daemon()
    retained, peak = traced(lambda: request_ok(world))
    assert peak > 0
    return peak, retained


def test_a_cold_request_retains_under_five_percent_of_its_peak(monkeypatch, shaped_files):
    world = warmed_world(monkeypatch, shaped_files[0])
    retained, peak = traced(lambda: request_ok(world))
    assert retained < RETAINED_SHARE * peak, (
        f"{retained} bytes still traced after the request, against a request peak of {peak}"
    )


def test_a_third_file_decoded_in_turn_still_retains_under_five_percent(monkeypatch, shaped_files):
    world = warmed_world(monkeypatch, shaped_files[0])

    def three_requests() -> None:
        for source in shaped_files:
            world.place_file(FILE_DATE, source)
            request_ok(world)

    retained, peak = traced(three_requests)
    assert retained < RETAINED_SHARE * peak, (
        f"{retained} bytes retained after three files, against one request's peak {peak}"
    )


def test_two_cold_requests_together_peak_at_most_a_quarter_above_one(
    monkeypatch, shaped_files, server
):
    """The pair provably overlaps: the first request's decode is finished and held (its parsed
    history alive) until the second request has reached the same file. A second request that
    decodes for itself then holds a second parsed copy at the same time."""
    warmed_world(monkeypatch, shaped_files[0])
    counter = install_counter(monkeypatch)
    arrivals = watch_arrivals(monkeypatch)
    _, single_peak = traced(lambda: fetch(server))

    restart_daemon()
    mark = counter.mark()
    gate = counter.hold_first_decode(after_read=True)

    def overlapping_pair() -> None:
        first = Request(server)
        first.start()
        assert gate.entered.wait(30), "the first request never finished its decode"
        arrivals.reset()
        second = Request(server)
        second.start()
        assert arrivals.wait_for_threads_besides(gate.owner, 1, timeout=30), (
            "the second request never reached the cache file"
        )
        time.sleep(SETTLE_SECONDS)
        gate.release.set()
        join_all([first, second])

    _, pair_peak = traced(overlapping_pair)
    assert not gate.timed_out
    decodes = (counter.reads_since(mark), counter.loader_since(mark))
    assert decodes == (1, 1) and pair_peak <= PAIR_PEAK_RATIO * single_peak, (
        f"two overlapping cold requests: (json.load, loader) decodes {decodes}, expected one; "
        f"traced peak {pair_peak} against {single_peak} for one alone "
        f"(ratio {pair_peak / single_peak:.2f}, allowed {PAIR_PEAK_RATIO})"
    )


def test_a_request_with_no_file_retains_nothing_that_matters(monkeypatch, shaped_files):
    world = warmed_world(monkeypatch, shaped_files[0])
    peak, _ = cold_reference(world)
    world.delete_file(FILE_DATE)
    retained, _ = traced(lambda: request_ok(world))
    assert retained < RETAINED_SHARE * peak, f"{retained} bytes retained by a request with no file"


def test_a_failed_decode_retains_nothing_beyond_the_file_identity(
    monkeypatch, shaped_files, truncated_shaped_file
):
    world = warmed_world(monkeypatch, shaped_files[0])
    peak, _ = cold_reference(world)
    world.place_file(FILE_DATE, truncated_shaped_file)
    # The suite's log capture keeps every record, and a corrupt-file record carries the decode
    # error, whose text is the file's: that retention is the harness's, never the route's.
    logging.disable(logging.CRITICAL)
    try:
        retained, _ = traced(lambda: request_ok(world))
    finally:
        logging.disable(logging.NOTSET)
    assert retained < RETAINED_SHARE * peak, (
        f"{retained} bytes retained after a corrupt file's decode failed (a request peak is {peak})"
    )


def test_twenty_warm_requests_do_not_grow_the_retained_memory(monkeypatch, shaped_files):
    world = warmed_world(monkeypatch, shaped_files[0])
    peak, _ = cold_reference(world)

    def twenty() -> None:
        for _ in range(20):
            request_ok(world)

    retained, _ = traced(twenty)
    assert retained < RETAINED_SHARE * peak, f"{retained} bytes gained across 20 warm requests"


def test_a_request_writes_no_file_anywhere_in_the_cache_directory_or_the_repository(
    monkeypatch, shaped_files, truncated_shaped_file, chdir_to_tmp
):
    database_files = (".db", "-wal", "-shm", "-journal")  # SQLite's own, opened by any read

    def listing() -> tuple[list, list, list]:
        walked = sorted(
            os.path.join(root, name)
            for root, _dirs, names in os.walk(chdir_to_tmp)
            for name in names
            if not name.endswith(database_files)
        )
        return sorted(os.listdir("cache")), walked, sorted(os.listdir(REPO))

    world = standard(monkeypatch)  # the excerpt state
    states = [
        lambda: None,
        lambda: link_or_copy(shaped_files[0], world.files[FILE_DATE]),
        lambda: link_or_copy(truncated_shaped_file, world.files[FILE_DATE]),
    ]
    for arrange in states:
        arrange()
        before = listing()
        request_ok(world)
        assert listing() == before, "a request created or removed a file"
