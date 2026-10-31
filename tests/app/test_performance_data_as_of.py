"""RED: ``GET /api/performance`` says how fresh its newest observation is (``data_as_of``).

CONTRACT PINNED: the body gains ``data_as_of``, ``app._format_data_stamp`` of the newest
``shadow_history.ts_utc`` of the response's LAST date (``dates[-1]``): across all symphonies for
``scope=aggregate``, for the one symphony for ``scope=symphony``. It reads ``--`` when the
response has no date or the date has no stored time. The key is present on every 200 answer.

The route runs over the real database: the captured production shadow rows are written through
``database.record_shadow_observation`` (``tests/fixtures/prod_droplet/shadow_last_rows.json``) and the
expected stamp is recomputed here from those same rows. This is a new file because the sibling
``tests/app/test_performance_routes.py`` replaces ``analytics`` wholesale, which hides the date the
stamp is read for. ``days`` is an integer row count so no calendar cutoff makes the test date-bound.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

import analytics
import app as app_module
import database

ROWS = json.loads(
    (
        Path(__file__).resolve().parents[1] / "fixtures" / "prod_droplet" / "shadow_last_rows.json"
    ).read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def _analytics_reads_the_test_database(monkeypatch):
    """analytics reads the shadow table through its own DB_FILE; point it at this test's database."""
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def seed(rows):
    for index, row in enumerate(rows):
        database.record_shadow_observation(
            symphony_id=row["symphony_id"],
            account_id=row["account_id"],
            cycle_id=row["cycle_id"] or f"fixture-{index}",
            ts_utc=row["ts_utc"],
            ts_et=row["ts_et"],
            trading_day=row["trading_day"],
            current_return=row["current_return"],
            shadow_return=row["shadow_return"],
            is_post_trigger=row["is_post_trigger"],
            trigger_id=None,
            position_epoch=row.get("position_epoch"),
        )


def stamp_of(text: str) -> str:
    return app_module._format_data_stamp(datetime.fromisoformat(text.replace("Z", "+00:00")))


def newest(day: str, symphony: str | None = None) -> str:
    return max(
        r["ts_utc"]
        for r in ROWS
        if r["trading_day"] == day and symphony in (None, r["symphony_id"])
    )


def get(client, query):
    response = client.get("/api/performance?" + query)
    assert response.status_code == 200, response.get_data(as_text=True)[:200]
    return response.get_json()


def test_aggregate_stamp_is_the_newest_observation_of_the_last_date(client):
    seed(ROWS)
    body = get(client, "scope=aggregate&days=60")
    assert body["dates"], "fixture integrity: the captured rows must chart"
    assert "data_as_of" in body, "GET /api/performance carries no data_as_of"
    assert body["data_as_of"] == stamp_of(newest(body["dates"][-1]))


def test_symphony_stamp_is_that_symphonys_newest_observation(client):
    seed(ROWS)
    symphony = sorted({r["symphony_id"] for r in ROWS})[0]
    body = get(client, f"scope=symphony&symphony_id={symphony}&days=60")
    assert body["dates"], "fixture integrity: the captured symphony must chart"
    assert body["data_as_of"] == stamp_of(newest(body["dates"][-1], symphony))


def test_a_new_row_moves_the_stamp(client):
    seed(ROWS)
    first = get(client, "scope=aggregate&days=60")["data_as_of"]
    last_day = sorted({r["trading_day"] for r in ROWS})[-1]
    row = next(r for r in ROWS if r["trading_day"] == last_day)
    database.record_shadow_observation(
        symphony_id=row["symphony_id"],
        account_id=row["account_id"],
        cycle_id="later-cycle",
        ts_utc=f"{last_day}T23:59:00Z",
        ts_et=f"{last_day}T19:59:00",
        trading_day=last_day,
        current_return=row["current_return"],
        shadow_return=row["shadow_return"],
        is_post_trigger=row["is_post_trigger"],
        trigger_id=None,
    )
    second = get(client, "scope=aggregate&days=60")["data_as_of"]
    assert second == stamp_of(f"{last_day}T23:59:00Z") and second != first


@pytest.mark.parametrize(
    "query", ["scope=aggregate&days=60", "scope=symphony&symphony_id=none&days=60"]
)
def test_an_empty_history_reads_the_dash(client, query):
    body = get(client, query)
    assert body["dates"] == []
    assert body["data_as_of"] == "--"


def test_the_stamp_is_a_text_in_the_dated_form(client):
    seed(ROWS)
    stamp = get(client, "scope=aggregate&days=60")["data_as_of"]
    assert isinstance(stamp, str)
    import re

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} ET", stamp), stamp
