"""Measured latency of the preconditions route on a production-sized cache file, and what a
concurrent request pays while it runs.

The file is generated at test time from the excerpt's real tick records (12 symphonies, 250 dates,
the recorded shape; 71 MB, and 147 MB for one test), written once per module and deleted at
teardown, never committed. Every test asserts decode counts first (deterministic), then relations
between wall-clock times measured in the same run: a warm request under half a second, a cold
request within one decode of the file (timed alone here) plus one second, and a concurrent
``/api/performance``, ``/api/header-status`` or ``/api/candidate-alert`` within its alone time
plus half a second (plus the decode time a cold request legitimately spends). The server is the
real threaded one, so the decode's hold on the interpreter lock is what is measured.

The measured numbers are written to ``timings-<worker>.json`` under pytest's temporary directory,
or under ``PRL_TIMING_DIR`` when that names a directory; nothing is written into the repository.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.app._preconditions_replay_world import (
    FILE_DATE,
    MEGABYTE,
    OLDER_DATE,
    ROUTE,
    assert_decodes,
    chdir_to_tmp,  # noqa: F401  (fixture)
    fetch,
    generate_file,
    install_counter,
    median_of,
    restart_daemon,
    run_together,
    server,  # noqa: F401  (fixture)
    standard,
    time_decode,
    truncate_copy,
)

pytestmark = pytest.mark.usefixtures("chdir_to_tmp")

WARM_BOUND_SECONDS = 0.5
COLD_MARGIN_SECONDS = 1.0
HELD_UP_MARGIN_SECONDS = 0.5
PERFORMANCE = "/api/performance?scope=aggregate&days=60d"
HEADER_STATUS = "/api/header-status"
CANDIDATE_ALERT = "/api/candidate-alert"
OTHER_ROUTES = pytest.mark.parametrize(
    "other", [PERFORMANCE, HEADER_STATUS, CANDIDATE_ALERT], ids=["performance", "header", "alert"]
)

NUMBERS: dict[str, dict] = {}


def record(name: str, **numbers) -> None:
    NUMBERS[name] = {key: round(value, 4) for key, value in numbers.items()}


@pytest.fixture(scope="module", autouse=True)
def _write_recorded_numbers(tmp_path_factory):
    """The measured numbers go under pytest's temporary directory, so a run leaves nothing in
    the repository; ``PRL_TIMING_DIR`` names a directory when a reviewer wants them kept."""
    yield
    target = Path(os.environ.get("PRL_TIMING_DIR") or tmp_path_factory.mktemp("timings"))
    target.mkdir(parents=True, exist_ok=True)
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    (target / f"timings-{worker}.json").write_text(json.dumps(NUMBERS, indent=1), encoding="utf-8")


@pytest.fixture(scope="module")
def production_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("production_sized") / "history.json"
    size = generate_file(path, target_bytes=71 * MEGABYTE)
    NUMBERS["fixture"] = {"bytes_71mb_file": size}
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def truncated_production_file(production_file, tmp_path_factory):
    path = tmp_path_factory.mktemp("truncated") / "history.json"
    truncate_copy(production_file, path)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture(scope="module")
def largest_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("largest") / "history.json"
    size = generate_file(path, target_bytes=147 * MEGABYTE)
    NUMBERS["fixture"] = {**NUMBERS.get("fixture", {}), "bytes_147mb_file": size}
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def counter(monkeypatch):
    return install_counter(monkeypatch)


def sized_world(monkeypatch, source):
    world = standard(monkeypatch, file=False)
    world.place_file(FILE_DATE, source)
    return world


def corrupt_newest_world(monkeypatch, production_file, truncated):
    world = standard(monkeypatch, file=False)
    world.place_file(OLDER_DATE, production_file)
    world.place_file(FILE_DATE, truncated)
    return world


def warm_up(server) -> None:
    """The first request over a cold daemon. How many decodes it costs is asserted where a test
    is about a cold request; here it only has to succeed, so a warm test fails on its warm
    assertion."""
    assert fetch(server).status == 200


def warm_times(server, counter, count: int) -> list[float]:
    """``count`` requests back to back; each is checked for zero decodes before its time counts."""
    times = []
    for index in range(count):
        mark = counter.mark()
        resp = fetch(server)
        assert resp.status == 200
        assert_decodes(counter, mark, 0, f"warm request {index + 1}")
        times.append(resp.seconds)
    return times


def other_alone_median(server, other: str, count: int = 5) -> float:
    return median_of(count, lambda: _ok(fetch(server, other)).seconds)


def _ok(fetched):
    assert fetched.status == 200, f"answered {fetched.status}"
    return fetched


def other_during(server, counter, other: str, *, expected_reads: int) -> float:
    """How long ``other`` takes while a preconditions request runs beside it. The preconditions
    request's decode count is checked first."""
    mark = counter.mark()
    pre, concurrent = run_together(server, [ROUTE, other])
    assert pre.fetched.status == 200 and concurrent.fetched.status == 200
    assert_decodes(counter, mark, expected_reads, "the concurrent preconditions request")
    return concurrent.fetched.seconds


# ---------------------------------------------------------------------------
# The route's own latency
# ---------------------------------------------------------------------------


def test_a_warm_request_decodes_nothing_and_takes_under_half_a_second(
    monkeypatch, counter, server, production_file
):
    sized_world(monkeypatch, production_file)
    warm_up(server)
    times = warm_times(server, counter, 5)
    median = sorted(times)[len(times) // 2]
    record("warm_71mb", median=median, worst=max(times))
    assert median < WARM_BOUND_SECONDS, f"median warm request {median:.3f} s over 71 MB"


def test_a_cold_request_decodes_once_and_stays_within_one_decode_plus_a_second(
    monkeypatch, counter, server, production_file
):
    sized_world(monkeypatch, production_file)
    decode_alone = time_decode(production_file)
    mark = counter.mark()
    cold = fetch(server)
    assert cold.status == 200
    assert_decodes(counter, mark, 1, "a cold request over 12 symphonies")
    record("cold_71mb", cold=cold.seconds, one_decode_alone=decode_alone)
    assert cold.seconds <= decode_alone + COLD_MARGIN_SECONDS, (
        f"cold {cold.seconds:.2f} s against one decode {decode_alone:.2f} s plus "
        f"{COLD_MARGIN_SECONDS} s"
    )


def test_a_warm_request_over_the_largest_recent_file_takes_under_half_a_second(
    monkeypatch, counter, server, largest_file
):
    sized_world(monkeypatch, largest_file)
    warm_up(server)
    (warm,) = warm_times(server, counter, 1)
    record("warm_147mb", warm=warm)
    assert warm < WARM_BOUND_SECONDS, f"warm request {warm:.3f} s over 147 MB"


def test_a_request_with_no_file_in_the_window_decodes_nothing_and_is_fast(
    monkeypatch, counter, server
):
    standard(monkeypatch, file=False)
    (elapsed,) = warm_times(server, counter, 1)
    record("no_file", elapsed=elapsed)
    assert elapsed < WARM_BOUND_SECONDS, f"{elapsed:.3f} s with no file anywhere"


def test_five_warm_requests_in_a_row_each_stay_under_half_a_second(
    monkeypatch, counter, server, production_file
):
    sized_world(monkeypatch, production_file)
    warm_up(server)
    times = warm_times(server, counter, 5)
    record("warm_x5", **{f"request_{i + 1}": t for i, t in enumerate(times)})
    assert all(t < WARM_BOUND_SECONDS for t in times), f"warm times {times}"


def test_a_corrupt_newest_file_costs_one_read_and_one_decode_not_one_per_symphony(
    monkeypatch, counter, server, production_file, truncated_production_file
):
    world = corrupt_newest_world(monkeypatch, production_file, truncated_production_file)
    read_alone = time_decode(truncated_production_file)
    decode_alone = time_decode(production_file)
    mark = counter.mark()
    resp = fetch(server)
    assert resp.status == 200
    assert counter.reads_of(world.files[FILE_DATE], mark) == 1
    assert counter.reads_of(world.files[OLDER_DATE], mark) == 1
    assert_decodes(counter, mark, 2, "corrupt newest plus valid older")
    record(
        "cold_corrupt_newest",
        cold=resp.seconds,
        read_f_alone=read_alone,
        decode_g_alone=decode_alone,
    )
    assert resp.seconds <= read_alone + decode_alone + COLD_MARGIN_SECONDS


def test_once_a_corrupt_file_is_remembered_the_next_request_reads_nothing(
    monkeypatch, counter, server, production_file, truncated_production_file
):
    corrupt_newest_world(monkeypatch, production_file, truncated_production_file)
    warm_up(server)
    (elapsed,) = warm_times(server, counter, 1)
    record("warm_after_corrupt", elapsed=elapsed)
    assert elapsed < WARM_BOUND_SECONDS


# ---------------------------------------------------------------------------
# Other requests are not held up
# ---------------------------------------------------------------------------


@OTHER_ROUTES
def test_a_concurrent_request_is_not_held_up_by_a_warm_request(
    monkeypatch, counter, server, production_file, other
):
    sized_world(monkeypatch, production_file)
    warm_up(server)
    alone = other_alone_median(server, other)
    during = median_of(5, lambda: other_during(server, counter, other, expected_reads=0))
    record(f"warm_beside{other.split('?')[0]}", alone=alone, during=during)
    assert during <= alone + HELD_UP_MARGIN_SECONDS, (
        f"{other} took {during:.3f} s beside a warm request, {alone:.3f} s alone"
    )


@OTHER_ROUTES
def test_a_concurrent_request_pays_at_most_one_decode_beside_a_cold_request(
    monkeypatch, counter, server, production_file, other
):
    sized_world(monkeypatch, production_file)
    alone = other_alone_median(server, other, count=3)
    decode_alone = time_decode(production_file)

    def cold_run() -> float:
        restart_daemon()
        return other_during(server, counter, other, expected_reads=1)

    during = median_of(3, cold_run)
    record(
        f"cold_beside{other.split('?')[0]}", alone=alone, during=during, decode_alone=decode_alone
    )
    assert during <= alone + decode_alone + HELD_UP_MARGIN_SECONDS, (
        f"{other} took {during:.3f} s beside a cold request; alone {alone:.3f} s plus one "
        f"decode {decode_alone:.3f} s"
    )


def test_performance_is_not_held_up_when_no_file_is_in_the_window(monkeypatch, counter, server):
    standard(monkeypatch, file=False)
    alone = other_alone_median(server, PERFORMANCE)
    during = median_of(5, lambda: other_during(server, counter, PERFORMANCE, expected_reads=0))
    record("no_file_beside_performance", alone=alone, during=during)
    assert during <= alone + HELD_UP_MARGIN_SECONDS


def test_performance_pays_one_read_and_one_decode_beside_a_cold_corrupt_newest_file(
    monkeypatch, counter, server, production_file, truncated_production_file
):
    corrupt_newest_world(monkeypatch, production_file, truncated_production_file)
    alone = other_alone_median(server, PERFORMANCE, count=3)
    read_alone = time_decode(truncated_production_file)
    decode_alone = time_decode(production_file)

    def cold_run() -> float:
        restart_daemon()
        return other_during(server, counter, PERFORMANCE, expected_reads=2)

    during = median_of(3, cold_run)
    record("cold_corrupt_beside_performance", alone=alone, during=during)
    assert during <= alone + read_alone + decode_alone + HELD_UP_MARGIN_SECONDS


def test_performance_is_not_held_up_once_a_corrupt_file_is_remembered(
    monkeypatch, counter, server, production_file, truncated_production_file
):
    corrupt_newest_world(monkeypatch, production_file, truncated_production_file)
    warm_up(server)
    alone = other_alone_median(server, PERFORMANCE)
    during = median_of(5, lambda: other_during(server, counter, PERFORMANCE, expected_reads=0))
    record("remembered_corrupt_beside_performance", alone=alone, during=during)
    assert during <= alone + HELD_UP_MARGIN_SECONDS
