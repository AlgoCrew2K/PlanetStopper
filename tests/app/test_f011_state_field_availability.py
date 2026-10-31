"""
F-011: /api/state carries `state`/`bot_state` and the `symphonies` card list on every
branch, and the fields no client reads are not computed.

History: the closed/frozen branch once emitted `state`/`bot_state` but no `symphonies`, so
static/index.js's updateSectionMeta read `data.state`/`data.bot_state` (present on both
branches) instead of `data.symphonies`. These tests exercise the REAL route, not template
logic in isolation ("already works is not evidence", tests/app/
test_f7_ac2_poll_path_null_passthrough.py).

The closed/frozen branch now also lists its cards (`symphonies`), built from the snapshot's
entries, so the control case below reverses: `symphonies` is present on every branch and each
frozen card carries the legs the branch computed for its entry. The same file pins that
`meta.triggers_today`, which no script reads, is no longer computed, and that every other
`meta` and top-level field the reference served is still served.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

import app as app_module
import database as database_module
from tests.app._refresh_timing_state_world import BRANCHES, load_record, seeded_world


class TestOpenMarketStateFieldAvailability:
    """Control case: the open-market branch already carries all three keys --
    this must remain true (regression pin) both before and after the F-011 fix,
    since the fix only changes WHICH field the CLIENT reads, not what the
    server emits."""

    def test_open_market_symphonies_state_and_bot_state_all_present(self):
        app_module.app.config["TESTING"] = True
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {
                "sym-open": {
                    "name": "Open Market Symphony",
                    "account": "ACC1",
                    "armed": True,
                    "tp_armed": False,
                    "para_armed": False,
                    "triggered": False,
                    "current_return": 1.0,
                    "current_value": 5000.0,
                    "stop_trigger": -2.0,
                    "mc_prob": 30.0,
                }
            }
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower()
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            db_mock.get_triggers.return_value = []
            db_mock.read_fleet_alert.return_value = None
            db_mock.get_guard_alpha_by_symphony.return_value = {}
            db_mock.get_last_trigger_per_symphony.return_value = {}

            with patch.object(app_module, "get_market_state", lambda dt: "open"):
                with app_module.app.test_client() as client:
                    resp = client.get("/api/state")

        assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
        body = resp.get_json()
        assert "symphonies" in body, (
            "regression sanity: the open-market branch must carry 'symphonies' -- "
            "this is the control case proving the closed-branch omission (below) "
            "is a genuine branch difference, not a route-wide absence."
        )
        assert "state" in body and "bot_state" in body, (
            "the open-market branch must ALSO carry 'state'/'bot_state' -- these "
            "are the fields the F-011 fix switches the client to read."
        )


class TestClosedFrozenMarketStateFieldAvailability:
    """On the closed/frozen branch `state`/`bot_state` and the `symphonies` card list are
    all present, so a reader of either field renders correctly on both branches."""

    def test_closed_frozen_symphonies_list_carries_the_snapshot_entries(self):
        app_module.app.config["TESTING"] = True
        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {
                "date": "2026-06-09",
                "last_market_close_snapshot": {
                    "captured_at_et": "16:00:00 ET",
                    "data_as_of": "16:00 ET",
                    "trading_day": "2026-06-09",
                    # The closed/frozen route builds `_state` from accounts_map
                    # (app.py:1691-1695), NOT from a top-level "state" key --
                    # each account's symphony dicts must carry "id".
                    "accounts_map": {
                        "ACC1": [
                            {
                                "id": "sym-closed",
                                "name": "Closed Market Symphony",
                                "account": "ACC1",
                                "armed": True,
                                "tp_armed": False,
                                "para_armed": False,
                                "triggered": False,
                                "current_return": 1.0,
                                "current_value": 5000.0,
                                "simple_return": 0.1,
                                "net_deposits": 1000.0,
                                "time_weighted_return": 0.1,
                                "max_drawdown": 0.05,
                            }
                        ]
                    },
                    "portfolio_strip": {
                        "today_change": {"if_held": 1.0, "dry_run": 1.0},
                        "cumulative_return": {"if_held": 10.0, "dry_run": 10.0},
                        "max_drawdown": {"if_held": 5.0, "dry_run": 4.0},
                    },
                },
            }
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower()
            db_mock.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            db_mock.read_fleet_alert.return_value = None

            with patch.object(app_module, "get_market_state", lambda dt: "closed_frozen"):
                with app_module.app.test_client() as client:
                    resp = client.get("/api/state")

        assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
        body = resp.get_json()
        assert body.get("market_state") == "closed_frozen", "fixture must hit the frozen branch"
        assert isinstance(body.get("symphonies"), list), (
            "the closed/frozen branch must list its cards: each snapshot entry's status "
            "and its Today, Cumulative and Max DD legs"
        )
        assert [card["id"] for card in body["symphonies"]] == ["sym-closed"]
        card = body["symphonies"][0]
        leg_keys = ("tc_bot", "tc_held", "cr_bot", "cr_held", "mdd_bot", "mdd_held")
        assert all(key in card for key in leg_keys), sorted(card)
        entry = body["bot_state"]["sym-closed"]
        assert card["status"] == "armed"
        assert card["tc_bot"] == entry["_tc"]["dry_run"], "the card reads the entry's legs"
        assert card["cr_held"] == entry["_cr"]["if_held"]
        assert card["mdd_bot"] == entry["_mdd"]["dry_run"]
        assert "state" in body and "bot_state" in body, (
            "F-011 FAIL (ground truth): 'state'/'bot_state' are missing on the "
            "closed/frozen branch -- static/index.js's updateSectionMeta cannot "
            "safely switch to reading data.state/data.bot_state if this branch "
            "doesn't carry it either."
        )
        assert body["state"] and "sym-closed" in body["state"], (
            "the closed/frozen 'state' field must carry the real per-symphony "
            "dict (keyed by symphony id) -- the shape updateSectionMeta's "
            "armed/tp_armed/para_armed/triggered filter needs."
        )


# ---------------------------------------------------------------------------
# The fields no client reads are not computed; every other field is still served
# ---------------------------------------------------------------------------


class TestTriggersTodayIsNotComputed:
    """`meta.triggers_today` fed only a status strip no template contains. It is gone, and the
    exit-trigger query that built it is not run for it on any branch."""

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_meta_has_no_triggers_today_and_the_meta_query_is_not_run(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)
        callers = _spy_on_trigger_reads(monkeypatch)

        body = world.poll()

        assert "triggers_today" not in body["meta"]
        assert "_build_meta" not in callers, (
            "the exit-trigger read for the removed field still runs in _build_meta"
        )

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_the_page_render_does_not_run_the_query_either(self, monkeypatch, tmp_path, branch):
        world = seeded_world(monkeypatch, tmp_path, branch)
        callers = _spy_on_trigger_reads(monkeypatch)

        world.page()

        assert "_build_meta" not in callers


class TestEveryOtherFieldIsStillServed:
    @pytest.mark.parametrize("branch", BRANCHES)
    def test_reference_top_level_and_meta_fields_are_all_present(
        self, monkeypatch, tmp_path, branch
    ):
        golden = load_record(f"golden_ref_api_state_{branch}.json")
        world = seeded_world(monkeypatch, tmp_path, branch)

        body = world.poll()

        assert set(golden) - set(body) == set(), "a top-level field the reference served is gone"
        assert set(golden["meta"]) - {"triggers_today"} <= set(body["meta"]), (
            "a meta field other than triggers_today is gone"
        )

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_documented_fields_no_script_reads_are_still_served(
        self, monkeypatch, tmp_path, branch
    ):
        world = seeded_world(monkeypatch, tmp_path, branch)

        body = world.poll()

        assert isinstance(body.get("html"), str) or branch != "open", "html"
        for key in ("shadow_divergence", "port_state", "exit_authority"):
            assert key in body, key
        if branch == "open":
            assert "execution_start_time" in body
        else:
            assert "accounts_map" in body

    @pytest.mark.parametrize("branch", BRANCHES)
    def test_stable_meta_values_equal_the_reference(self, monkeypatch, tmp_path, branch):
        golden = load_record(f"golden_ref_api_state_{branch}.json")["meta"]
        world = seeded_world(monkeypatch, tmp_path, branch)

        body = world.poll()

        for key in ("tracked", "armed", "triggered", "market_state", "market_state_label", "mode"):
            assert body["meta"][key] == golden[key], key


def _spy_on_trigger_reads(monkeypatch) -> list[str]:
    """Record the function that calls `database.get_triggers` (the real read still runs)."""
    callers: list[str] = []
    real = database_module.get_triggers

    def spy(*args, **kwargs):
        callers.append(sys._getframe(1).f_code.co_name)
        return real(*args, **kwargs)

    monkeypatch.setattr(database_module, "get_triggers", spy)
    return callers
