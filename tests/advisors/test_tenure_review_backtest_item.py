"""The realized-against-backtest item.

Module under test: advisors.tenure_review (backtest_comparison and build_review).

Backtest values are COUNTERFACTUAL. They are read from the per-day portfolio values
(dollars), never from BacktestResult.daily_returns (fraction scale, so a 100x error
waiting to happen) and never from the response's whole-history stats block. Gaps are
realized minus backtest in percentage points, so a symphony trailing its backtest has
a negative gap.

Failure text comes from the real run_backtest, with only requests.post patched.
"""

from __future__ import annotations

import dataclasses
import json
import math
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

import advisors.tenure_review as tr
from advisors import composer_backtest_client as client
from tests.advisors import _tenure_review_builders as b

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "composer" / "backtest_response_v1.json"
)
COUNTERFACTUAL_LABEL = "counterfactual: Composer backtest of today's definition"

REALIZED_TOTAL = 5.0  # percent
REALIZED_DRAWDOWN = -3.0  # percent


@pytest.fixture(scope="module")
def captured():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def parsed(captured):
    """The captured Composer response parsed by the real client parser."""
    return client._parse_response(captured, captured["_symphony_id"])  # noqa: SLF001


@pytest.fixture(scope="module")
def matched(parsed):
    """The captured result with its last_market_day set to the last captured value
    (the fixture truncates the series but keeps the full-history last day)."""
    last = max(parsed.daily_portfolio_values)
    return dataclasses.replace(parsed, last_market_day=last)


def _window_of(result):
    dates = sorted(result.daily_portfolio_values)
    return {"first": dates[0], "last": dates[-1], "days": len(dates)}


def _compare(window, backtest, **overrides):
    kwargs = {
        "window": window,
        "realized_total_return": REALIZED_TOTAL,
        "realized_max_drawdown": REALIZED_DRAWDOWN,
        "backtest": backtest,
    }
    kwargs.update(overrides)
    return tr.backtest_comparison(**kwargs)


def _oracle(values_by_date, first, last):
    keys = [k for k in sorted(values_by_date) if first <= k <= last]
    vals = [values_by_date[k] for k in keys]
    total = (vals[-1] / vals[0] - 1) * 100
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return total, mdd * 100


# ---------------------------------------------------------------------------
# A successful comparison
# ---------------------------------------------------------------------------


def test_both_legs_and_both_gaps_come_from_the_per_day_values_in_percent(matched):
    window = _window_of(matched)
    total, mdd = _oracle(matched.daily_portfolio_values, window["first"], window["last"])
    item = _compare(window, matched)
    assert item["status"] == "available"
    assert item["reason"] is None
    assert item["label"] == COUNTERFACTUAL_LABEL
    assert item["window"] == window
    figs = item["figures"]
    assert figs["realized_total_return"]["value"] == REALIZED_TOTAL
    assert figs["realized_max_drawdown"]["value"] == REALIZED_DRAWDOWN
    # plain float arithmetic on the same numbers; 1e-9 only absorbs expression order
    assert figs["backtest_total_return"]["value"] == pytest.approx(total, rel=1e-9)
    assert figs["backtest_max_drawdown"]["value"] == pytest.approx(mdd, rel=1e-9)
    assert figs["total_return_gap"]["value"] == pytest.approx(REALIZED_TOTAL - total, rel=1e-9)
    assert figs["max_drawdown_gap"]["value"] == pytest.approx(REALIZED_DRAWDOWN - mdd, rel=1e-9)
    for name in (
        "realized_total_return",
        "realized_max_drawdown",
        "backtest_total_return",
        "backtest_max_drawdown",
    ):
        assert figs[name]["unit"] == "percent", name
    for name in ("total_return_gap", "max_drawdown_gap"):
        assert figs[name]["unit"] == "percentage points", name


@pytest.mark.parametrize("realized", [-40.0, 0.0, 40.0])
def test_the_gap_is_realized_minus_backtest_so_trailing_is_negative(matched, realized):
    window = _window_of(matched)
    total, _ = _oracle(matched.daily_portfolio_values, window["first"], window["last"])
    item = _compare(window, matched, realized_total_return=realized)
    gap = item["figures"]["total_return_gap"]["value"]
    assert gap == pytest.approx(realized - total, rel=1e-9, abs=1e-9)
    assert (gap < 0) == (realized < total)


def test_a_realized_zero_is_a_value_not_a_missing_figure(matched):
    item = _compare(
        _window_of(matched), matched, realized_total_return=0.0, realized_max_drawdown=0.0
    )
    assert item["status"] == "available"
    assert item["figures"]["realized_total_return"]["value"] == 0.0
    assert item["figures"]["realized_max_drawdown"]["value"] == 0.0


def test_the_fraction_scale_daily_returns_and_the_whole_history_stats_are_never_read(matched):
    """Poisoning both with absurd values must change nothing."""
    window = _window_of(matched)
    clean = _compare(window, matched)
    poisoned = dataclasses.replace(
        matched,
        daily_returns={k: 123.0 for k in matched.daily_returns},
        stats={"cumulative_return": 987.0, "max_drawdown": 0.99},
    )
    assert _compare(window, poisoned) == clean


def test_values_outside_the_realized_window_do_not_enter_either_leg(matched):
    window = _window_of(matched)
    padded_values = dict(matched.daily_portfolio_values)
    padded_values["2023-12-27"] = 1.0  # before the window
    padded_values["2024-01-12"] = 1_000_000.0  # after the window
    padded = dataclasses.replace(matched, daily_portfolio_values=padded_values)
    assert _compare(window, padded) == _compare(window, matched)


def test_the_comparison_leaves_its_inputs_unchanged(matched):
    snapshot = dict(matched.daily_portfolio_values)
    window = _window_of(matched)
    window_snapshot = dict(window)
    _compare(window, matched)
    assert matched.daily_portfolio_values == snapshot
    assert window == window_snapshot


# ---------------------------------------------------------------------------
# A backtest that does not match the realized window
# ---------------------------------------------------------------------------


def test_a_different_first_day_makes_the_item_unavailable_with_no_gap(matched):
    window = _window_of(matched)
    shifted = dataclasses.replace(matched, first_day="2023-12-27")
    item = _compare(window, shifted)
    assert item["status"] == "unavailable"
    assert item["reason"] == "backtest dates do not match the realized window"
    for fig in item["figures"].values():
        assert fig["value"] is None


def test_a_different_last_day_makes_the_item_unavailable_with_no_gap(parsed):
    """The captured result as it stands: its last_market_day is the full-history day,
    which is not the last day of the window below."""
    window = _window_of(parsed)
    assert parsed.last_market_day != window["last"]
    item = _compare(window, parsed)
    assert item["status"] == "unavailable"
    assert item["reason"] == "backtest dates do not match the realized window"
    for fig in item["figures"].values():
        assert fig["value"] is None


@pytest.mark.parametrize("kept", [1, 2], ids=["one_value", "two_values"])
def test_a_window_with_too_few_values_to_score_is_unavailable_and_names_the_minimum(matched, kept):
    """The shared metric function scores a series from two returns, which is three
    values, so one value and two values are both too short. The reason says so and names
    the minimum, which is read from the function. Rejects an available item with its
    backtest legs empty and a reason that does not say how many values are needed."""
    window = _window_of(matched)
    values = {
        day: matched.daily_portfolio_values[day] for day in [window["first"], window["last"]][:kept]
    }
    item = _compare(window, dataclasses.replace(matched, daily_portfolio_values=values))
    assert item["status"] == "unavailable"
    assert item["reason"] == (
        f"backtest series too short to score: needs at least {b.MIN_VALUES_TO_SCORE} values"
    )
    for fig in item["figures"].values():
        assert fig["value"] is None


# ---------------------------------------------------------------------------
# Failure envelopes written by the real client
# ---------------------------------------------------------------------------

WINDOW = {"first": "2026-12-01", "last": "2026-12-31", "days": 21}


def _http(status, text=""):
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"Retry-After": "0"}
    resp.text = text
    resp.json.side_effect = ValueError("not json")
    return resp


def _failed(*, response=None, raises=None):
    with (
        patch("requests.post", return_value=response, side_effect=raises),
        patch("time.sleep"),
    ):
        result = client.run_backtest({"id": "any-id"}, max_retries=0)
    assert result.error, "fixture sanity: the real client must report a failure"
    return result


def _reason_for(result):
    item = tr.backtest_comparison(
        window=WINDOW,
        realized_total_return=REALIZED_TOTAL,
        realized_max_drawdown=REALIZED_DRAWDOWN,
        backtest=result,
    )
    assert item["status"] == "unavailable"
    for fig in item["figures"].values():
        assert fig["value"] is None
    return item["reason"]


def test_http_413_reads_definition_too_large():
    reason = _reason_for(_failed(response=_http(413, "request entity too large")))
    assert reason == "definition too large for Composer backtest (HTTP 413)"


@pytest.mark.parametrize("status", [400, 422])
def test_http_400_and_422_read_composer_rejected_the_backtest_with_the_code(status):
    reason = _reason_for(_failed(response=_http(status, "bad tree")))
    assert reason.startswith("Composer rejected the backtest")
    assert str(status) in reason


def test_http_429_reads_composer_rate_limit():
    assert _reason_for(_failed(response=_http(429))) == "Composer rate limit"


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_http_5xx_reads_composer_unavailable(status):
    assert _reason_for(_failed(response=_http(status, "upstream"))) == "Composer unavailable"


def test_a_timeout_reads_composer_unavailable():
    assert _reason_for(_failed(raises=requests.Timeout("slow"))) == "Composer unavailable"


def test_a_network_failure_reads_composer_unavailable():
    assert _reason_for(_failed(raises=requests.ConnectionError("down"))) == "Composer unavailable"


def test_a_200_with_a_body_that_is_not_json_reads_composer_response_unreadable():
    assert _reason_for(_failed(response=_http(200, "<html>oops</html>"))) == (
        "Composer response unreadable"
    )


def test_an_unrecognized_failure_is_still_an_unavailable_item_with_some_reason():
    unknown = dataclasses.replace(
        client.BacktestResult(stats=None, data_warnings=[]), error="something new went wrong"
    )
    reason = _reason_for(unknown)
    assert isinstance(reason, str) and reason


def test_a_definition_that_could_not_be_fetched_reads_definition_unavailable():
    item = tr.backtest_comparison(
        window=WINDOW,
        realized_total_return=REALIZED_TOTAL,
        realized_max_drawdown=REALIZED_DRAWDOWN,
        backtest=None,
        definition_available=False,
    )
    assert item["status"] == "unavailable"
    assert item["reason"] == "definition unavailable"
    for fig in item["figures"].values():
        assert fig["value"] is None


# ---------------------------------------------------------------------------
# The counterfactual never leaks into a realized figure
# ---------------------------------------------------------------------------

DAYS = b.last_n_nyse_days(b.AS_OF, 31)


def _roster(backtest):
    first = b.composer_symphony(
        "leak-1",
        "Leak One",
        DAYS,
        b.wave_returns(30, amp=0.4, drift=0.05),
        backtest=backtest,
    )
    second = b.composer_symphony("leak-2", "Leak Two", DAYS, b.wave_returns(30, amp=0.6, freq=1.3))
    return [first, second]


def _strong_backtest():
    """A synthetic backtest that compounds 1% a day over the same dates."""
    values = {d.isoformat(): 100.0 * 1.01**i for i, d in enumerate(DAYS)}
    return client.BacktestResult(
        stats={},
        data_warnings=[],
        daily_portfolio_values=values,
        first_day=DAYS[0].isoformat(),
        last_market_day=DAYS[-1].isoformat(),
    )


def test_a_backtest_changes_only_its_own_item_and_what_depends_on_it():
    without = b.review(_roster(None), as_of=b.AS_OF, threshold=30)
    with_backtest = b.review(_roster(_strong_backtest()), as_of=b.AS_OF, threshold=30)
    before = b.entry_of(without, "leak-1")
    after = b.entry_of(with_backtest, "leak-1")

    assert b.item_of(after, "backtest")["status"] == "available"
    assert b.item_of(before, "backtest")["status"] == "unavailable"
    for name in ("performance", "composite", "redundancy", "marginal", "long_vix"):
        assert b.item_of(after, name) == b.item_of(before, name), name
    assert after["basis"] == before["basis"]
    # the backtest compounds 1% a day, so the realized result trails it by far more than
    # the watch margin; without a backtest that reason cannot appear
    assert after["verdict"] == "watch"
    assert "trails its own backtest" in after["verdict_reasons"]
    assert "trails its own backtest" not in before["verdict_reasons"]
    assert "backtest" in before["missing_items"]
    assert "backtest" not in after["missing_items"]
    # the sibling without a backtest is untouched
    assert (
        b.entry_of(with_backtest, "leak-2")["evidence"] == b.entry_of(without, "leak-2")["evidence"]
    )


# ---------------------------------------------------------------------------
# The backtest leg is the shared metric function over percent returns
# ---------------------------------------------------------------------------

# Opens with a 10 percent loss and contains a day of +100 percent, with down days
# around it, so the series is unambiguously a returns path.
SHAPED_LEVELS = [100.0, 90.0, 95.0, 190.0, 171.0, 180.0]
SHAPED_DAYS = b.last_n_nyse_days(b.AS_OF, len(SHAPED_LEVELS))


def _window_over(days):
    return {"first": days[0].isoformat(), "last": days[-1].isoformat(), "days": len(days)}


def _pct_returns(levels):
    return [(later / earlier - 1.0) * 100.0 for earlier, later in zip(levels, levels[1:])]


def test_the_backtest_leg_is_the_shared_metric_function_over_the_percent_returns(monkeypatch):
    """Expected values come from the metric function on percent returns derived from the
    per-day values. A recorder proves the leg was computed by that function.

    Rejects a hand-written total return and drawdown loop, and fraction-scale returns."""
    returns = _pct_returns(SHAPED_LEVELS)
    assert returns[0] < 0 and max(returns) >= 100.0  # fixture sanity: first-day loss, +100 day
    expected = b.quantstats_percent(returns)
    calls = b.spy_on_quantstats_metrics(monkeypatch)

    item = tr.backtest_comparison(
        window=_window_over(SHAPED_DAYS),
        realized_total_return=REALIZED_TOTAL,
        realized_max_drawdown=REALIZED_DRAWDOWN,
        backtest=b.backtest_over(SHAPED_DAYS, SHAPED_LEVELS),
    )

    assert item["status"] == "available"
    assert b.was_called_with(calls, returns)
    figs = item["figures"]
    # the same function on the same inputs: 1e-9 relative absorbs only float summation order
    assert figs["backtest_total_return"]["value"] == pytest.approx(
        expected["total_return"], rel=1e-9
    )
    assert figs["backtest_max_drawdown"]["value"] == pytest.approx(
        expected["max_drawdown"], rel=1e-9
    )
    assert figs["total_return_gap"]["value"] == pytest.approx(
        REALIZED_TOTAL - expected["total_return"], rel=1e-9
    )
    assert figs["max_drawdown_gap"]["value"] == pytest.approx(
        REALIZED_DRAWDOWN - expected["max_drawdown"], rel=1e-9
    )


def test_an_unusable_backtest_level_is_dropped_like_an_unusable_realized_one(monkeypatch):
    """The move across the dropped day is kept (the realized series does the same), so
    the returns are those of the remaining levels in order."""
    levels = [100.0, 90.0, None, 190.0, 171.0, 180.0]
    kept = [v for v in levels if v is not None]
    returns = _pct_returns(kept)
    expected = b.quantstats_percent(returns)
    calls = b.spy_on_quantstats_metrics(monkeypatch)

    item = tr.backtest_comparison(
        window=_window_over(SHAPED_DAYS),
        realized_total_return=REALIZED_TOTAL,
        realized_max_drawdown=REALIZED_DRAWDOWN,
        backtest=b.backtest_over(SHAPED_DAYS, levels),
    )

    assert b.was_called_with(calls, returns)
    assert item["figures"]["backtest_total_return"]["value"] == pytest.approx(
        expected["total_return"], rel=1e-9
    )


# A window with no negative daily return has no decline, so its max drawdown is zero by
# definition. The shared function would read such a series as prices when a day is +100
# percent or more and report a false drawdown of about -90 percent, so the module does not
# ask it. A zero return is not a decline either.
NO_DOWN_DAY_LEVELS = [
    pytest.param([100.0, 200.0, 220.0, 240.0], id="plus_100_percent_then_rises"),
    pytest.param([100.0, 100.0, 200.0, 220.0], id="a_flat_day_then_plus_100_percent"),
]


@pytest.mark.parametrize("levels", NO_DOWN_DAY_LEVELS)
def test_a_backtest_window_with_no_down_day_has_a_drawdown_of_exactly_zero(levels):
    days = SHAPED_DAYS[: len(levels)]
    returns = _pct_returns(levels)
    assert min(returns) >= 0.0 and max(returns) >= 100.0  # fixture sanity: the trap shape
    item = tr.backtest_comparison(
        window=_window_over(days),
        realized_total_return=REALIZED_TOTAL,
        realized_max_drawdown=REALIZED_DRAWDOWN,
        backtest=b.backtest_over(days, levels),
    )
    assert item["status"] == "available"
    figs = item["figures"]
    assert figs["backtest_max_drawdown"]["value"] == 0.0  # a computed zero, not None
    assert figs["max_drawdown_gap"]["value"] == pytest.approx(REALIZED_DRAWDOWN, abs=1e-12)
    expected = b.quantstats_percent(returns)["total_return"]
    # the total return still comes from the shared function; its answer is right here
    assert figs["backtest_total_return"]["value"] == pytest.approx(expected, rel=1e-9)
    assert figs["backtest_total_return"]["value"] == pytest.approx(
        (levels[-1] / levels[0] - 1.0) * 100.0, rel=1e-9
    )


# ---------------------------------------------------------------------------
# Through build_review: the realized legs are the entry's own performance figures
# ---------------------------------------------------------------------------

CASE_DAYS = b.last_n_nyse_days(b.AS_OF, 61)  # 60 returns: total and annualized return differ widely
CASE_THRESHOLD = 30
TRAILS = "trails its own backtest"


def _case(realized_drift, shortfall_pp):
    """One eligible symphony and a backtest whose total return exceeds the symphony's
    realized total return by ``shortfall_pp`` percentage points, with a small wobble so
    the backtest has a drawdown of its own. The backtest covers the realized window."""
    returns = b.wave_returns(60, amp=0.4, drift=realized_drift)
    realized = b.oracle_metrics(returns)
    target_total = realized["total_return"] + shortfall_pp
    wobble = [1.0 + 0.01 * math.sin(i) for i in range(len(CASE_DAYS))]
    growth = ((1.0 + target_total / 100.0) / wobble[-1]) ** (1.0 / (len(CASE_DAYS) - 1))
    levels = [100.0 * growth**i * wobble[i] for i in range(len(CASE_DAYS))]
    symphony = b.composer_symphony(
        "case-sym", "Case Symphony", CASE_DAYS, returns, backtest=b.backtest_over(CASE_DAYS, levels)
    )
    bt_total, bt_dd = _oracle(
        {d.isoformat(): v for d, v in zip(CASE_DAYS, levels, strict=True)},
        CASE_DAYS[0].isoformat(),
        CASE_DAYS[-1].isoformat(),
    )
    return symphony, realized, bt_total, bt_dd


def _backtest_figures(symphony):
    result = b.review([symphony], as_of=b.AS_OF, threshold=CASE_THRESHOLD)
    entry = b.entry_of(result, symphony.symphony_id)
    perf = b.item_of(entry, "performance")["figures"]
    item = b.item_of(entry, "backtest")
    assert item["status"] == "available"
    return entry, perf, item["figures"]


@pytest.mark.parametrize(
    ("drift", "shortfall", "verdict", "reasons"),
    [(0.16, 12.0, "watch", [TRAILS]), (-0.18, 9.0, "keep", [])],
    ids=["trails_by_12_points", "trails_by_9_points"],
)
def test_the_backtest_item_carries_the_entrys_own_realized_legs_and_gaps_from_the_per_day_values(
    drift, shortfall, verdict, reasons
):
    """Both runs are chosen so the realized annualized return is more than the 10 point
    margin away from the realized total return, on the side that would flip the verdict
    if the annualized figure (or the drawdown) were wired into the comparison by mistake.

    Rejects realized legs taken from another figure of the performance item, a gap
    computed from anything but the realized and backtest legs, and a verdict that does
    not follow the total-return shortfall."""
    symphony, realized, bt_total, bt_dd = _case(drift, shortfall)
    entry, perf, figs = _backtest_figures(symphony)
    # fixture sanity: the wrong wiring would change the outcome
    assert abs(realized["annualized_return"] - realized["total_return"]) > 10.0
    assert abs(realized["max_drawdown"] - realized["total_return"]) > 1.0

    # the entry's own performance figures, which the oracle confirms
    assert perf["total_return"]["value"] == pytest.approx(realized["total_return"], rel=1e-9)
    assert perf["max_drawdown"]["value"] == pytest.approx(realized["max_drawdown"], rel=1e-9)
    # the same numbers reach the backtest item; 1e-12 relative: no arithmetic in between
    assert figs["realized_total_return"]["value"] == pytest.approx(
        perf["total_return"]["value"], rel=1e-12
    )
    assert figs["realized_max_drawdown"]["value"] == pytest.approx(
        perf["max_drawdown"]["value"], rel=1e-12
    )
    # the backtest legs and both gaps come from the fixture's per-day values
    assert figs["backtest_total_return"]["value"] == pytest.approx(bt_total, rel=1e-9)
    assert figs["backtest_max_drawdown"]["value"] == pytest.approx(bt_dd, rel=1e-9)
    assert figs["total_return_gap"]["value"] == pytest.approx(
        perf["total_return"]["value"] - bt_total, rel=1e-9, abs=1e-9
    )
    assert figs["max_drawdown_gap"]["value"] == pytest.approx(
        perf["max_drawdown"]["value"] - bt_dd, rel=1e-9, abs=1e-9
    )
    assert entry["verdict"] == verdict
    assert entry["verdict_reasons"] == reasons


# ---------------------------------------------------------------------------
# A backtest that reproduces the realized returns leaves no gap, on either basis
# ---------------------------------------------------------------------------

REPLICA_THRESHOLD = 5
REPLICA_SESSIONS = list(b.nyse_days(date(2026, 12, 14), b.AS_OF))


def _replica_returns(count):
    returns = b.wave_returns(count, amp=2.0)
    returns[0] = 2.5  # large enough that counting or dropping it shows as a gap
    return returns


def _composer_replica():
    returns = _replica_returns(len(REPLICA_SESSIONS) - 1)
    symphony = b.composer_symphony("rep-sym", "Replica Symphony", REPLICA_SESSIONS, returns)
    return symphony, returns


def _shadow_replica(invested_since, *, first_return_counted):
    """A shadow row on every session from 14 December. With the tenure starting before
    that, the session before the first row is the window's first day and all 13 returns
    count; with the tenure starting on the first row there is no session before it inside
    the tenure, the first row is the baseline and 12 returns count."""
    returns = _replica_returns(len(REPLICA_SESSIONS))
    rows = b.by_date(REPLICA_SESSIONS, returns)
    symphony = b.shadow_symphony("rep-sym", "Replica Symphony", invested_since.isoformat(), rows)
    return symphony, returns if first_return_counted else returns[1:]


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_composer_replica, id="composer_basis"),
        pytest.param(
            lambda: _shadow_replica(date(2026, 12, 7), first_return_counted=True),
            id="shadow_basis_with_a_baseline_session_in_the_tenure",
        ),
        pytest.param(
            lambda: _shadow_replica(date(2026, 12, 14), first_return_counted=False),
            id="shadow_basis_with_the_first_row_on_the_first_tenure_day",
        ),
    ],
)
def test_a_backtest_that_reproduces_the_realized_returns_leaves_a_gap_of_zero(build):
    """The backtest is requested over the window the review reports, so its return days
    are the realized return days. A perfect replica then differs from the realized legs by
    nothing, whichever basis the realized series came from.

    Rejects a shadow window with one more return than the backtest over it can have (a gap
    of that day's return, 2.5 points here, on a symphony that did exactly what its
    definition says)."""
    symphony, counted = build()
    window = tr.backtest_window(symphony, b.AS_OF, REPLICA_THRESHOLD)
    assert window is not None
    first, last = date.fromisoformat(window["first"]), date.fromisoformat(window["last"])
    sessions_in_window = len(list(b.nyse_days(first, last)))
    assert sessions_in_window == len(counted) + 1, "a window spans one more day than it has returns"
    symphony.backtest = b.replica_backtest(first, last, counted)

    result = b.review([symphony], as_of=b.AS_OF, threshold=REPLICA_THRESHOLD)
    item = b.item_of(b.entry_of(result, symphony.symphony_id), "backtest")
    assert item["status"] == "available"
    figs = item["figures"]
    # percentage points; 1e-9 absorbs only the rounding of rebuilding levels from percent
    # returns, far below the smallest real gap, which is one day's return
    assert figs["total_return_gap"]["value"] == pytest.approx(0.0, abs=1e-9)
    assert figs["max_drawdown_gap"]["value"] == pytest.approx(0.0, abs=1e-9)
    assert figs["realized_total_return"]["value"] == pytest.approx(
        figs["backtest_total_return"]["value"], abs=1e-9
    )


# ---------------------------------------------------------------------------
# A shadow window with sessions that have no row cannot be compared with a backtest
# ---------------------------------------------------------------------------


def _shadow_replica_with_rows_left_out(left_out, *, stored_without_return=False):
    """The shadow replica of this section, with the rows of ``left_out`` missing (or stored
    with no return) and a backtest that reproduces every session's return, the left-out ones
    included. Its window runs from the session before the first row to the last row."""
    returns = _replica_returns(len(REPLICA_SESSIONS))
    rows = b.by_date(REPLICA_SESSIONS, returns)
    for day in left_out:
        if stored_without_return:
            rows[day.isoformat()] = None
        else:
            del rows[day.isoformat()]
    symphony = b.shadow_symphony("rep-sym", "Replica Symphony", "2026-12-07", rows)
    first, last = b.session_before(REPLICA_SESSIONS[0]), REPLICA_SESSIONS[-1]
    sessions = list(b.nyse_days(first, last))
    assert len(sessions) == len(REPLICA_SESSIONS) + 1  # fixture sanity: the baseline and every row
    symphony.backtest = b.replica_backtest(first, last, returns)
    return symphony


@pytest.mark.parametrize(
    "positions",
    [[4], [3, 4, 7]],
    ids=["one_session_without_a_row", "three_sessions_without_a_row"],
)
@pytest.mark.parametrize(
    "stored_without_return", [False, True], ids=["no_row_stored", "row_stored_with_no_return"]
)
def test_a_shadow_window_with_sessions_that_have_no_usable_row_makes_the_backtest_unavailable(
    positions, stored_without_return
):
    """The realized chain skips a session with no usable return while the backtest over the
    window covers it, so a perfect replica would show a gap of that day's return. The item
    reads unavailable and the reason names each missing session by date, and only those. The
    comparison is not made. Rejects a gap shown for a symphony that did exactly what its
    definition says, and a reason that names no session."""
    missing = [REPLICA_SESSIONS[i] for i in positions]
    symphony = _shadow_replica_with_rows_left_out(
        missing, stored_without_return=stored_without_return
    )
    entry = b.entry_of(
        b.review([symphony], as_of=b.AS_OF, threshold=REPLICA_THRESHOLD), symphony.symphony_id
    )
    item = b.item_of(entry, "backtest")
    assert item["status"] == "unavailable"
    reason = item["reason"]
    for day in missing:
        assert day.isoformat() in reason, day
    for day in REPLICA_SESSIONS:
        if day not in missing:
            assert day.isoformat() not in reason, day
    for figure in item["figures"].values():
        assert figure["value"] is None
    assert "backtest" in entry["missing_items"]


def test_a_composer_series_with_a_day_missing_inside_the_window_is_still_compared_with_a_backtest():
    """The Composer series is a series of levels, so a missing interior day changes no
    endpoint and the total return over the window is the same with or without it. Rejects
    applying the shadow rule to the Composer basis."""
    symphony, returns = _composer_replica()
    symphony.composer_values[REPLICA_SESSIONS[5].isoformat()] = None
    window = tr.backtest_window(symphony, b.AS_OF, REPLICA_THRESHOLD)
    assert window is not None
    first, last = date.fromisoformat(window["first"]), date.fromisoformat(window["last"])
    assert window["days"] == len(REPLICA_SESSIONS) - 1  # fixture sanity: one day is missing
    symphony.backtest = b.replica_backtest(first, last, returns)
    item = b.item_of(
        b.entry_of(
            b.review([symphony], as_of=b.AS_OF, threshold=REPLICA_THRESHOLD), symphony.symphony_id
        ),
        "backtest",
    )
    assert item["status"] == "available"
    # percentage points; 1e-9 absorbs only the rounding of rebuilding levels from returns
    assert item["figures"]["total_return_gap"]["value"] == pytest.approx(0.0, abs=1e-9)
