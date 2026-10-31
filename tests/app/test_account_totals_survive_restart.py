"""A daemon restart while the markets are closed moves no number.

Every deploy and every crash recovery happens outside market hours, so a restart with the markets
closed is the ordinary case. The account totals that Today, Cumulative and the account value rest
on are latched for the close snapshot once; the latch is stored where the engine's state rewrites
cannot drop it and is read back after a restart. After the restart, before the first account
refresh, and after it, every figure of the routes and of the page reads what it read before the
restart, until the next session starts.

A restart is simulated in process the way the shared closed-market world resets the account
totals (`clean_account_totals`): the cache, the last-good copy, the success stamps and the latch
held in the module are emptied. The database is left as it is, which is what a restart leaves.
Composer answers with different figures after the restart than before it (Composer's totals drift
after the close), so a latch taken again from the post-restart refresh shows.

Expected values are never typed: the baseline is the response before the restart.
"""

from __future__ import annotations

from datetime import date

import pytest

import app as app_module
from tests.app._closed_session_world import ClosedWorld, build_history_world
from tests.app._refresh_timing_frozen_world import (
    clean_account_totals,
    describe,
    expected_account_value,
)
from tests.app.test_closed_market_numbers_never_move import (
    local,
    moved_figures,
    read_everything,
    write_engine_cycle,
)


def routes_first(changed: dict) -> dict:
    """The moved figures in the order an operator meets them: the poll, the strip, the chart, the page."""
    order = ("state.", "strip.", "chart.", "page.figures.", "page.numbers")
    return dict(
        sorted(
            changed.items(),
            key=lambda item: next(
                (i for i, prefix in enumerate(order) if item[0].startswith(prefix)), len(order)
            ),
        )
    )


def restart(cw: ClosedWorld) -> None:
    """Empty everything the module holds about the account totals; the database stays."""
    clean_account_totals(cw.monkeypatch)


def first_refresh(cw: ClosedWorld) -> None:
    """The account refresh job, against a Composer body whose figures have moved."""
    cw.composer.version += 5
    app_module._refresh_account_totals()


def world_at(monkeypatch, tmp_path, day: str, clock: str) -> ClosedWorld:
    cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
    cw.at(day, clock)
    return cw


@pytest.mark.parametrize(
    "engine_rewrote_state", [False, True], ids=["state-untouched", "state-rewritten"]
)
@pytest.mark.parametrize(
    ("day", "clock"),
    [("2026-10-30", "20:00:00"), ("2026-10-31", "02:00:00"), ("2026-11-02", "08:30:00")],
)
def test_a_restart_while_closed_moves_no_figure(
    monkeypatch, tmp_path, day, clock, engine_rewrote_state
):
    cw = world_at(monkeypatch, tmp_path, day, clock)
    baseline = read_everything(cw.world)
    assert baseline["state"]["portfolio_strip"].get("account_value") is not None
    if engine_rewrote_state:
        # The engine saves bot_state from its own copy: any key the daemon had put there is gone.
        write_engine_cycle(cw.world, local(day, clock), 1)

    failures = []
    restart(cw)
    for step in (
        "after the restart, before any refresh",
        "after the first refresh",
        "after a second restart",
    ):
        if step == "after the first refresh":
            first_refresh(cw)
        elif step == "after a second restart":
            restart(cw)
        changed = moved_figures(baseline, read_everything(cw.world))
        if changed:
            failures.append(f"{step}: {describe(routes_first(changed), 6)}")
    assert not failures, f"{day} {clock} ET: figures moved across a restart:\n" + "\n".join(
        failures
    )


def test_the_close_totals_are_released_when_the_next_session_starts(monkeypatch, tmp_path):
    cw = world_at(monkeypatch, tmp_path, "2026-10-31", "02:00:00")
    friday_account_value = cw.poll("/api/state")["portfolio_strip"]["account_value"]
    restart(cw)

    # Monday's session: the first open cycle commits, the refresh that follows reads Composer.
    write_engine_cycle(cw.world, local("2026-11-02", "09:30:20"), 1)
    cw.composer.version += 5
    cw.at("2026-11-02", "09:31:00")
    app_module._refresh_account_totals()
    cw.at("2026-11-02", "09:31:10")
    state = cw.poll("/api/state")

    assert state["market_state"] == "open"
    shown = state["portfolio_strip"]["account_value"]
    assert shown == pytest.approx(expected_account_value(cw.composer.version)), (
        f"Monday 09:31 ET the account value is {shown}; Composer's figure of that refresh is "
        f"{expected_account_value(cw.composer.version)} (Friday's close totals were {friday_account_value})"
    )
