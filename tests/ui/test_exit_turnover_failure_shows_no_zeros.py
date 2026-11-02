"""A failed Exit Turnover read says so; it is never answered or shown as zero exits.

``database.get_exit_turnover_stats`` answered ``{"exit_count": 0, "coverage_days": 0}`` for every window
on a database error, and the route answered the same zeros (and a 0.0 drag) when the accessor or the
drag arithmetic raised, so the Performance row read ``0 (0d retained)`` and ``0.00%``: a lookup that
failed looked like a symphony that never exited.

Real call path: the real ``GET /api/exit-turnover`` over the captured production records
(tests/ui/_performance_page), with the failure made where it happens: the accessor's database
connection raises (so the accessor's own error path runs and its answer reaches the route), or the
accessor / the drag arithmetic raises inside the route. The page is the real ``static/performance.js``
under node, fed the route's real answer to the failing read.

Contract pinned
* Route, accessor failure: the response states the failure (an error status, or an error body with no
  numeric windows and no drag) and carries no zeroed windows and no 0.0 drag.
* Route, failure inside the route (accessor raises, drag arithmetic raises): the same.
* A real symphony with no exit rows still answers 200 with true zeros.
* Page: the symphony whose read failed has a row whose three window cells and drag cell are dashes (no
  digit); no cell anywhere reads ``0 (0d retained)`` for it; the other symphonies' rows still show the
  numbers their own answers carry.
* Page: a real symphony with no exits still reads ``0 (0d retained)`` and ``0.00%``.
* Page, the note beside the table states only what is on screen: a read the server answered with an
  error is never called a lost connection; a row that shows dashes because it never had a value is
  never described as showing its last values; a read the network never answered still says the
  connection was lost. Every failed state carries a non-empty note.
"""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import ExitStack
from unittest.mock import patch

import pytest

import analytics
import app as app_module
import database
import market_calendar
from tests.ui import _performance_page as perf
from tests.ui._history_answers import NOW_ET, frozen_host_clock
from tests.ui._page_js_harness import answer, route
from tests.ui._shell_support import (  # noqa: F401
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
)

WINDOWS = ("30", "90", "365")
DASH = re.compile(r"^(--|—|–|-|n/a)$", re.I)
ZERO_CELL = re.compile(r"^0 \(0d retained\)$")
ROWS = """
const rows = () => H.$$('#exit-turnover-tbody tr').map((tr) => tr.children.map((td) => td.textContent.trim()));
"""


@pytest.fixture
def world(monkeypatch):
    # analytics reads the shadow table through its own DB_FILE; point it at this test's database.
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])
    # The calendar library cannot build its first schedule under the frozen standard-library clock;
    # build the years the frozen routes ask about before any frozen read, whatever ran before.
    for year in (NOW_ET.year - 1, NOW_ET.year, NOW_ET.year + 1):
        market_calendar._get_schedule_for_year(year)
    return perf.seed_world()


def _read(symphony_id: str, *, patches=()) -> dict:
    """The route's real answer to one read, with `patches` (context managers) active around it."""
    with ExitStack() as stack:
        for entry in patches:
            stack.enter_context(entry)
        stack.enter_context(frozen_host_clock())
        client = stack.enter_context(app_module.app.test_client())
        return answer(client.get(f"/api/exit-turnover?symphony_id={symphony_id}"))


def _accessor_database_fails():
    return patch.object(
        database, "get_connection", side_effect=sqlite3.OperationalError("disk I/O")
    )


def _accessor_raises():
    return patch.object(database, "get_exit_turnover_stats", side_effect=RuntimeError("boom"))


def _drag_raises():
    return patch.object(
        database, "compute_est_annual_friction_drag_pct", side_effect=RuntimeError("boom")
    )


def states_failure(spec: dict) -> bool:
    """The answer says the read failed and presents no number as a reading."""
    if spec["status"] >= 400:
        return True
    body = spec.get("json")
    if not isinstance(body, dict) or not body.get("error"):
        return False
    return body.get("windows") in (None, {}) and body.get("est_annual_friction_drag_pct") is None


def presents_zeros(spec: dict) -> bool:
    body = spec.get("json") or {}
    windows = body.get("windows") or {}
    return any(
        isinstance(w, dict) and w.get("exit_count") == 0 and w.get("coverage_days") == 0
        for w in windows.values()
    )


@pytest.mark.parametrize(
    "failure",
    [_accessor_database_fails, _accessor_raises, _drag_raises],
    ids=["database error in the accessor", "accessor raises", "drag arithmetic raises"],
)
class TestRouteStatesTheFailure:
    def test_the_answer_says_the_read_failed(self, world, failure):
        spec = _read(perf.symphony_ids()[0], patches=[failure()])
        assert states_failure(spec), (
            f"a failed read was not stated as one: status {spec['status']}, body {spec.get('json')}"
        )

    def test_the_answer_carries_no_zeroed_windows_and_no_zero_drag(self, world, failure):
        spec = _read(perf.symphony_ids()[0], patches=[failure()])
        body = spec.get("json") or {}
        assert not presents_zeros(spec), f"zeroed windows presented as a reading: {body}"
        assert body.get("est_annual_friction_drag_pct") in (None,), (
            f"a drag of {body.get('est_annual_friction_drag_pct')!r} presented for a failed read"
        )


class TestARealSymphonyWithNoExitsReadsATrueZero:
    def test_the_route_answers_200_with_zeroed_windows(self, world):
        spec = _read("symphony-that-never-exited")
        assert spec["status"] == 200, spec
        body = spec["json"]
        for window in WINDOWS:
            assert body["windows"][window] == {"exit_count": 0, "coverage_days": 0}
        assert body["est_annual_friction_drag_pct"] == pytest.approx(0.0, abs=1e-9)

    def test_the_page_shows_the_zero_not_a_dash(self, world):
        ids = perf.symphony_ids()
        never = _read("symphony-that-never-exited")
        out = perf.run_performance(
            ROWS + "return rows();",
            perf.real_routes(turnover_overrides={ids[0]: [never]}),
        )
        row = out.result[0]
        assert row[1:4] == ["0 (0d retained)"] * 3, row
        assert row[4] == "0.00%", row


class TestPageShowsDashesForAFailedRead:
    @pytest.mark.parametrize(
        "failure",
        [_accessor_database_fails, _accessor_raises],
        ids=["database error in the accessor", "accessor raises"],
    )
    def test_the_failed_symphony_has_a_row_of_dashes_and_the_others_keep_their_numbers(
        self, world, failure
    ):
        ids = perf.symphony_ids()
        failed_id = ids[0]
        failed = _read(failed_id, patches=[failure()])
        good = {sid: perf.get_real(f"/api/exit-turnover?symphony_id={sid}") for sid in ids[1:]}
        out = perf.run_performance(
            ROWS + "return rows();",
            perf.real_routes(turnover_overrides={failed_id: [failed]}),
        )
        table = out.result
        assert len(table) == len(ids), f"expected one row per symphony, got {table}"

        failed_cells = table[0][1:]
        assert len(failed_cells) == 4, table[0]
        for cell in failed_cells:
            assert DASH.match(cell) and not re.search(r"\d", cell), (
                f"the failed read shows {cell!r}, not a dash: {table[0]}"
            )
        assert not any(ZERO_CELL.match(cell) for row in table[:1] for cell in row)

        for row, sid in zip(table[1:], ids[1:], strict=True):
            body = good[sid]["json"]
            expected = [
                f"{body['windows'][w]['exit_count']} ({body['windows'][w]['coverage_days']}d retained)"
                for w in WINDOWS
            ]
            assert row[1:4] == expected, f"{sid}: {row}"
            assert row[4] == f"{body['est_annual_friction_drag_pct']:.2f}%", f"{sid}: {row}"


NOTE = """
const note = () => (H.text('[data-testid="exit-turnover-note"]') || '').trim().toLowerCase();
"""
LOST_WORDING = "connection lost"
LAST_VALUE_WORDING = "last"
NETWORK_NEVER_ANSWERED = {"reject": True}


@pytest.fixture
def server_error(world):
    """The route's own answer to a read that failed inside it: an error status with an error body."""
    ids = perf.symphony_ids()
    spec = _read(ids[0], patches=[_accessor_raises()])
    assert spec["status"] >= 400 and spec["json"].get("error"), spec
    return spec


def _is_dash_row(row: list[str]) -> bool:
    return all(DASH.match(cell) and not re.search(r"\d", cell) for cell in row[1:])


class TestNoteStatesOnlyWhatIsOnScreen:
    def test_every_read_answered_with_an_error_and_no_earlier_rows_is_not_called_a_lost_connection(
        self, world, server_error
    ):
        ids = perf.symphony_ids()
        out = perf.run_performance(
            ROWS + NOTE + "return {rows: rows(), note: note()};",
            perf.real_routes(turnover_overrides={sid: [server_error] for sid in ids}),
        )
        rows, text = out.result["rows"], out.result["note"]
        assert rows and all(_is_dash_row(row) for row in rows), f"fixture integrity: {rows}"
        assert text, "a failed read left no note beside the table"
        assert LOST_WORDING not in text, f"the server answered, yet the note says: {text!r}"
        assert LAST_VALUE_WORDING not in text, (
            f"every row is a dash (no value was ever received), yet the note says: {text!r}"
        )

    def test_every_read_answered_with_an_error_after_good_rows_is_not_called_a_lost_connection(
        self, world, server_error
    ):
        ids = perf.symphony_ids()
        good = {sid: perf.get_real(f"/api/exit-turnover?symphony_id={sid}") for sid in ids}
        out = perf.run_performance(
            ROWS + NOTE + "const first = rows(); await H.advance(60000);"
            " return {first, second: rows(), note: note()};",
            perf.real_routes(turnover_overrides={sid: [good[sid], server_error] for sid in ids}),
        )
        assert out.result["second"] == out.result["first"], "rows must be the last good ones"
        text = out.result["note"]
        assert text, "a failed read left no note beside the table"
        assert LOST_WORDING not in text, f"the server answered, yet the note says: {text!r}"

    @pytest.mark.parametrize(
        "failure",
        [pytest.param(None, id="server error"), pytest.param(NETWORK_NEVER_ANSWERED, id="network")],
    )
    def test_a_row_that_never_had_a_value_is_not_described_as_showing_its_last_values(
        self, world, server_error, failure
    ):
        ids = perf.symphony_ids()
        down = server_error if failure is None else failure
        out = perf.run_performance(
            ROWS + NOTE + "return {rows: rows(), note: note()};",
            perf.real_routes(turnover_overrides={ids[0]: [down]}),
        )
        rows, text = out.result["rows"], out.result["note"]
        assert _is_dash_row(rows[0]) and not _is_dash_row(rows[1]), f"fixture integrity: {rows}"
        assert text, "a failed read left no note beside the table"
        assert LAST_VALUE_WORDING not in text, (
            f"row 0 is a dash that never had a value, yet the note says: {text!r}"
        )
        if failure is None:
            assert LOST_WORDING not in text, f"the server answered, yet the note says: {text!r}"

    def test_one_failed_read_after_a_good_tick_keeps_its_row_and_still_carries_a_note(
        self, world, server_error
    ):
        ids = perf.symphony_ids()
        good = {sid: perf.get_real(f"/api/exit-turnover?symphony_id={sid}") for sid in ids}
        out = perf.run_performance(
            ROWS + NOTE + "const first = rows(); await H.advance(60000);"
            " return {first, second: rows(), note: note()};",
            perf.real_routes(turnover_overrides={ids[0]: [good[ids[0]], server_error]}),
        )
        assert out.result["second"] == out.result["first"]
        assert out.result["note"], "a failed read left no note beside the table"
        assert LOST_WORDING not in out.result["note"]

    def test_a_read_the_network_never_answered_still_says_the_connection_was_lost(self, world):
        ids = perf.symphony_ids()
        out = perf.run_performance(
            ROWS + NOTE + "return note();",
            perf.real_routes(turnover_overrides={sid: [NETWORK_NEVER_ANSWERED] for sid in ids}),
        )
        assert LOST_WORDING in out.result, out.result

    def test_a_symphony_list_the_server_answered_with_an_error_is_not_called_a_lost_connection(
        self, world, server_error
    ):
        routes = perf.real_routes()
        routes.insert(0, route(r"/api/performance/symphonies$", server_error, method="GET"))
        out = perf.run_performance(ROWS + NOTE + "return note();", routes)
        assert out.result, "a failed list read left no note beside the table"
        assert LOST_WORDING not in out.result, out.result

    def test_a_symphony_list_the_network_never_answered_still_says_the_connection_was_lost(
        self, world
    ):
        routes = perf.real_routes()
        routes.insert(
            0, route(r"/api/performance/symphonies$", NETWORK_NEVER_ANSWERED, method="GET")
        )
        out = perf.run_performance(ROWS + NOTE + "return note();", routes)
        assert LOST_WORDING in out.result, out.result
