"""Cluster 5 AC-1 — RED tests: the synthetic-history fetch window must be
sized from a TRADING-DAY requirement, not a fixed calendar-day literal.

THE DEFECT
----------
``synthetic_history.generate_synthetic_history`` computes its Alpaca fetch
window with a hardcoded ``timedelta(days=180)`` literal (twice — the daily and
the intraday start). The audit (autotuner__2026-05-21.md finding 8) measured
that 180 calendar days yields FEWER than 125 trading days in essentially every
realistic window: 180 * 5/7 = 128.6 raw weekdays, minus ~9 US holidays per
~6 months gives ~119-123 trading days. The autotuner then slices ``[-125:]``
and silently optimises Sortino/DSR on a degenerate ~3-day validation fold.

THE CONTRACT
------------
The fetch window must guarantee at least:

    required_trading_days = WALK_FORWARD_TRADING_DAYS         (autotuner replay)
                          + MC_MIN_HISTORY_DAYS               (MC warmup floor)
                          + (MC_VOL_WINDOW_DAYS - 1)          (vol-window warmup)
                          + a holiday / margin buffer

trading days between the computed start date and the end date — even across a
window that spans multiple market holidays. The start date is derived by
counting BACK that many trading days from the end date, not by subtracting a
fixed calendar span.

WHY THE WARMUP FLOOR IS ADDITIVE
--------------------------------
The 125-day walk-forward replay window is the LAST 125 trading days. The
Monte-Carlo gate on the FIRST of those replay days still needs
``MC_MIN_HISTORY_DAYS`` eligible days of prior history, and an eligible day
requires ``MC_VOL_WINDOW_DAYS - 1`` raw days of vol-window warmup before it
(math_engine.run_monte_carlo's eligible-pool guard). So the fetched history
must extend ``MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS - 1`` trading days
BEFORE the replay window — the warmup precedes the replay, it does not overlap.

PROVENANCE
----------
Expected trading-day counts are computed INDEPENDENTLY here via
``numpy.busday_count`` against an explicit, hand-listed set of US market
holidays for the test window — never read back from the producer. The required
floor is derived from the named math_engine / autotuner constants, not a
literal. ``pytest.approx`` is not used: trading-day counts are exact integers.

TESTABILITY
-----------
The window-sizing logic must be exposed as a pure, importable helper so it can
be exercised without a live Alpaca fetch. These tests import that helper. If
the implementer has not yet extracted it the import-guarded tests fail RED with
a clear message naming the expected surface.
"""

from __future__ import annotations

import datetime as _dt

import numpy as np
import pytest

import math_engine


# ---------------------------------------------------------------------------
# Independently-derived required floor — from named constants, never a literal.
# ---------------------------------------------------------------------------

# The autotuner walk-forward replay window length. autotuner.py slices the last
# 125 trading days of synthetic history; the plan (AC-1) names 125 explicitly.
# Pinned here as a test-local expectation so a drift in the replay window is
# caught — the implementer's production constant must equal this.
_WALK_FORWARD_TRADING_DAYS = 125

# MC warmup floor: run_monte_carlo needs MC_MIN_HISTORY_DAYS eligible days, and
# each eligible day needs MC_VOL_WINDOW_DAYS - 1 raw days of vol warmup behind
# it (math_engine eligible-pool guard). Derived from named math_engine
# constants — NOT hardcoded.
_MC_WARMUP_TRADING_DAYS = (
    math_engine.MC_MIN_HISTORY_DAYS + (math_engine.MC_VOL_WINDOW_DAYS - 1)
)

# The minimum trading days the fetch window must yield, BEFORE any holiday /
# margin buffer. The implementer's buffer makes the real target larger; the
# window must never yield fewer than this bare minimum.
_MIN_REQUIRED_TRADING_DAYS = _WALK_FORWARD_TRADING_DAYS + _MC_WARMUP_TRADING_DAYS


# ---------------------------------------------------------------------------
# Independent trading-day oracle — numpy.busday_count + explicit US holidays.
# This is the test's own reference; the producer is never consulted.
# ---------------------------------------------------------------------------

# US equity market holidays across the test windows below. Hand-listed from the
# NYSE published calendar — the independent oracle. Covers late-2025 through
# 2026 so every window exercised by these tests is fully accounted for.
_US_MARKET_HOLIDAYS = np.array(
    [
        # 2025
        "2025-01-01",  # New Year's Day
        "2025-01-20",  # MLK Jr. Day
        "2025-02-17",  # Washington's Birthday
        "2025-04-18",  # Good Friday
        "2025-05-26",  # Memorial Day
        "2025-06-19",  # Juneteenth
        "2025-07-04",  # Independence Day
        "2025-09-01",  # Labor Day
        "2025-11-27",  # Thanksgiving
        "2025-12-25",  # Christmas
        # 2026
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # MLK Jr. Day
        "2026-02-16",  # Washington's Birthday
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-06-19",  # Juneteenth
        "2026-07-03",  # Independence Day (observed)
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving
        "2026-12-25",  # Christmas
    ],
    dtype="datetime64[D]",
)


def _independent_trading_day_count(start: _dt.date, end: _dt.date) -> int:
    """Count trading days in [start, end] INCLUSIVE — the test's own oracle.

    numpy.busday_count is half-open [start, end); add the end date back if it
    is itself a trading day so the comparison is inclusive of both endpoints,
    matching how a fetch window [start_str, end_str] is consumed.
    """
    count = int(
        np.busday_count(
            np.datetime64(start, "D"),
            np.datetime64(end, "D"),
            holidays=_US_MARKET_HOLIDAYS,
        )
    )
    end64 = np.datetime64(end, "D")
    if np.is_busday(end64, holidays=_US_MARKET_HOLIDAYS):
        count += 1
    return count


# ---------------------------------------------------------------------------
# The helper under test — import-guarded so a not-yet-extracted helper fails
# RED with a clear message rather than an ImportError at collection.
# ---------------------------------------------------------------------------


def _resolve_window_helper():
    """Return the pure fetch-window-start helper synthetic_history must expose.

    Contract: a callable taking an end date and returning the fetch-window
    start date such that the [start, end] span yields >= the required trading
    days. The implementer chooses the exact name; this resolver accepts the
    documented candidates and fails RED (not errors) if none exists.
    """
    synth = pytest.importorskip(
        "synthetic_history",
        reason="synthetic_history import unavailable in this environment",
    )
    for name in (
        "compute_fetch_window_start",
        "trading_day_window_start",
        "fetch_window_start_date",
    ):
        fn = getattr(synth, name, None)
        if callable(fn):
            return fn
    pytest.fail(
        "synthetic_history must expose a PURE, importable helper that sizes "
        "the fetch-window start date from a TRADING-DAY count (expected one "
        "of: compute_fetch_window_start / trading_day_window_start / "
        "fetch_window_start_date). The 180-calendar-day literal must be "
        "replaced by a trading-day-counted window so it can be tested without "
        "a live Alpaca fetch."
    )


def _call_window_helper(fn, end_date: _dt.date) -> _dt.date:
    """Call the window helper and normalise the result to a datetime.date.

    The helper may return a date or a datetime; both are acceptable. Anything
    else fails RED — the contract is a date-like start boundary.
    """
    result = fn(end_date)
    if isinstance(result, _dt.datetime):
        return result.date()
    if isinstance(result, _dt.date):
        return result
    pytest.fail(
        f"fetch-window helper must return a date or datetime, got "
        f"{type(result).__name__} ({result!r})."
    )


# ---------------------------------------------------------------------------
# AC-1 tests
# ---------------------------------------------------------------------------


def test_required_floor_is_derived_from_named_constants_not_a_literal() -> None:
    """Sanity pin on the test's own derived floor.

    The required floor must be the sum of the walk-forward window and the MC
    warmup (itself MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS - 1). This guards
    the other tests in this file: if a future edit to math_engine's MC
    constants changes the warmup, this assertion documents the new expectation
    explicitly rather than letting the window tests drift silently.
    """
    expected_warmup = (
        math_engine.MC_MIN_HISTORY_DAYS + math_engine.MC_VOL_WINDOW_DAYS - 1
    )
    assert _MC_WARMUP_TRADING_DAYS == expected_warmup
    assert (
        _MIN_REQUIRED_TRADING_DAYS
        == _WALK_FORWARD_TRADING_DAYS + expected_warmup
    )
    # The whole point of the fix: the floor exceeds the old 180-calendar-day
    # window's worst-case trading-day yield (~119-123). 125 + 39 = 164 trading
    # days, which 180 calendar days (~128 weekdays) cannot possibly cover.
    assert _MIN_REQUIRED_TRADING_DAYS > 128, (
        "the required trading-day floor must exceed the raw weekday count of "
        "a 180-calendar-day window — that is the entire defect being fixed."
    )


def test_window_yields_required_trading_days_for_a_plain_end_date() -> None:
    """A non-holiday-spanning end date: the computed window must still yield
    at least the required trading-day floor.

    End date 2026-05-15 (a Friday). The window helper counts back from it; the
    independent numpy.busday_count oracle confirms the [start, end] span holds
    >= _MIN_REQUIRED_TRADING_DAYS trading days.
    """
    fn = _resolve_window_helper()
    end_date = _dt.date(2026, 5, 15)
    start_date = _call_window_helper(fn, end_date)

    assert start_date < end_date, (
        "fetch-window start must precede the end date."
    )
    trading_days = _independent_trading_day_count(start_date, end_date)
    assert trading_days >= _MIN_REQUIRED_TRADING_DAYS, (
        f"fetch window {start_date}..{end_date} yields only {trading_days} "
        f"trading days; the autotuner replay + MC warmup need at least "
        f"{_MIN_REQUIRED_TRADING_DAYS}. A fixed calendar-day window "
        f"under-delivers — size the window from a trading-day count."
    )


def test_window_spanning_a_heavy_holiday_cluster_still_meets_the_floor() -> None:
    """Holiday-spanning window — the core AC-1 scenario.

    End date 2026-01-09: counting ~164+ trading days back from early January
    sweeps through the densest holiday stretch of the US calendar — Memorial
    Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas, New
    Year's Day, plus MLK day. A fixed 180-calendar-day window loses ~9 trading
    days to these holidays and falls short. The trading-day-counted window must
    NOT — it must still yield >= the required floor INCLUSIVE of every holiday.
    """
    fn = _resolve_window_helper()
    end_date = _dt.date(2026, 1, 9)  # a Friday
    start_date = _call_window_helper(fn, end_date)

    trading_days = _independent_trading_day_count(start_date, end_date)
    assert trading_days >= _MIN_REQUIRED_TRADING_DAYS, (
        f"holiday-spanning fetch window {start_date}..{end_date} yields only "
        f"{trading_days} trading days (< required {_MIN_REQUIRED_TRADING_DAYS}). "
        f"This is exactly the audit's finding-8 defect: a calendar-day window "
        f"loses ~9 trading days to holidays and the autotuner silently runs a "
        f"degenerate validation fold. The window must be counted in trading "
        f"days so holidays are accounted for."
    )


def test_window_is_not_a_fixed_180_calendar_day_subtraction() -> None:
    """The window must NOT be a fixed 180-calendar-day subtraction.

    A trading-day-counted window for the required floor (>= 164 trading days)
    necessarily spans MORE than 180 calendar days once weekends and holidays
    are included (~164 trading days ~ 230+ calendar days). If the helper still
    returns exactly end - 180 days, the calendar span is exactly 180 and the
    fix was not applied. This test fails RED against the old literal and GREEN
    once the window is trading-day-sized.
    """
    fn = _resolve_window_helper()
    end_date = _dt.date(2026, 5, 15)
    start_date = _call_window_helper(fn, end_date)

    calendar_span = (end_date - start_date).days
    assert calendar_span != 180, (
        f"fetch-window calendar span is exactly 180 days — the old "
        f"`timedelta(days=180)` literal is still in place. A window holding "
        f">= {_MIN_REQUIRED_TRADING_DAYS} trading days must span well over "
        f"180 calendar days once weekends + holidays are counted."
    )
    assert calendar_span > 180, (
        f"fetch-window calendar span is {calendar_span} days — to hold "
        f">= {_MIN_REQUIRED_TRADING_DAYS} trading days the window must span "
        f"more than 180 calendar days."
    )


@pytest.mark.parametrize(
    "end_date",
    [
        _dt.date(2026, 1, 9),   # spans year-end + Thanksgiving holiday cluster
        _dt.date(2026, 3, 13),  # spans the DST transition + winter holidays
        _dt.date(2026, 5, 15),  # spring end date
        _dt.date(2025, 12, 12), # spans the autumn holiday run
    ],
)
def test_window_meets_floor_across_multiple_end_dates(
    end_date: _dt.date,
) -> None:
    """Property: for ANY end date the computed window yields >= the required
    trading-day floor.

    The window helper must be robust across the whole calendar — start dates
    landing before, during, and after holiday clusters. Each end date is
    checked against the independent numpy.busday_count oracle. A
    holiday-blind calendar-day window fails on the holiday-dense end dates.
    """
    fn = _resolve_window_helper()
    start_date = _call_window_helper(fn, end_date)

    trading_days = _independent_trading_day_count(start_date, end_date)
    assert trading_days >= _MIN_REQUIRED_TRADING_DAYS, (
        f"end date {end_date}: window {start_date}..{end_date} yields "
        f"{trading_days} trading days, below the required "
        f"{_MIN_REQUIRED_TRADING_DAYS}."
    )


# ---------------------------------------------------------------------------
# AC-1 Revise round — wiring + shortfall guard.
#
# The window-sizing helper passing in isolation does not prove the production
# path USES it, nor that a genuine bar shortfall is surfaced rather than
# silently sliced. Audit finding 8's fix has TWO parts: (a) size the window
# with margin — covered above; (b) "assert len(intraday_dates) >= 125 (or a
# documented floor) before proceeding — raise/log ERROR on shortfall instead
# of silently running a degenerate split." The plan's Edge Cases likewise
# require "Alpaca returning fewer bars than requested (partial data) — the
# bare-except replacement must surface this, not swallow it." These tests pin
# part (b) and the wiring.
# ---------------------------------------------------------------------------

import ast as _ast  # noqa: E402  (Revise-round import, kept local to its section)


def _synth_tree() -> _ast.Module:
    """Parse synthetic_history.py — Revise-round static analysis."""
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[2] / "synthetic_history.py"
    assert path.is_file(), f"expected production module at {path}"
    return _ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_no_fixed_180_day_timedelta_literal_survives_in_code() -> None:
    """Static guard: no executable ``timedelta(days=180)`` may remain.

    The helper passing in isolation does not prove the old literal is gone
    from the fetch-window code path. A worse implementation could add the
    helper yet leave the original ``timedelta(days=180)`` driving the actual
    fetch. This walks the AST for any ``timedelta`` call with ``days`` ~ 180
    and fails if one survives (a docstring mention of "180" is fine — only
    executable Call nodes are inspected).
    """
    tree = _synth_tree()
    offenders: list[int] = []
    for node in _ast.walk(tree):
        if not isinstance(node, _ast.Call):
            continue
        func = node.func
        is_timedelta = (
            isinstance(func, _ast.Name) and func.id == "timedelta"
        ) or (
            isinstance(func, _ast.Attribute) and func.attr == "timedelta"
        )
        if not is_timedelta:
            continue
        for kw in node.keywords:
            if kw.arg == "days" and isinstance(kw.value, _ast.Constant):
                # The old window literal was 180; flag anything in that range
                # so a near-miss (e.g. 175/185) is also caught.
                if isinstance(kw.value.value, (int, float)) and (
                    150 <= kw.value.value <= 220
                ):
                    offenders.append(node.lineno)
    assert not offenders, (
        f"synthetic_history.py still has an executable `timedelta(days=~180)` "
        f"call at line(s) {offenders}. The fetch window must be sized by the "
        f"trading-day-counted helper, not a calendar-day literal."
    )


def test_generate_synthetic_history_calls_the_window_helper() -> None:
    """Static guard: ``generate_synthetic_history`` must invoke the
    trading-day window helper.

    Pins the wiring: the production fetch path must call
    ``compute_fetch_window_start`` (or the accepted alias). Without this a
    helper could exist, pass the isolation tests, and never be reached by the
    real fetch.
    """
    tree = _synth_tree()
    gen = None
    for node in _ast.walk(tree):
        if (
            isinstance(node, _ast.FunctionDef)
            and node.name == "generate_synthetic_history"
        ):
            gen = node
            break
    assert gen is not None, (
        "synthetic_history.generate_synthetic_history not found."
    )

    helper_names = {
        "compute_fetch_window_start",
        "trading_day_window_start",
        "fetch_window_start_date",
    }
    called = False
    for node in _ast.walk(gen):
        if isinstance(node, _ast.Call):
            f = node.func
            if isinstance(f, _ast.Name) and f.id in helper_names:
                called = True
            elif isinstance(f, _ast.Attribute) and f.attr in helper_names:
                called = True
    assert called, (
        "generate_synthetic_history does not call the trading-day fetch-window "
        "helper (compute_fetch_window_start / trading_day_window_start / "
        "fetch_window_start_date). The window-sizing fix is not wired into the "
        "production fetch path."
    )


def test_replay_window_shortfall_is_surfaced_not_silently_sliced() -> None:
    """Audit finding 8 part (b): a trading-day shortfall must be SURFACED.

    Before the fix, ``intraday_dates = sorted(...)[-125:]`` silently takes
    whatever exists — if the upstream fetch returns fewer than 125 trading
    days the autotuner runs a degenerate validation fold with no warning.
    The fix must surface the shortfall: when the available replay days fall
    below the documented floor, the code must raise OR log at ERROR/WARNING
    level — it must NOT silently proceed.

    Static check: ``generate_synthetic_history`` must contain, downstream of
    the intraday-date collection, either a comparison of a ``len(...)`` of the
    replay-date sequence against a numeric floor that feeds a raise/log, OR an
    explicit raise / logging.error|warning whose reachability is guarded by
    such a length check. We assert the WEAKER, robust proxy: the function body
    references both a length check on the replay dates AND a raise or an
    ERROR/WARNING log — the silent bare ``[-125:]`` slice with no guard fails.
    """
    tree = _synth_tree()
    gen = None
    for node in _ast.walk(tree):
        if (
            isinstance(node, _ast.FunctionDef)
            and node.name == "generate_synthetic_history"
        ):
            gen = node
            break
    assert gen is not None, (
        "synthetic_history.generate_synthetic_history not found."
    )

    # Does the function compare a len(...) against a numeric constant? (the
    # shortfall guard's predicate)
    has_len_floor_compare = False
    for node in _ast.walk(gen):
        if not isinstance(node, _ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        has_len_call = any(
            isinstance(o, _ast.Call)
            and isinstance(o.func, _ast.Name)
            and o.func.id == "len"
            for o in operands
        )
        has_numeric = any(
            isinstance(o, _ast.Constant)
            and isinstance(o.value, (int, float))
            and not isinstance(o.value, bool)
            for o in operands
        )
        if has_len_call and has_numeric:
            has_len_floor_compare = True
            break

    # Does the function raise an exception OR log at ERROR/WARNING level?
    has_raise = any(isinstance(n, _ast.Raise) for n in _ast.walk(gen))
    has_error_log = False
    for node in _ast.walk(gen):
        if isinstance(node, _ast.Call):
            f = node.func
            if (
                isinstance(f, _ast.Attribute)
                and f.attr in ("error", "warning", "exception", "critical")
                and isinstance(f.value, _ast.Name)
                and f.value.id in ("logging", "logger", "log")
            ):
                has_error_log = True
                break

    assert has_len_floor_compare and (has_raise or has_error_log), (
        "generate_synthetic_history does not surface a replay-window "
        "shortfall. The `[-125:]` slice silently truncates to whatever the "
        "fetch returned; audit finding 8 (b) requires the code to compare the "
        "available replay-day count against a documented floor and "
        "raise / log ERROR on shortfall — never run a degenerate split "
        "silently. Found: len-vs-floor comparison="
        f"{has_len_floor_compare}, raise={has_raise}, error/warning log="
        f"{has_error_log}."
    )
