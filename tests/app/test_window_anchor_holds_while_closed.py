"""A trailing or year-to-date window is counted from the session the figures belong to, never
from the wall clock.

While the markets are closed no number moves, and a window's lower bound is a number: every
windowed figure (the headline guard alpha, the window Max DD and volatility legs, the hero chart,
the Performance metrics, the dollar-saved panel and the History windows) is cut at a date. The
date is the ET trading day of the session the figures describe: the day of the stored close
snapshot while the market is closed or in pre-market, today's session once one has started. It
moves only when the next session starts.

Real routes over a temp database holding 60 or more days of rows, with the real
`analytics._window_cutoff_date` (the shared worlds pin it to their baseline date, which would hide
the fault) under a virtual host clock. The droplet's host clock is UTC, so the host's date turns
over at 20:00 EDT / 19:00 EST, in the middle of a closed evening.

Expected dates are computed from the seeded day list and the session day, never read from a
response; the figure comparisons are between two reads of the same world.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.app._closed_session_world import (
    FRIDAY_SESSION,
    WINDOW_TOKENS,
    ClosedWorld,
    Session,
    build_history_world,
)
from tests.app._refresh_timing_frozen_world import differences
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle

# Fields that are a clock reading or the label of the market state, as in the closed-market rule.
CLOCK_FIELDS = frozenset(
    {
        "state.market_state",
        "state.meta.market_state",
        "state.meta.clock_et",
    }
)

# Instants of a closed stretch after Friday's close. Each is named by what it crosses: the host's
# UTC date turns over at 20:00 EDT, and again on Sunday at 19:00 EST (the clock change put the
# offset back an hour that night).
FRIDAY_AFTER_CLOSE = ("2026-10-30", "16:06:00")
STRETCH = (
    FRIDAY_AFTER_CLOSE,
    ("2026-10-30", "19:59:00"),
    ("2026-10-30", "20:01:00"),  # 00:01 UTC Saturday
    ("2026-10-31", "12:00:00"),
    ("2026-10-31", "20:01:00"),  # 00:01 UTC Sunday
    ("2026-11-01", "18:59:00"),
    ("2026-11-01", "19:01:00"),  # 00:01 UTC Monday
    ("2026-11-02", "09:29:00"),
)

SESSION_DAY = date.fromisoformat(FRIDAY_SESSION.session_day)
TRAILING_DAYS = {"30d": 30, "60d": 60, "90d": 90, "125d": 125, "1y": 365}


def read_windows(cw: ClosedWorld) -> dict:
    """The body of every route that cuts a window, for every token."""
    out: dict = {"state": cw.poll("/api/state")}
    for token in WINDOW_TOKENS:
        out[f"strip/{token}"] = cw.poll(f"/api/strip/{token}")
        out[f"hero/{token}"] = cw.poll(f"/api/hero-chart/{token}")
        out[f"performance/{token}"] = cw.poll(f"/api/performance?days={token}")
        out[f"guard-alpha-summary/{token}"] = cw.poll(f"/api/guard-alpha-summary?window={token}")
    for days in (30, 90, "ytd"):
        history = dict(cw.poll(f"/api/history/{days}"))
        # The feed of today's exits is keyed to the current ET date by design; it is not a window.
        history.pop("todays_exits", None)
        out[f"history/{days}"] = history
    return out


def moved(baseline: dict, current: dict) -> dict:
    return {
        path: pair
        for path, pair in differences(baseline, current).items()
        if path not in CLOCK_FIELDS
    }


def first_per_route(changed: dict) -> str:
    """One moved figure of each route that has any, so a failure names every route at fault."""
    first: dict[str, str] = {}
    for path, (before, after) in changed.items():
        first.setdefault(path.split(".")[0], f"{path}: {before!r} -> {after!r}")
    return "; ".join(first.values())


def all_days(session: Session, cw: ClosedWorld) -> list[date]:
    return sorted({*cw.history_days, date.fromisoformat(session.previous_day), SESSION_DAY})


def first_day_on_or_after(days: list[date], cutoff: date) -> str | None:
    return next((d.isoformat() for d in days if d >= cutoff), None)


def test_no_windowed_figure_moves_between_the_close_and_the_next_open(monkeypatch, tmp_path):
    cw = build_history_world(monkeypatch, tmp_path)
    failures: list[str] = []
    baseline = None
    for day, clock in STRETCH:
        cw.at(day, clock)
        current = read_windows(cw)
        if baseline is None:
            baseline = current
            continue
        changed = moved(baseline, current)
        if changed:
            failures.append(f"{day} {clock} ET: {first_per_route(changed)}")
    assert not failures, "windowed figures moved while the markets were closed:\n" + "\n".join(
        failures
    )


@pytest.mark.parametrize("token", sorted(TRAILING_DAYS))
def test_a_closed_window_starts_that_many_days_before_the_session_it_describes(
    monkeypatch, tmp_path, token
):
    cw = build_history_world(monkeypatch, tmp_path)
    days = all_days(FRIDAY_SESSION, cw)
    cutoff = SESSION_DAY - timedelta(days=TRAILING_DAYS[token])
    want_first = first_day_on_or_after(days, cutoff)
    want_count = len([d for d in days if d >= cutoff])
    assert want_first is not None and want_count >= 2, "the seeded history must reach the window"
    assert days[0] < cutoff, "fixture sanity: the history starts before the window does"
    wrong = []
    # Every instant of the stretch: a host date that is 1, 2 or 3 days past the session moves
    # the cutoff over a different trading day for each token.
    for day, clock in STRETCH:
        cw.at(day, clock)
        hero = cw.poll(f"/api/hero-chart/{token}")
        strip = cw.poll(f"/api/strip/{token}")
        performance = cw.poll(f"/api/performance?days={token}")
        summary = cw.poll(f"/api/guard-alpha-summary?window={token}")
        for label, got, want in (
            ("hero first date", hero["hist_dates"][0], want_first),
            ("hero days", len(hero["hist_dates"]), want_count),
            ("strip window_day_count", strip["window_day_count"], want_count),
            ("performance first date", performance["dates"][0], want_first),
            ("dollar-saved first day", summary["date_range"]["earliest"], want_first),
        ):
            if got != want:
                wrong.append(f"{day} {clock} ET: {label} is {got!r}, expected {want!r}")
    assert not wrong, f"the {token} window of the {SESSION_DAY} session:\n" + "\n".join(wrong)


@pytest.mark.parametrize("days", [30, 90])
def test_a_history_window_of_a_closed_session_counts_back_from_that_session(
    monkeypatch, tmp_path, days
):
    cw = build_history_world(monkeypatch, tmp_path)
    cutoff = SESSION_DAY - timedelta(days=days)
    post_mortem_days = all_days(FRIDAY_SESSION, cw)
    want = [d.isoformat() for d in post_mortem_days if d >= cutoff]
    got = {}
    for day, clock in (FRIDAY_AFTER_CLOSE, ("2026-11-01", "19:01:00")):
        cw.at(day, clock)
        got[f"{day} {clock}"] = cw.poll(f"/api/history/{days}")["daily_dates"]
    wrong = {when: dates for when, dates in got.items() if dates != want}
    assert not wrong, (
        f"/api/history/{days} reads the days {want[0]} to {want[-1]} ({len(want)} days) for the "
        f"session of {SESSION_DAY}; got "
        + "; ".join(f"{w}: {d[:1]}..{d[-1:]} ({len(d)} days)" for w, d in wrong.items())
    )


def test_the_window_moves_on_when_the_next_session_starts(monkeypatch, tmp_path):
    cw = build_history_world(monkeypatch, tmp_path)
    monday = date.fromisoformat("2026-11-02")
    days = all_days(FRIDAY_SESSION, cw)
    cw.at("2026-10-30", "16:06:00")
    friday_first = cw.poll("/api/hero-chart/30d")["hist_dates"][0]

    # Monday's session: the first open cycle has committed its rows and its state.
    write_engine_cycle(cw.world, local("2026-11-02", "10:00:20"), 1)
    cw.at("2026-11-02", "10:01:00")
    open_hero = cw.poll("/api/hero-chart/30d")
    open_strip = cw.poll("/api/strip/30d")

    cutoff = monday - timedelta(days=30)
    want_first = first_day_on_or_after(days, cutoff)
    assert friday_first == first_day_on_or_after(days, SESSION_DAY - timedelta(days=30))
    assert want_first != friday_first, "fixture sanity: the two windows start on different days"
    assert open_hero["hist_dates"][0] == want_first, (
        f"during Monday's session the 30d window starts {cutoff}, so its first day is "
        f"{want_first}; the chart starts {open_hero['hist_dates'][0]}"
    )
    want_count = len([d for d in [*days, monday] if d >= cutoff])
    assert open_strip["window_day_count"] == want_count, (
        f"the 30d strip holds {open_strip['window_day_count']} days, expected {want_count}"
    )


def test_a_year_to_date_window_does_not_reset_when_the_host_date_turns_over_the_new_year(
    monkeypatch, tmp_path
):
    """The last session of 2026 closes on 31 December; the host's UTC date is 1 January from
    19:00 EST that evening, and the market is closed on 1 January."""
    session = Session(
        name="last session of the year",
        session_day="2026-12-31",
        previous_day="2026-12-30",
        baseline=local("2026-12-31", "16:02:00"),
        steps=(),
    )
    cw = build_history_world(monkeypatch, tmp_path, session)
    reads = {}
    for day, clock in (
        ("2026-12-31", "16:06:00"),
        ("2026-12-31", "19:01:00"),  # 00:01 UTC, 1 January
        ("2027-01-01", "12:00:00"),
        ("2027-01-03", "12:00:00"),
    ):
        cw.at(day, clock)
        hero = cw.poll("/api/hero-chart/ytd")
        reads[f"{day} {clock}"] = {
            "hero": hero,
            "strip": cw.poll("/api/strip/ytd"),
            "performance": cw.poll("/api/performance?days=ytd"),
            "dollar saved": cw.poll("/api/guard-alpha-summary?window=ytd"),
        }
    first = next(iter(reads.values()))
    assert first["hero"]["hist_dates"], "fixture sanity: the year has rows on 31 December"
    assert all(d.startswith("2026") for d in first["hero"]["hist_dates"])
    failures = [
        f"{when}: {first_per_route(moved(first, current))}"
        for when, current in list(reads.items())[1:]
        if moved(first, current)
    ]
    assert not failures, (
        "year-to-date figures moved after the host date reached the new year:\n"
        + ("\n".join(failures))
    )
