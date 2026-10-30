"""Window tokens on the Performance and History routes mean the dashboard's calendar windows.

`/api/performance` accepts the dashboard's window tokens (30d, 60d, 90d, 125d, ytd, 1y,
5y) as calendar cutoffs resolved by `analytics._window_cutoff_date`, still accepts an
integer as the trailing row count, refuses anything else naming what it accepts, and
`/api/history/ytd` windows History from 1 January. Series come from `shadow_history`
rows written with the writer's own INSERT (tests/ui/test_static_js_page_harness.py proves
the equivalence) and from captured production post-mortems and shadow rows.

The clock is frozen the way tests/analytics/test_bl5_window_cutoff_unification.py freezes
it, because the cutoff is read from `datetime.now(UTC)`.
"""

from __future__ import annotations

import datetime as dtmod
import os
from datetime import date, timedelta

import pytest

import analytics
import app as app_module
import database
from tests.ui.test_static_js_page_harness import (
    captured_json,
    pm_capture,
    seed_shadow_rows,
    shadow_row,
    stamped,
)

SYMPHONY = "Hb3Kd9Pq2Rt7Vw5Xy1Za"
TOKENS = {"30d": 30, "60d": 60, "90d": 90, "125d": 125, "1y": 365, "5y": 1825}


@pytest.fixture(autouse=True)
def _analytics_shadow_db_seam(_isolate_db, monkeypatch):
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _freeze(monkeypatch, instant_utc: str) -> date:
    frozen = dtmod.datetime.fromisoformat(instant_utc).replace(tzinfo=dtmod.UTC)

    class _Frozen(dtmod.datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen.astimezone(tz) if tz is not None else frozen.replace(tzinfo=None)

    monkeypatch.setattr(dtmod, "datetime", _Frozen)
    return frozen.date()


def _weekdays(start: date, end: date) -> list[str]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def _seed_flat_history(days: list[str], symphony: str = SYMPHONY) -> None:
    seed_shadow_rows(os.environ["DB_PATH"], [shadow_row(symphony, d, 0.1, 0.1) for d in days])
    database.save_state({symphony: {"name": "Copper Ridge Rotation", "current_value": 1000.0}})


def _get(client, query: str) -> dict:
    resp = client.get(f"/api/performance?{query}")
    assert resp.status_code == 200, (
        f"/api/performance?{query} answered {resp.status_code}: {resp.get_data(as_text=True)[:200]}"
    )
    return resp.get_json()


# ---------------------------------------------------------------------------
# Performance: tokens are calendar windows
# ---------------------------------------------------------------------------


def test_one_year_starts_at_the_first_row_on_or_after_365_calendar_days_ago(client, monkeypatch):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    all_days = _weekdays(today - timedelta(days=730), today)
    _seed_flat_history(all_days)
    cutoff = (today - timedelta(days=365)).isoformat()
    expected = [d for d in all_days if d >= cutoff]
    assert len(expected) != 252, "fixture integrity: 365 calendar days must differ from 252 rows"

    body = _get(client, "scope=aggregate&days=1y")

    assert body["dates"] == expected
    assert body["window_days"] == "1y"
    assert body["window_start"] == cutoff


@pytest.mark.parametrize("token", ["30d", "60d", "90d", "125d"])
def test_day_tokens_start_at_the_first_row_on_or_after_that_many_calendar_days_ago(
    client, monkeypatch, token
):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    all_days = _weekdays(today - timedelta(days=730), today)
    _seed_flat_history(all_days)
    cutoff = (today - timedelta(days=TOKENS[token])).isoformat()
    expected = [d for d in all_days if d >= cutoff]
    assert len(expected) != TOKENS[token], "fixture integrity: calendar days differ from row count"

    body = _get(client, f"scope=aggregate&days={token}")

    assert body["dates"] == expected
    assert body["window_days"] == token
    assert body["window_start"] == cutoff


def test_a_thirty_day_window_cannot_hold_thirty_observations_so_history_is_insufficient(
    client, monkeypatch
):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    _seed_flat_history(_weekdays(today - timedelta(days=730), today))

    body = _get(client, "scope=aggregate&days=30d")

    assert body["observation_count"] < app_module._PERFORMANCE_MIN_HISTORY_DAYS
    assert body["insufficient_history"] is True


def test_five_years_over_two_hundred_days_of_rows_returns_every_row_and_states_the_cutoff(
    client, monkeypatch
):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    all_days = _weekdays(today - timedelta(days=200), today)
    _seed_flat_history(all_days)

    body = _get(client, "scope=aggregate&days=5y")

    assert body["dates"] == all_days
    assert body["date_range"]["start"] == all_days[0]
    assert body["window_start"] == (today - timedelta(days=1825)).isoformat()
    assert body["coverage_days"] == len(all_days), "coverage states the days actually held"


def test_year_to_date_on_a_january_day_keeps_only_rows_dated_after_the_first(client, monkeypatch):
    _freeze(monkeypatch, "2027-01-02T12:00:00")
    _seed_flat_history(["2026-12-30", "2026-12-31", "2027-01-02"])

    body = _get(client, "scope=aggregate&days=ytd")

    assert body["dates"] == ["2027-01-02"]
    assert body["window_days"] == "ytd"
    assert body["window_start"] == "2027-01-01"


def test_integer_days_still_mean_trailing_rows_and_carry_no_window_start(client, monkeypatch):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    all_days = _weekdays(today - timedelta(days=730), today)
    _seed_flat_history(all_days)

    body = _get(client, "scope=aggregate&days=252")

    assert body["dates"] == all_days[-252:]
    assert body["window_days"] == 252
    assert "window_start" in body and body["window_start"] is None


def test_a_history_shorter_than_the_minimum_reports_insufficient_with_what_exists(
    client, monkeypatch
):
    _freeze(monkeypatch, "2026-07-10T12:00:00")
    rows = captured_json("shadow_last_rows.json")
    seed_shadow_rows(os.environ["DB_PATH"], rows)
    database.save_state(captured_json("bot_state_positions.json"))
    held_days = sorted({r["trading_day"] for r in rows})
    assert len(held_days) < app_module._PERFORMANCE_MIN_HISTORY_DAYS

    body = _get(client, "scope=aggregate&days=60d")

    assert body["dates"] == held_days
    assert body["observation_count"] == len(held_days)
    assert body["insufficient_history"] is True


# A token window that holds no rows must read as empty. The day-1 fallbacks fetch the
# whole series, so without the same cutoff they hand back rows from before the window
# and the response labels them with the window's own `window_start`.
ALL_WINDOW_TOKENS = ["30d", "60d", "90d", "125d", "ytd", "1y", "5y"]
DAYS_BEFORE_EVERY_WINDOW = 2000  # older than the 5y cutoff (1825) and any 1 January


def _assert_empty_window(body: dict) -> None:
    assert body["window_start"] is not None, "a token window states its cutoff"
    assert body["dates"] == [] and body["live_returns"] == [] and body["shadow_returns"] == []
    assert body["observation_count"] == 0 and body["actual_days"] == 0
    assert body["coverage_days"] == 0
    assert body["date_range"] == {"start": None, "end": None}
    assert body["insufficient_history"] is True
    for leg in ("live_metrics", "shadow_metrics"):
        assert all(v is None for v in body[leg].values()), (
            f"{leg} must carry no figure for a window that holds no rows: {body[leg]!r}"
        )


@pytest.mark.parametrize("token", ALL_WINDOW_TOKENS)
def test_a_window_that_holds_no_rows_is_empty_even_when_older_days_exist(
    client, monkeypatch, token
):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    old_days = _weekdays(
        today - timedelta(days=DAYS_BEFORE_EVERY_WINDOW),
        today - timedelta(days=DAYS_BEFORE_EVERY_WINDOW - 14),
    )
    assert len(old_days) >= 2, "fixture integrity: two or more days so the full series exists"
    _seed_flat_history(old_days)

    body = _get(client, f"scope=aggregate&days={token}")

    _assert_empty_window(body)
    assert body["window_days"] == token


@pytest.mark.parametrize("token", ALL_WINDOW_TOKENS)
def test_a_single_day_older_than_the_window_is_empty_not_the_one_day_chart(
    client, monkeypatch, token
):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    old_day = _weekdays(
        today - timedelta(days=DAYS_BEFORE_EVERY_WINDOW),
        today - timedelta(days=DAYS_BEFORE_EVERY_WINDOW - 6),
    )[:1]
    _seed_flat_history(old_day)

    body = _get(client, f"scope=aggregate&days={token}")

    _assert_empty_window(body)


@pytest.mark.parametrize("token", ["30d", "1y", "ytd"])
def test_a_first_day_inside_the_window_still_charts_its_one_day(client, monkeypatch, token):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    _seed_flat_history([today.isoformat()])

    body = _get(client, f"scope=aggregate&days={token}")

    assert body["dates"] == [today.isoformat()]
    assert body["observation_count"] == 1
    assert body["date_range"] == {"start": today.isoformat(), "end": today.isoformat()}
    assert all(d >= body["window_start"] for d in body["dates"])


@pytest.mark.parametrize("bad", ["abc", "2w", "-", "1.5"])
def test_an_unrecognised_days_value_is_refused_naming_the_integers_and_tokens(client, bad):
    resp = client.get(f"/api/performance?scope=aggregate&days={bad}")

    assert resp.status_code == 400
    message = resp.get_json()["message"]
    assert "integer" in message
    for token in TOKENS | {"ytd": 0}:
        assert token in message, f"the refusal must name {token!r}: {message!r}"


def test_the_dashboard_detail_panel_request_still_returns_the_trailing_sixty_rows(client):
    rows = captured_json("shadow_last_rows.json")
    seed_shadow_rows(os.environ["DB_PATH"], rows)
    state = captured_json("bot_state_positions.json")
    database.save_state(state)
    sym = next(s for s in state if any(r["symphony_id"] == s for r in rows))
    own = sorted((r for r in rows if r["symphony_id"] == sym), key=lambda r: r["trading_day"])[-60:]

    body = _get(client, f"scope=symphony&symphony_id={sym}&days=60")

    assert body["dates"] == [r["trading_day"] for r in own]
    assert body["live_returns"] == pytest.approx([r["current_return"] for r in own])
    assert body["shadow_returns"] == pytest.approx([r["shadow_return"] for r in own])


# ---------------------------------------------------------------------------
# History: YTD and one-year parity with Performance
# ---------------------------------------------------------------------------


def _write_pm(tmp_path, monkeypatch, days: dict[str, list[dict]]) -> None:
    import json

    pm = tmp_path / "pm"
    pm.mkdir()
    for day, triggers in days.items():
        (pm / f"post_mortem_{day}.json").write_text(
            json.dumps({"triggers": triggers}), encoding="utf-8"
        )
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(pm))


def test_history_ytd_covers_post_mortem_days_from_the_first_of_january(
    client, monkeypatch, tmp_path
):
    _freeze(monkeypatch, "2027-01-02T12:00:00")
    triggers = [stamped(t) for t in pm_capture("2026-06-22")["triggers"]]
    _write_pm(
        tmp_path,
        monkeypatch,
        {"2026-12-31": triggers, "2027-01-01": triggers, "2027-01-02": triggers},
    )

    resp = client.get("/api/history/ytd")

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["daily_dates"] == ["2027-01-01", "2027-01-02"]
    assert body["window_days"] == "ytd"
    assert body["coverage_days"] == body["actual_days"] == len(body["daily_dates"])


def test_history_one_year_and_performance_one_year_start_on_the_same_day(
    client, monkeypatch, tmp_path
):
    today = _freeze(monkeypatch, "2026-08-20T12:00:00")
    cutoff = today - timedelta(days=365)
    _seed_flat_history(_weekdays(today - timedelta(days=730), today))
    triggers = [stamped(t) for t in pm_capture("2026-06-22")["triggers"]]
    band = [(cutoff + timedelta(days=i)).isoformat() for i in range(-3, 4)]
    _write_pm(tmp_path, monkeypatch, {d: triggers for d in band})

    history = client.get("/api/history/365").get_json()
    performance = _get(client, "scope=aggregate&days=1y")

    assert history["daily_dates"][0] == performance["dates"][0] == cutoff.isoformat()
    assert all(d >= cutoff.isoformat() for d in history["daily_dates"])
