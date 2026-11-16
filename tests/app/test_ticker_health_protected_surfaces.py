"""What the dead-ticker check must leave as it is: the tenure review's stored records, the health
probe and the header status read.

* The tenure review runs the real job and the real runner over the Composer router. Its stored run
  row and recommendation rows are read once with no check ever run and once after a real check run
  has written its own rows into the same kind of database from the same router; the two are equal
  once the run ids (fresh uuids) are removed. The settings the review reads are pinned beside it in
  tests/app/test_ticker_health_scheduling.py, so the helper the two features now share cannot
  change what the review is handed.
* ``GET /health`` (on the box and from a remote caller) and ``GET /api/header-status`` answer the
  same status, the same degraded reasons and the same keys with a stored run that names dead
  tickers as with none, and name no ticker. The stored run is written by the real runner.

The settings allowlist and the engine files are pinned by existing tests, cited in the handoff:
tests/app/test_hygiene_surface_unchanged.py (the allowlist and the route table) and
tests/test_mdd_window_truth_zero_diff_guard.py (the two engine files).

Nothing reaches the network and no sleep is real; the developer's environment file is never read.
"""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

import app as app_module
from tests.advisors import _ticker_health_http_router as t
from tests.app import _tenure_review_job_builders as jb
from tests.app.test_ticker_health_scheduling import (
    ACCOUNT,
    DEAD,
    HEALTHY,
    isolate_env,
    need,
    run_records,
    seed_run,
    set_clocks,
)

ET = ZoneInfo("America/New_York")
ID_KEYS = ("run_id", "review_run_id")
REMOTE_PEER = {"REMOTE_ADDR": "203.0.113.9"}
NIGHT = t.NIGHT


def _scrub(value):
    """The record with every run id (a fresh uuid per run) removed, at any depth."""
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items() if k not in ID_KEYS}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _review_world(monkeypatch, tmp_path, database_name: str):
    """A scratch database, the review's environment and a router that answers both the review's
    reads and the check's reads from the same captured records."""
    jb.use_scratch_db(monkeypatch, tmp_path, name=database_name)
    jb.freeze_app_clock(monkeypatch)
    jb.stub_live_explainer(monkeypatch)
    isolate_env(
        monkeypatch,
        tmp_path,
        ACCOUNT_ROTH=ACCOUNT,
        ALPACA_KEY=t.ALPACA_KEY,
        ALPACA_SECRET=t.ALPACA_SECRET,
    )
    clean = jb.install_clean_world(monkeypatch, twin=True)
    router = t.TickerHealthRouter()
    router.statistics_default = clean.statistics_default
    router.series_by_symphony = clean.series_by_symphony
    router.definition_by_symphony = clean.definition_by_symphony
    router.backtest_handler = clean.backtest_handler
    return router.install(monkeypatch)


def _run_review_tick() -> None:
    """The 03:45 tick, with the thread it starts run on the spot."""
    with patch("threading.Thread", jb.ImmediateThread):
        app_module._run_retirement_recommender_tick()


def _stored_review_records() -> dict:
    return {
        "run": [_scrub(row["raw_response"]) for row in jb.run_rows()],
        "recommendations": [_scrub(row["raw_response"]) for row in jb.rec_rows()],
    }


class TestTheTenureReviewIsUnchanged:
    def test_its_stored_records_are_the_same_after_a_check_run_as_with_none(
        self, monkeypatch, tmp_path
    ):
        _review_world(monkeypatch, tmp_path, "review_alone.db")
        _run_review_tick()
        alone = _stored_review_records()
        assert len(alone["run"]) == 1, "test premise: the review stored its run row"
        assert alone["recommendations"], "test premise: the world yields a recommendation"

        router = _review_world(monkeypatch, tmp_path, "review_after_check.db")
        set_clocks(monkeypatch, router, jb.NOW_ET, app_clock=False)
        need("_ticker_health_run_body")("nightly")
        assert run_records()[0]["completed"] is True, "test premise: the check run completed"
        _run_review_tick()
        after = _stored_review_records()

        assert after == alone


# ---------------------------------------------------------------------------
# /health and /api/header-status
# ---------------------------------------------------------------------------

_FIXED_NOW = datetime(2026, 11, 16, 21, 0, tzinfo=ET)


def _status_reads(client, monkeypatch) -> dict:
    monkeypatch.setattr(app_module, "_now_et", lambda: _FIXED_NOW)
    monkeypatch.setattr(
        app_module._dotenv_module,
        "dotenv_values",
        lambda *_a, **_k: {"LIVE_EXECUTION": "False", "ACCOUNT_INDIVIDUAL": "0123abcd-1"},
    )
    health_on_the_box = client.get("/health")
    health_remote = client.get("/health", environ_base=REMOTE_PEER)
    header = client.get("/api/header-status")
    for response in (health_on_the_box, health_remote, header):
        assert response.status_code == 200, response.status_code
    return {
        "health_on_the_box": health_on_the_box.get_json(),
        "health_remote": health_remote.get_json(),
        "header": header.get_json(),
    }


class TestHealthAndHeaderStatusIgnoreTheCheck:
    @pytest.fixture
    def world(self, monkeypatch, tmp_path):
        router = t.TickerHealthRouter().install(monkeypatch)
        tree = t.tree_with(assets=[DEAD, HEALTHY])
        router.set_roster(t.roster_entry(t.PLANET_LQD_ID), t.roster_entry(t.TROLLING_ID))
        router.set_tree(t.PLANET_LQD_ID, tree)
        router.set_tree(t.TROLLING_ID, tree)
        isolate_env(monkeypatch, tmp_path)
        return router

    def test_a_stored_run_naming_dead_tickers_changes_no_status_reason_or_key(
        self, world, client, monkeypatch
    ):
        before = _status_reads(client, monkeypatch)

        outcome = seed_run(world, NIGHT)
        record = run_records()[0]
        assert record["run_id"] == outcome.run_id
        assert record["warnings"], "test premise: the stored run names dead tickers"
        after = _status_reads(client, monkeypatch)

        for key in ("health_on_the_box", "health_remote", "header"):
            assert after[key]["status"] == before[key]["status"], key
            assert after[key]["degraded_reasons"] == before[key]["degraded_reasons"], key
            assert sorted(after[key]) == sorted(before[key]), key
            assert "ticker" not in json.dumps(after[key]).lower(), key
            assert DEAD not in json.dumps(after[key]), key
