"""One portfolio guard number: the compounded gap between the Bot and the If-held return.

The dashboard headline (`compute_windowed_portfolio_strip()["guard_alpha"]`, served by
`/api/strip/<window>`), the hero chart's two lines (`/api/hero-chart/<window>`) and the
Performance tile (`/api/performance?days=<window>`) must be the same quantity: the Bot return
compounded over the window, minus the If-held return compounded over the window, over every
symphony at today's values. The cumulative row keeps its own, narrower figure: only the
cumulative row's return leaves out a symphony without a Composer cost basis.

Expected values are never typed in. The golden value is recomputed in this file, from the raw
`shadow_history` rows of the committed fixture, through the formula the audit states (last row per
symphony and day; per day the value-weighted Bot and If-held returns over the symphonies that have a
row that day; each series compounded; the two compounded returns subtracted). Fixture provenance:
`tests/fixtures/math/guard_alpha_windowed_basis_mix.json` carries its own `_provenance`; the weights
are the symphonies' `value` fields in that file.
"""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

import analytics
import app as app_module
import database
from tests.analytics.test_windowed_strip import (
    _epoch_additive_alpha,
    _make_db,
    _sym_dict,
    _twr_fallback_sym_dict,
)

_FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "math" / "guard_alpha_windowed_basis_mix.json"
)


def compounded_gap(series_by_symphony: dict[str, list[dict]], weights: dict[str, float]):
    """Bot minus If-held, each compounded over the days given, in percentage points.

    series_by_symphony: symphony id -> shadow_history rows (trading_day, ts_utc, shadow_return,
    current_return). None when fewer than two distinct days hold a weighted row.
    """
    per_day: dict[str, dict[str, tuple[float, float]]] = {}
    for symphony_id, rows in series_by_symphony.items():
        last: dict[str, dict] = {}
        for row in rows:
            held = last.get(row["trading_day"])
            if held is None or row["ts_utc"] > held["ts_utc"]:
                last[row["trading_day"]] = row
        for day, row in last.items():
            per_day.setdefault(day, {})[symphony_id] = (row["shadow_return"], row["current_return"])
    bot_level = held_level = 1.0
    days = 0
    for day in sorted(per_day):
        total = sum(weights[s] for s in per_day[day] if weights.get(s, 0.0) > 0.0)
        if total <= 0.0:
            continue
        bot = sum(per_day[day][s][0] * weights[s] for s in per_day[day] if weights.get(s, 0.0) > 0)
        held = sum(per_day[day][s][1] * weights[s] for s in per_day[day] if weights.get(s, 0.0) > 0)
        bot_level *= 1.0 + (bot / total) / 100.0
        held_level *= 1.0 + (held / total) / 100.0
        days += 1
    if days < 2:
        return None
    return (bot_level - held_level) * 100.0


@pytest.fixture(scope="module")
def basis_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _scenario_inputs(basis_fixture, tmp_path):
    norm = basis_fixture["scenarios"]["normal_symphony"]
    twr = basis_fixture["scenarios"]["twr_fallback_symphony"]
    db_file = _make_db(tmp_path, {"norm": norm, "twr": twr})
    norm_sym = _sym_dict(norm["symphony_id"], norm["if_held_pct"], norm["value"])
    norm_sym["net_deposits"] = norm["net_deposits"]
    twr_sym = _twr_fallback_sym_dict(twr["symphony_id"], twr["value"], twr["twr_pct"])
    state = {
        norm["symphony_id"]: {"name": "norm", "current_value": norm["value"]},
        twr["symphony_id"]: {"name": "twr", "current_value": twr["value"]},
    }
    weights = {norm["symphony_id"]: norm["value"], twr["symphony_id"]: twr["value"]}
    series = {
        norm["symphony_id"]: norm["shadow_history_rows"],
        twr["symphony_id"]: twr["shadow_history_rows"],
    }
    return norm, twr, db_file, [norm_sym, twr_sym], state, weights, series


class TestHeadlineIsTheCompoundedGapOverEverySymphony:
    def test_guard_alpha_is_the_compounded_bot_minus_if_held_gap(self, basis_fixture, tmp_path):
        norm, twr, db_file, symphonies, state, weights, series = _scenario_inputs(
            basis_fixture, tmp_path
        )
        expected = compounded_gap(series, weights)
        # Fixture sanity: the old figures (the additive sum over the symphonies without the
        # no-cost-basis one, and the unweighted-by-compounding sum over both) must differ from
        # the golden, or this test cannot tell the new formula from either.
        old_ex_crypto = _epoch_additive_alpha(norm["shadow_history_rows"])
        assert expected != pytest.approx(old_ex_crypto, abs=1e-3), "fixture cannot tell them apart"

        strip = analytics.compute_windowed_portfolio_strip(
            symphonies, state, window="all", db_path=db_file, state=state
        )

        assert strip["guard_alpha"] is not None, "the headline has a value over a 5-day window"
        assert strip["guard_alpha"] == pytest.approx(expected, abs=1e-6), (
            f"guard_alpha {strip['guard_alpha']} is not the compounded Bot minus If-held gap "
            f"{expected}: the symphony without a Composer cost basis is still left out, or the "
            "daily gaps are summed instead of the two returns being compounded"
        )

    def test_the_symphony_without_a_cost_basis_is_in_the_headline(self, basis_fixture, tmp_path):
        """Dropping the TWR-fallback symphony changes the gap; the headline must be the one with it."""
        norm, twr, db_file, symphonies, state, weights, series = _scenario_inputs(
            basis_fixture, tmp_path
        )
        without = compounded_gap({norm["symphony_id"]: series[norm["symphony_id"]]}, weights)
        with_all = compounded_gap(series, weights)
        assert with_all != pytest.approx(without, abs=1e-3), "fixture cannot tell them apart"
        strip = analytics.compute_windowed_portfolio_strip(
            symphonies, state, window="all", db_path=db_file, state=state
        )
        assert strip["guard_alpha"] != pytest.approx(without, abs=1e-6), (
            "the headline is still the figure without the symphony that has no Composer cost basis"
        )

    def test_the_cumulative_row_keeps_its_narrower_basis(self, basis_fixture, tmp_path):
        """Only the cumulative row's return leaves out the no-cost-basis symphony."""
        norm, twr, db_file, symphonies, state, weights, series = _scenario_inputs(
            basis_fixture, tmp_path
        )
        strip = analytics.compute_windowed_portfolio_strip(
            symphonies, state, window="all", db_path=db_file, state=state
        )
        expected_if_held = analytics.get_portfolio_cumulative_return(
            symphonies, state, db_path=db_file
        )["if_held"]
        norm_divergence = _epoch_additive_alpha(norm["shadow_history_rows"])
        cr = strip["cumulative_return"]
        assert cr["dry_run"] == pytest.approx(expected_if_held + norm_divergence, abs=1e-6), (
            "the cumulative row's bot return must stay if-held plus the windowed alpha of the "
            "symphonies its if-held anchor holds"
        )
        assert cr["dry_run"] - cr["if_held"] != pytest.approx(strip["guard_alpha"], abs=1e-6), (
            "the cumulative row's alpha and the headline must be different quantities here"
        )

    def test_the_headline_does_not_wait_for_thirty_days(self, basis_fixture, tmp_path):
        """The vol and drawdown legs need 30 days; the guard effect needs two."""
        norm, twr, db_file, symphonies, state, weights, series = _scenario_inputs(
            basis_fixture, tmp_path
        )
        strip = analytics.compute_windowed_portfolio_strip(
            symphonies, state, window="all", db_path=db_file, state=state
        )
        assert strip["insufficient_history"] is True, "the fixture holds five days"
        assert strip["vol_bot"] is None and strip["max_drawdown"]["dry_run"] is None
        assert strip["guard_alpha"] is not None

    def test_fewer_than_two_days_in_the_window_is_none_not_zero(self, tmp_path):
        """The route's day-1 fallback is gated on None; a fabricated 0.0 would hide it."""
        rows = [
            {
                "trading_day": "2026-06-01",
                "ts_utc": "2026-06-01T20:00:00Z",
                "shadow_return": 1.0,
                "current_return": 2.0,
                "position_epoch": "E",
            }
        ]
        scen = {"symphony_id": "oneDaySym", "shadow_history_rows": rows}
        db_file = _make_db(tmp_path, {"a": scen})
        state = {"oneDaySym": {"name": "one", "current_value": 1000.0}}
        strip = analytics.compute_windowed_portfolio_strip(
            [_sym_dict("oneDaySym", 5.0, 1000.0)], state, window="all", db_path=db_file, state=state
        )
        assert strip["guard_alpha"] is None, (
            f"one day of rows gave {strip['guard_alpha']!r}; the day-1 fallback needs None"
        )

    def test_the_window_bounds_the_gap(self, tmp_path):
        """Only the days inside the window are compounded; older days with a large gap are out."""
        today = date.today()
        weekdays: list[date] = []
        d = today
        while len(weekdays) < 8:
            if d.weekday() < 5:
                weekdays.append(d)
            d -= timedelta(days=1)
        recent = sorted(weekdays)
        old = [today - timedelta(days=90 + i) for i in range(0, 4)]
        rows = []
        for i, day in enumerate(old):
            rows.append(("2oldSym", day, 9.0, 1.0))  # a large gap that must stay outside 30d
        for i, day in enumerate(recent):
            rows.append(("2oldSym", day, 0.5 + 0.1 * i, 0.4))
        db_file = str(tmp_path / "windowed.db")
        conn = sqlite3.connect(db_file)
        conn.execute(
            "CREATE TABLE shadow_history (id INTEGER PRIMARY KEY AUTOINCREMENT, symphony_id TEXT NOT NULL,"
            " ts_utc TEXT NOT NULL, trading_day TEXT NOT NULL, current_return REAL NOT NULL,"
            " shadow_return REAL NOT NULL, is_post_trigger INTEGER NOT NULL DEFAULT 0, position_epoch TEXT)"
        )
        for sid, day, shadow, current in rows:
            conn.execute(
                "INSERT INTO shadow_history (symphony_id, ts_utc, trading_day, current_return, "
                "shadow_return, position_epoch) VALUES (?,?,?,?,?, 'E')",
                (sid, f"{day.isoformat()}T20:00:00Z", day.isoformat(), current, shadow),
            )
        conn.commit()
        conn.close()
        database._shadow_cr_cache.clear()
        state = {"2oldSym": {"name": "w", "current_value": 1000.0}}
        in_window = [
            {
                "trading_day": day.isoformat(),
                "ts_utc": f"{day.isoformat()}T20:00:00Z",
                "shadow_return": shadow,
                "current_return": current,
            }
            for sid, day, shadow, current in rows
            if day >= today - timedelta(days=30)
        ]
        expected = compounded_gap({"2oldSym": in_window}, {"2oldSym": 1000.0})
        strip = analytics.compute_windowed_portfolio_strip(
            [_sym_dict("2oldSym", 5.0, 1000.0)], state, window="30d", db_path=db_file, state=state
        )
        assert strip["guard_alpha"] == pytest.approx(expected, abs=1e-6)
        assert not math.isclose(strip["guard_alpha"], 0.0, abs_tol=1e-9), "fixture has a real gap"


# ---------------------------------------------------------------------------
# The three surfaces agree (real routes)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _recent_weekdays(count: int) -> list[date]:
    days, d = [], date.today() - timedelta(days=1)
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return sorted(days)


def _seed_three_symphonies(monkeypatch):
    """Three symphonies, one of them without a Composer cost basis, diverging on different days."""
    import os

    from tests.ui.test_static_js_page_harness import seed_shadow_rows, shadow_row

    days = [d.isoformat() for d in _recent_weekdays(12)]
    ids = {"routeSymA": 5000.0, "routeSymB": 3000.0, "routeSymC": 2000.0}
    rows = []
    for i, day in enumerate(days):
        rows.append(shadow_row("routeSymA", day, 0.30 - 0.02 * i, 0.30 - 0.02 * i))
        rows.append(
            shadow_row("routeSymB", day, 0.50 if i % 2 else -0.40, 0.20 if i % 2 else -0.40)
        )
        rows.append(shadow_row("routeSymC", day, 1.10 - 0.3 * i, 0.10 - 0.1 * i))
    seed_shadow_rows(os.environ["DB_PATH"], rows)
    state = {
        sid: {
            "name": sid,
            "current_value": value,
            "current_return": 0.0,
            "simple_return": 0.0 if sid == "routeSymC" else 0.05,
            "net_deposits": 0.0 if sid == "routeSymC" else 1000.0,
            "time_weighted_return": 0.05,
            "max_drawdown": 0.05,
        }
        for sid, value in ids.items()
    }
    database.save_state(state)
    monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: "open")
    return days, ids, rows


def test_dashboard_chart_and_performance_report_the_same_figure(client, monkeypatch):
    days, ids, rows = _seed_three_symphonies(monkeypatch)
    strip = client.get("/api/strip/30d").get_json()
    chart = client.get("/api/hero-chart/30d").get_json()
    perf = client.get("/api/performance?days=30d").get_json()

    chart_gap = chart["hist_bot"][-1] - chart["hist_held"][-1]
    perf_gap = (perf["shadow_metrics"]["total_return"] - perf["live_metrics"]["total_return"]) * 100

    assert strip["guard_alpha"] is not None
    assert strip["guard_alpha"] == pytest.approx(chart_gap, abs=5e-4), (
        "the headline is not the gap between the hero chart's two lines"
    )
    assert strip["guard_alpha"] == pytest.approx(perf_gap, abs=5e-4), (
        "the dashboard headline and the Performance tile disagree for the same window"
    )
    by_symphony = {}
    for r in rows:
        by_symphony.setdefault(r["symphony_id"], []).append(r)
    golden = compounded_gap(by_symphony, ids)
    assert strip["guard_alpha"] == pytest.approx(golden, abs=5e-4)


def test_after_the_close_the_headline_still_equals_the_chart_gap(monkeypatch, tmp_path):
    """The frozen branch computes the headline from the same cut as the chart."""
    from tests.app.test_closed_market_numbers_never_move import (
        WEDNESDAY_TO_THURSDAY,
        build_session,
    )

    world, _composer = build_session(monkeypatch, tmp_path, WEDNESDAY_TO_THURSDAY)
    strip = world.poll("/api/strip/30d")
    chart = world.poll("/api/hero-chart/30d")
    assert strip["guard_alpha"] is not None
    assert strip["guard_alpha"] == pytest.approx(
        chart["hist_bot"][-1] - chart["hist_held"][-1], abs=5e-4
    ), "after the close the headline and the chart read different cuts"
