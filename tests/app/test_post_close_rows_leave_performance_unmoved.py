"""Rows the engine writes after the close do not move the Performance tab or the card sparklines.

The engine's data phase writes shadow rows at 16:01 to 16:05 and keeps writing overnight. The
dashboard's own routes cut the rows they read at the close snapshot's capture moment while the
market is closed. `/api/performance` and `/api/chart/<symphony>` are read by the Performance tab
and by the detail panel's sparkline, so they cut the same way: their bodies are the same before
and after rows stamped later than the capture are added.

Real routes over a temp database holding weeks of rows, the close snapshot of Friday 2026-10-30
captured at 16:00:42 ET, a virtual host clock. The rows are added through the shared
`write_engine_cycle` (new shadow rows, then the state saved with the cycle's stamp). Expected
values are never typed: the baseline is the response read just after the capture.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.app._closed_session_world import ClosedWorld, build_history_world
from tests.app._refresh_timing_frozen_world import differences
from tests.app._refresh_timing_state_world import SYMPHONY_IDS
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle

FRIDAY = "2026-10-30"

# The engine's cycles after the capture (16:00:42): the data phase, the grace window's end, the
# evening, the small hours. A sparkline is the tape of the current ET date, so it is compared only
# on the session's own date.
LATER_CYCLES = (
    (FRIDAY, "16:01:10"),
    (FRIDAY, "16:03:05"),
    (FRIDAY, "16:05:00"),
    (FRIDAY, "20:00:05"),
    ("2026-10-31", "03:00:05"),
)


def first_per_read(changed: dict) -> str:
    """One moved figure of each read that has any, so a failure names every route at fault."""
    first: dict[str, str] = {}
    for path, (before, after) in changed.items():
        first.setdefault(path.split(".")[0], f"{path}: {before!r} -> {after!r}")
    return "; ".join(first.values())


def read_performance_and_sparklines(cw: ClosedWorld, *, sparklines: bool = True) -> dict:
    out = {
        "performance/30d": cw.poll("/api/performance?days=30d"),
        "performance/90d": cw.poll("/api/performance?days=90d"),
        "performance/rows60": cw.poll("/api/performance?days=60"),
    }
    for sym_id in SYMPHONY_IDS:
        out[f"performance/symphony/{sym_id}"] = cw.poll(
            f"/api/performance?days=30d&scope=symphony&symphony_id={sym_id}"
        )
        if sparklines:
            out[f"chart/{sym_id}"] = cw.poll(f"/api/chart/{sym_id}")
    return out


def test_rows_written_after_the_close_do_not_move_performance_or_the_sparklines(
    monkeypatch, tmp_path
):
    cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
    cw.at(FRIDAY, "16:01:00")
    baseline = read_performance_and_sparklines(cw)
    assert baseline["chart/" + SYMPHONY_IDS[0]]["data"], "fixture sanity: the sparkline has points"

    failures = []
    for number, (day, clock) in enumerate(LATER_CYCLES, start=1):
        write_engine_cycle(cw.world, local(day, clock), number)
        cw.at(day, clock)
        same_date = day == FRIDAY
        current = read_performance_and_sparklines(cw, sparklines=same_date)
        changed = differences({k: baseline[k] for k in current}, current)
        if changed:
            failures.append(f"after the cycle at {day} {clock} ET: {first_per_read(changed)}")
    assert not failures, "rows stamped after the capture moved a closed-market read:\n" + "\n".join(
        failures
    )


@pytest.mark.parametrize("route", ["/api/performance?days=30d", "/api/chart/{symphony}"])
def test_the_rows_the_test_adds_are_real_rows_of_the_window(monkeypatch, tmp_path, route):
    """Guards the test above: once the market opens the same rows do move the same routes, so the
    comparison is not between two reads that could never differ."""
    cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
    path = route.format(symphony=SYMPHONY_IDS[0])
    # Monday's session: new rows, read while the market is open.
    cw.at("2026-11-02", "10:00:00")
    before = cw.poll(path)
    write_engine_cycle(cw.world, local("2026-11-02", "10:00:20"), 1)
    cw.at("2026-11-02", "10:01:00")
    assert differences(before, cw.poll(path)), (
        f"{path}: an open-market cycle's rows must reach the route"
    )
