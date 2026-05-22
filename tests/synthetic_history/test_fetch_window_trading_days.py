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
import pathlib as _pathlib  # noqa: E402

_SYNTH_PATH = (
    _pathlib.Path(__file__).resolve().parents[2] / "synthetic_history.py"
)


def _synth_source() -> str:
    """Raw text of synthetic_history.py — Revise-round static analysis."""
    assert _SYNTH_PATH.is_file(), f"expected production module at {_SYNTH_PATH}"
    return _SYNTH_PATH.read_text(encoding="utf-8")


def _synth_tree() -> _ast.Module:
    """Parse synthetic_history.py — Revise-round static analysis."""
    return _ast.parse(_synth_source(), filename=str(_SYNTH_PATH))


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

    Static check: ``generate_synthetic_history`` must contain a comparison of
    a ``len(...)`` of the replay-date sequence against a numeric floor — the
    floor may be a bare literal OR (preferred) a Name bound to a module-level
    numeric constant carrying the 125 + 39 + buffer derivation — AND a raise
    or an ERROR/WARNING log. We assert the robust proxy: the function body
    references both a length-vs-floor check AND a raise / ERROR-WARNING log.
    The silent bare ``[-125:]`` slice with no guard fails this.
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

    # Module-level names bound to a numeric constant. The shortfall guard
    # SHOULD compare against a NAMED floor constant (not a bare literal — the
    # floor's 125 + 39 + buffer derivation belongs in a source comment), so a
    # Compare operand that is an ast.Name resolving to one of these counts as
    # the numeric side of the floor check.
    module_numeric_consts: set[str] = set()
    for node in tree.body:
        if isinstance(node, _ast.Assign) and isinstance(
            node.value, _ast.Constant
        ):
            if isinstance(node.value.value, (int, float)) and not isinstance(
                node.value.value, bool
            ):
                for tgt in node.targets:
                    if isinstance(tgt, _ast.Name):
                        module_numeric_consts.add(tgt.id)
        # `_NAME: int = 174` annotated assignment also counts.
        if isinstance(node, _ast.AnnAssign) and isinstance(
            node.value, _ast.Constant
        ):
            if isinstance(node.value.value, (int, float)) and not isinstance(
                node.value.value, bool
            ):
                if isinstance(node.target, _ast.Name):
                    module_numeric_consts.add(node.target.id)

    def _is_numeric_floor_operand(operand: _ast.AST) -> bool:
        """A Compare operand counts as the numeric floor if it is a numeric
        literal OR a Name bound to a module-level numeric constant.

        A NAMED constant (the preferred form — it carries the derivation
        comment) and a bare literal both satisfy the floor-check contract; the
        test must not force a magic number to be reachable.
        """
        if (
            isinstance(operand, _ast.Constant)
            and isinstance(operand.value, (int, float))
            and not isinstance(operand.value, bool)
        ):
            return True
        if isinstance(operand, _ast.Name) and operand.id in module_numeric_consts:
            return True
        return False

    # Does the function compare a len(...) against a numeric floor (literal or
    # named module constant)? — the shortfall guard's predicate.
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
        has_numeric = any(_is_numeric_floor_operand(o) for o in operands)
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


def test_stale_cache_cannot_bypass_the_new_trading_day_floor() -> None:
    """A cache written under the OLD calendar-day window must not load and
    bypass the new trading-day floor.

    synthetic_history.py keys its on-disk cache as
    ``synthetic_history_v<N>_<date>_<hash>.json``. The cache marker was ``v2``
    when the fetch window was the old fixed-180-calendar-day literal. The
    Cluster-5 window-sizing change alters what a cache file represents: a
    ``v2`` file written before this fix holds a history sized under the old
    (short) window. If that stale ``v2`` file is loaded verbatim, the new
    AC-1 trading-day floor check is skipped entirely — the autotuner silently
    optimises against a short history, exactly the defect AC-1 closes, just
    routed through the cache.

    The fix must close this hole one of two ways (implementer's GREEN choice):
      (a) bump the cache marker (v2 -> v3+) so a stale v2 file cannot match
          the new cache filename and is therefore never loaded; OR
      (b) run the trading-day floor check on a cache-LOADED history too, so a
          short cached history is rejected / surfaced just like a short fetch.

    Static check: EITHER the cache filename no longer contains the literal
    ``v2`` marker, OR the cache-load branch is followed by a floor check.
    A bare ``v2`` cache key with no post-load validation fails RED.
    """
    tree = _synth_tree()
    src = _synth_source()

    # --- Path (a): the cache marker is bumped past v2. ---------------------
    # Find the cache-filename f-string / string literal and inspect its
    # version marker token. The old marker is the literal substring
    # "synthetic_history_v2".
    marker_is_stale = "synthetic_history_v2" in src

    # --- Path (b): a floor check runs on the cache-loaded history. ---------
    # The cache-load branch binds the loaded history to a name (e.g.
    # `cached = load_cached_history(...)`). Path (b) is satisfied if, after
    # such a load, the function applies a len()-vs-floor comparison to the
    # cached object before returning it. We use a robust proxy: the module
    # exposes a floor-validation helper applied to the loaded cache, OR the
    # cache-load branch contains a len() comparison.
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

    # Collect names bound from a load_cached_history(...) call.
    cache_loaded_names: set[str] = set()
    for node in _ast.walk(gen):
        if isinstance(node, _ast.Assign) and isinstance(node.value, _ast.Call):
            f = node.value.func
            is_cache_load = (
                isinstance(f, _ast.Name)
                and f.id in ("load_cached_history", "read_history_cache", "_load_cache")
            ) or (
                isinstance(f, _ast.Attribute)
                and f.attr in ("load_cached_history", "read_history_cache", "_load_cache")
            )
            if is_cache_load:
                for tgt in node.targets:
                    if isinstance(tgt, _ast.Name):
                        cache_loaded_names.add(tgt.id)

    # Path (b): is there a len() comparison referencing a cache-loaded name,
    # i.e. the cached history's day-count is validated before use?
    floor_checks_cached_history = False
    for node in _ast.walk(gen):
        if not isinstance(node, _ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for o in operands:
            if (
                isinstance(o, _ast.Call)
                and isinstance(o.func, _ast.Name)
                and o.func.id == "len"
                and o.args
            ):
                arg = o.args[0]
                # len(cached) or len(cached[...]) / len(cached.values()) etc.
                for sub in _ast.walk(arg):
                    if isinstance(sub, _ast.Name) and sub.id in cache_loaded_names:
                        floor_checks_cached_history = True

    assert (not marker_is_stale) or floor_checks_cached_history, (
        "a stale synthetic_history_v2 cache can bypass the new AC-1 "
        "trading-day floor. The cache marker is still 'v2' AND the "
        "cache-loaded history is not floor-validated before use — a cache "
        "file written under the old 180-calendar-day window would load "
        "verbatim and feed a short history to the autotuner. Fix: bump the "
        "cache marker (v2 -> v3+) so old caches cannot match, OR run the "
        "trading-day floor check on the cache-loaded history too."
    )


# ---------------------------------------------------------------------------
# BLOCKER round (risk-engine-specialist DENY on f4f645d) — two AC-1 defects
# the prior RED suite did not catch.
#
# BLOCKER 1: a hand-maintained _US_MARKET_HOLIDAYS table shipped with a drift
# error (2026-07-03 is NOT an NYSE holiday — July 4 2026 is a Saturday and the
# NYSE does not observe the preceding Friday; that is the FEDERAL rule). AC-1
# was ruled Option B specifically to AVOID a hand-maintained holiday table.
#
# BLOCKER 2: the shortfall guard checks only the 125-day intraday replay slice.
# It does NOT verify the daily-bar history clears the 39-day MC-warmup floor.
# A short daily fetch silently yields None mc_prob for the first ~39 of 125
# walk-forward folds — a silent under-floor history the autotuner optimises
# against. AC-1's text explicitly names the MC warmup floor.
# ---------------------------------------------------------------------------


def test_independence_day_2026_is_a_trading_day_not_a_holiday() -> None:
    """BLOCKER 1 — 2026-07-03 must NOT be treated as a market holiday.

    July 4, 2026 is a SATURDAY. The "observe the preceding Friday" rule is the
    US FEDERAL holiday rule — it is NOT the NYSE rule. The NYSE does not close
    the Friday before a Saturday Independence Day; the exchange is OPEN on
    Friday 2026-07-03. (The Sunday -> following-Monday observance rule does
    apply to the NYSE; the Saturday -> preceding-Friday rule does not.)

    If synthetic_history.py exposes a holiday table, 2026-07-03 must not be in
    it — a busday calculation that excludes a real trading day drifts the
    fetch window. If the table has been removed entirely (the preferred AC-1
    Option-B resolution), this test is trivially satisfied — there is no table
    to carry the drift.
    """
    synth = pytest.importorskip(
        "synthetic_history",
        reason="synthetic_history import unavailable in this environment",
    )
    holidays = getattr(synth, "_US_MARKET_HOLIDAYS", None)
    if holidays is None:
        # Table removed — AC-1 Option B as ruled. No hand-maintained holiday
        # table means no drift class. Nothing to assert.
        return

    # A table still exists — it must not misclassify 2026-07-03.
    jul3 = np.datetime64("2026-07-03", "D")
    holidays_arr = np.asarray(holidays, dtype="datetime64[D]")
    assert jul3 not in set(holidays_arr.tolist()), (
        "synthetic_history._US_MARKET_HOLIDAYS lists 2026-07-03 as a market "
        "holiday. July 4 2026 is a Saturday; the NYSE does NOT close the "
        "preceding Friday (that is the federal-observance rule, not the NYSE "
        "rule) — 2026-07-03 is a real trading day. Remove the entry, or "
        "remove the hand-maintained table entirely per the AC-1 Option-B "
        "ruling."
    )
    # Stronger: with that table as the holiday set, 2026-07-03 must be a
    # business day.
    assert bool(
        np.is_busday(jul3, holidays=holidays_arr)
    ), (
        "with _US_MARKET_HOLIDAYS as the holiday set, np.is_busday reports "
        "2026-07-03 as a non-trading day. It is a real NYSE trading day."
    )


def test_fetch_window_count_does_not_depend_on_a_hand_maintained_holiday_table() -> None:
    """BLOCKER 1 (deeper) — the trading-day floor must not be enforced by a
    hand-maintained holiday table.

    AC-1 was ruled Option B precisely to AVOID a hand-maintained holiday
    table: Alpaca returns daily bars only on real trading days, so the
    returned-bar count IS the exchange calendar — self-validating, no drift
    class. A hand-listed ``_US_MARKET_HOLIDAYS`` constant reintroduces exactly
    the drift the ruling excluded (and shipped with a 2026-07-03 error).

    Contract: ``synthetic_history`` must NOT carry a hand-maintained holiday
    table that drives the trading-day count. The fetch-window START may be
    picked by generous calendar-day padding; the AUTHORITY for the floor is
    the actual returned-bar count plus the post-fetch floor checks — not a
    holiday list.

    This test fails RED while ``_US_MARKET_HOLIDAYS`` exists; it passes once
    the table is removed.
    """
    synth = pytest.importorskip(
        "synthetic_history",
        reason="synthetic_history import unavailable in this environment",
    )
    assert not hasattr(synth, "_US_MARKET_HOLIDAYS"), (
        "synthetic_history still defines a hand-maintained _US_MARKET_HOLIDAYS "
        "table. AC-1 was ruled Option B to AVOID a hand-maintained holiday "
        "table — the returned Alpaca bar count is the self-validating "
        "exchange calendar. The table already shipped a drift error "
        "(2026-07-03). Remove it: pick the fetch-window start by generous "
        "calendar-day padding and let the returned-bar count + the post-fetch "
        "floor checks be the sole authority for the trading-day floor."
    )


def test_daily_warmup_shortfall_is_surfaced_not_only_the_intraday_slice() -> None:
    """BLOCKER 2 — the shortfall guard must also check the daily / MC-warmup
    floor, not only the 125-day intraday replay slice.

    ``_REQUIRED_FETCH_TRADING_DAYS`` is 174 = 125 walk-forward + 39 MC warmup
    + 10 buffer. The 39-day warmup is the daily history that PRECEDES the
    replay window: ``build_replay_day`` feeds ``hist_data_up_to_yesterday``
    into ``run_monte_carlo``, and on the earliest replay days that prior daily
    history must be >= the MC warmup floor or ``run_monte_carlo`` returns the
    None insufficient sentinel. If Alpaca under-delivers the DAILY bars,
    ``daily_dates`` is silently short and the first ~39 of 125 walk-forward
    folds get None mc_prob — the MC exit gate is dark for ~31% of the
    validation window and the autotuner tunes the MC-coupled parameters
    against a systematically MC-blind replay.

    The current guard checks only ``len(intraday_dates)`` against the 125-day
    floor. AC-1's text explicitly requires the window to "guarantee at least
    ... the MC warmup floor (MC_MIN_HISTORY_DAYS + MC_VOL_WINDOW_DAYS - 1)" —
    so a guard that verifies the 125 but not the 39 half-implements AC-1.

    Static check: ``generate_synthetic_history`` must contain a SECOND
    length-vs-floor comparison, distinct from the intraday-slice guard, that
    checks the DAILY history (a ``len(...)`` of the daily-date sequence /
    daily history) against the MC-warmup floor — feeding a raise or an
    ERROR/WARNING log. We detect it as: a ``len()`` comparison whose len()
    argument references a daily-history name AND at least two distinct
    length-vs-floor comparisons total (the intraday guard + the daily guard).
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

    # Names that plausibly hold the daily-bar history / its date sequence.
    # The current code names it `daily_dates` (sorted historical_daily keys)
    # and `historical_daily`.
    daily_history_names = {"daily_dates", "historical_daily", "daily_bars_raw"}

    # Walk every Compare; classify each len()-vs-floor comparison by whether
    # its len() argument touches a daily-history name or an intraday name.
    intraday_floor_checks = 0
    daily_floor_checks = 0
    for node in _ast.walk(gen):
        if not isinstance(node, _ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        len_args: list[_ast.AST] = []
        has_floor_operand = False
        for o in operands:
            if (
                isinstance(o, _ast.Call)
                and isinstance(o.func, _ast.Name)
                and o.func.id == "len"
                and o.args
            ):
                len_args.append(o.args[0])
            # numeric literal or a module-level numeric constant Name
            if (
                isinstance(o, _ast.Constant)
                and isinstance(o.value, (int, float))
                and not isinstance(o.value, bool)
            ):
                has_floor_operand = True
            if isinstance(o, _ast.Name) and (
                o.id.isupper()
                or o.id.startswith("_")
                and any(c.isupper() for c in o.id)
            ):
                # heuristic: a NAMED floor constant (e.g. _MC_WARMUP_TRADING_DAYS)
                has_floor_operand = True
        if not (len_args and has_floor_operand):
            continue
        for arg in len_args:
            names = {
                n.id for n in _ast.walk(arg) if isinstance(n, _ast.Name)
            }
            if names & daily_history_names:
                daily_floor_checks += 1
            elif any("intraday" in n for n in names):
                intraday_floor_checks += 1
            else:
                # An unclassified len()-vs-floor check — count it toward
                # neither; the test needs an explicitly daily-scoped one.
                pass

    assert daily_floor_checks >= 1, (
        "generate_synthetic_history surfaces an intraday-slice shortfall but "
        "NOT a daily / MC-warmup shortfall. The 39-day MC warmup is the "
        "reason the fetch floor is 174 (not 125); a short DAILY fetch leaves "
        "the first ~39 walk-forward folds MC-dark with nothing surfaced. "
        "AC-1 requires the MC warmup floor to be guaranteed. Add a daily-side "
        "floor check parallel to the intraday guard: compare the available "
        "daily history (len of daily_dates / historical_daily) against the "
        "MC-warmup floor and raise / log ERROR on shortfall. "
        f"Found {intraday_floor_checks} intraday floor check(s), "
        f"{daily_floor_checks} daily floor check(s)."
    )
