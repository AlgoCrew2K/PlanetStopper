"""RED — ONE canonical value-weighted portfolio series for Performance + Overview.

THE BUG (VERDICT-droplet.md Findings 4 + 6, HIGH — three surfaces, three stories):
  On the same production data the Performance tab said bot +3.1%/held +4.5% while
  the Overview hero chart said bot -3.43%/held -4.75%; an honest equal-weight
  recompute said -0.85%/-2.23%. Neither surface is a portfolio series:
    * /api/performance (aggregate) builds its series exclusively from post-mortem
      ``triggers`` arrays — only symphonies that triggered that day contribute, at
      their exit-moment snapshot; zero-trigger days vanish entirely. A selection-
      biased event sample presented as portfolio performance.
    * The hero chart series (analytics.get_portfolio_bot_and_held_daily_returns)
      weights each symphony by abs(current_return) — the day's biggest movers
      dominate, exaggerating levels ~4x. Real position values exist in
      bot_state.current_value and are used by other consumers, but not here.

CONTRACT PINNED:
  1. The portfolio daily series is VALUE-weighted: per trading day, over the last
     shadow_history row of each symphony, ret = sum(value_i * r_i) / sum(value_i),
     with value_i = bot_state[sym]["current_value"]; bot uses shadow_return, held
     uses current_return, both under the SAME weights.
  2. When no position values are available (day-1 droplet), the series degrades to
     EQUAL weight — never to the abs(return) proxy.
  3. /api/performance (scope=aggregate) serves THAT series: every shadow_history
     trading day appears (including zero-trigger days), and compounding its daily
     returns reproduces the hero chart's curve exactly — one series, two surfaces.

FIXTURE PROVENANCE (captured-from-producer):
  tests/fixtures/prod_droplet/ — the real droplet's last shadow_history row per
  (symphony, trading_day) (143 rows, 13 days, 11 symphonies), real position values
  from the bot_state blob, and the 12 real post-mortem files, captured at deployed
  SHA 0bcbd1a (see capture_from_droplet.py). Expected series are recomputed from
  those rows inside the tests — never hardcoded.
"""

from __future__ import annotations

import json
import os
import shutil
from collections import defaultdict
from pathlib import Path

import pytest

import analytics
import app as app_module
import database

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "prod_droplet"

# Hero route rounds each compounded point to 4dp; a 13-point compounding chain
# accumulates at most a few 1e-4 rounding steps.
_SERIES_ABS_TOL = 1e-3


@pytest.fixture(scope="module")
def shadow_last_rows() -> list[dict]:
    return json.loads((_FIXTURE_DIR / "shadow_last_rows.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def positions() -> dict:
    return json.loads((_FIXTURE_DIR / "bot_state_positions.json").read_text(encoding="utf-8"))


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def seeded_shadow_db(shadow_last_rows, monkeypatch):
    """Seed the per-test DB with the real last-row-per-(symphony, day) set and
    point the analytics module-attribute reader at the same file the env-based
    writers use (database._db_file honours DB_PATH; analytics._get_shadow_db_file
    reads database.DB_FILE)."""
    db_path = os.environ["DB_PATH"]
    monkeypatch.setattr(database, "DB_FILE", db_path)
    for r in shadow_last_rows:
        database.record_shadow_observation(
            symphony_id=r["symphony_id"],
            account_id=r["account_id"],
            cycle_id=r["cycle_id"],
            ts_utc=r["ts_utc"],
            ts_et=r["ts_et"],
            trading_day=r["trading_day"],
            current_return=r["current_return"],
            shadow_return=r["shadow_return"],
            is_post_trigger=r["is_post_trigger"],
            trigger_id=None,
            position_epoch=r["position_epoch"],
        )
    return db_path


@pytest.fixture
def pm_dir(tmp_path, monkeypatch) -> Path:
    pm = tmp_path / "post_mortems"
    pm.mkdir()
    for src in (_FIXTURE_DIR / "post_mortems").glob("post_mortem_*.json"):
        shutil.copy(src, pm)
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(pm))
    return pm


# ---------------------------------------------------------------------------
# Expected-series derivation (pure recompute from the fixture rows)
# ---------------------------------------------------------------------------


def _daily_series(shadow_last_rows: list[dict], weights: dict[str, float] | None):
    """(dates, bot_daily, held_daily) with explicit weights, or equal weight
    when weights is None/empty."""
    day_map: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in shadow_last_rows:
        day_map[r["trading_day"]][r["symphony_id"]] = (
            r["shadow_return"],
            r["current_return"],
        )
    dates, bot, held = [], [], []
    for day in sorted(day_map):
        w_sum = b_sum = h_sum = 0.0
        for sym_id, (sr, cr) in day_map[day].items():
            w = 1.0 if not weights else weights.get(sym_id, 0.0)
            if w <= 0.0:
                continue
            w_sum += w
            b_sum += sr * w
            h_sum += cr * w
        if w_sum > 0.0:
            dates.append(day)
            bot.append(b_sum / w_sum)
            held.append(h_sum / w_sum)
    return dates, bot, held


def _abs_return_series(shadow_last_rows: list[dict]):
    """The DEFECTIVE weighting (abs current_return proxy) — used only to prove
    the fixture data distinguishes it from the correct weightings."""
    day_map: dict[str, dict[str, tuple[float, float]]] = defaultdict(dict)
    for r in shadow_last_rows:
        day_map[r["trading_day"]][r["symphony_id"]] = (
            r["shadow_return"],
            r["current_return"],
        )
    bot = []
    for day in sorted(day_map):
        w_sum = b_sum = 0.0
        for sr, cr in day_map[day].values():
            w = abs(cr) if cr != 0.0 else 1.0
            w_sum += w
            b_sum += sr * w
        bot.append(b_sum / w_sum if w_sum else 0.0)
    return bot


def _compound(daily: list[float]) -> list[float]:
    running, out = 1.0, []
    for d in daily:
        running *= 1.0 + d / 100.0
        out.append((running - 1.0) * 100.0)
    return out


def _values(positions: dict) -> dict[str, float]:
    return {sym_id: entry["current_value"] for sym_id, entry in positions.items()}


def _assert_series_close(got: list, expected: list, label: str) -> None:
    assert len(got) == len(expected), f"{label}: {len(got)} points != expected {len(expected)}"
    for i, (g, e) in enumerate(zip(got, expected)):
        assert g == pytest.approx(e, abs=_SERIES_ABS_TOL), (
            f"{label}[{i}]: {g} != expected {e} (value-weighted recompute from the "
            "captured shadow_history rows)"
        )


# ---------------------------------------------------------------------------
# Fixture-integrity anchor: on this real data the three weightings genuinely
# differ, so a wrong weighting cannot silently pass.
# ---------------------------------------------------------------------------


def test_fixture_distinguishes_value_equal_and_abs_return_weighting(shadow_last_rows, positions):
    _, vw_bot, _ = _daily_series(shadow_last_rows, _values(positions))
    _, eq_bot, _ = _daily_series(shadow_last_rows, None)
    abs_bot = _abs_return_series(shadow_last_rows)
    assert max(abs(a - b) for a, b in zip(abs_bot, vw_bot)) > 10 * _SERIES_ABS_TOL, (
        "fixture integrity: abs-return weighting must be distinguishable from "
        "value weighting on the captured data"
    )
    assert max(abs(a - b) for a, b in zip(abs_bot, eq_bot)) > 10 * _SERIES_ABS_TOL, (
        "fixture integrity: abs-return weighting must be distinguishable from "
        "equal weighting on the captured data"
    )


# ---------------------------------------------------------------------------
# 1. Hero chart weights by position value
# ---------------------------------------------------------------------------


class TestHeroChartIsValueWeighted:
    def test_hero_series_compounds_the_value_weighted_daily_returns(
        self, client, seeded_shadow_db, positions
    ):
        """/api/hero-chart/all must serve the value-weighted portfolio series.

        RED today: analytics weights by abs(current_return), so big-mover days
        dominate and the curve's levels are exaggerated (audit: -3.43% shown vs
        -0.85% honest over the same window).
        """
        database.save_state(
            {
                sym_id: {"name": entry["name"], "current_value": entry["current_value"]}
                for sym_id, entry in positions.items()
            }
        )
        shadow_last_rows = json.loads(
            (_FIXTURE_DIR / "shadow_last_rows.json").read_text(encoding="utf-8")
        )
        dates, bot_daily, held_daily = _daily_series(shadow_last_rows, _values(positions))

        resp = client.get("/api/hero-chart/all")
        assert resp.status_code == 200
        body = resp.get_json()

        assert body["hist_dates"] == dates
        _assert_series_close(body["hist_bot"], _compound(bot_daily), "hist_bot")
        _assert_series_close(body["hist_held"], _compound(held_daily), "hist_held")

    def test_hero_series_degrades_to_equal_weight_without_position_values(
        self, client, seeded_shadow_db, shadow_last_rows
    ):
        """With no position values available (empty bot_state), the series must be
        EQUAL-weighted — the honest fallback — never the abs(return) proxy.
        """
        database.save_state({})
        dates, bot_daily, held_daily = _daily_series(shadow_last_rows, None)

        resp = client.get("/api/hero-chart/all")
        assert resp.status_code == 200
        body = resp.get_json()

        assert body["hist_dates"] == dates
        _assert_series_close(body["hist_bot"], _compound(bot_daily), "hist_bot(equal)")
        _assert_series_close(body["hist_held"], _compound(held_daily), "hist_held(equal)")


# ---------------------------------------------------------------------------
# 2 + 3. Performance tab serves the SAME canonical series
# ---------------------------------------------------------------------------


class TestPerformanceTabServesCanonicalSeries:
    def test_aggregate_series_includes_every_trading_day_not_only_trigger_days(
        self, client, seeded_shadow_db, positions, pm_dir, shadow_last_rows
    ):
        """Every shadow_history trading day must appear in the aggregate series —
        including days on which nothing triggered (derived from the fixture: days
        whose real post-mortem file has an empty triggers array). RED today: the
        series comes from post-mortem trigger arrays, so those days vanish.
        """
        database.save_state(
            {
                sym_id: {"name": entry["name"], "current_value": entry["current_value"]}
                for sym_id, entry in positions.items()
            }
        )
        shadow_days = sorted({r["trading_day"] for r in shadow_last_rows})
        zero_trigger_days = [
            day
            for day in shadow_days
            if (pm_dir / f"post_mortem_{day}.json").exists()
            and not json.loads(
                (pm_dir / f"post_mortem_{day}.json").read_text(encoding="utf-8")
            ).get("triggers")
        ]
        assert zero_trigger_days, (
            "fixture integrity: production data must contain a zero-trigger day "
            "(the audit found 2026-06-26 missing from the tab)"
        )

        resp = client.get("/api/performance?scope=aggregate&days=3650")
        assert resp.status_code == 200
        dates = resp.get_json()["dates"]

        assert list(dates) == shadow_days, (
            "aggregate series dates must be the shadow_history trading days "
            f"(selection-biased trigger-day series detected: missing "
            f"{sorted(set(shadow_days) - set(dates))})"
        )

    def test_aggregate_series_values_are_the_value_weighted_portfolio_returns(
        self, client, seeded_shadow_db, positions, pm_dir, shadow_last_rows
    ):
        """The aggregate live/shadow series must be the canonical value-weighted
        (bot, held) daily returns — not exit-snapshot event values."""
        database.save_state(
            {
                sym_id: {"name": entry["name"], "current_value": entry["current_value"]}
                for sym_id, entry in positions.items()
            }
        )
        _, bot_daily, held_daily = _daily_series(shadow_last_rows, _values(positions))

        resp = client.get("/api/performance?scope=aggregate&days=3650")
        assert resp.status_code == 200
        body = resp.get_json()

        _assert_series_close(body["live_returns"], bot_daily, "live_returns")
        _assert_series_close(body["shadow_returns"], held_daily, "shadow_returns")

    def test_performance_and_hero_chart_agree_on_one_series(
        self, client, seeded_shadow_db, positions, pm_dir
    ):
        """Cross-surface invariant, independent of the expected recompute:
        compounding the Performance tab's daily bot returns must reproduce the
        hero chart's bot curve point-for-point. One canonical series, two views.
        RED today: Performance says +3.1% where the hero says -3.43%.
        """
        database.save_state(
            {
                sym_id: {"name": entry["name"], "current_value": entry["current_value"]}
                for sym_id, entry in positions.items()
            }
        )
        perf = client.get("/api/performance?scope=aggregate&days=3650").get_json()
        hero = client.get("/api/hero-chart/all").get_json()

        assert list(perf["dates"]) == list(hero["hist_dates"]), (
            "Performance and Overview must window the same trading days"
        )
        _assert_series_close(
            _compound(list(perf["live_returns"])), hero["hist_bot"], "compound(perf.live)"
        )
        _assert_series_close(
            _compound(list(perf["shadow_returns"])), hero["hist_held"], "compound(perf.shadow)"
        )
