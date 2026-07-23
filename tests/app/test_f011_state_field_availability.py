"""
RED/ground-truth tests -- F-011: /api/state's `symphonies` field is genuinely
absent on the closed/frozen branch, while `state`/`bot_state` are present on
BOTH branches.

This is the ROUTE-LEVEL proof the JS fix (static/index.js's updateSectionMeta,
covered in tests/dashboard/test_display_truth_cluster_js.py) depends on --
"already works is not evidence" precedent (tests/app/
test_f7_ac2_poll_path_null_passthrough.py): a source-text pin on the JS alone
does not prove the field it switches to is actually safe to read on every
branch. These tests exercise the REAL route, not template logic in isolation.

Root cause: app.py's closed/frozen branch (~2033-2054) emits
`"state": _state, "bot_state": _state` but NO `"symphonies"` key at all --
confirmed against the OPEN-market branch (~2410-2426), which DOES emit
`"symphonies": _symphonies_for_cards` alongside the same `state`/`bot_state`
keys. static/index.js's updateSectionMeta currently reads `data.symphonies`
(absent on the closed branch) instead of `data.state`/`data.bot_state`
(present on both) -- the section-count badges deterministically read 0
whenever the market is closed.

BOTH TESTS PASS TODAY (expected -- these are GROUND-TRUTH/precondition pins,
not RED tests against a bug in app.py). app.py's server-side field shape is
already correct on both branches; the BUG is entirely client-side (index.js
reading the wrong field), covered separately and confirmed RED in
tests/dashboard/test_display_truth_cluster_js.py
(TestF011SectionBadgesReadRealField). Per the F7 "already works is not
evidence" precedent (tests/app/test_f7_ac2_poll_path_null_passthrough.py):
the JS fix's target field must be PROVEN safe via a real route test, not just
asserted from the audit -- these tests are that proof.
"""

from __future__ import annotations

from unittest.mock import patch

import app as app_module


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
    """The critical ground-truth pin: on the closed/frozen branch, `symphonies`
    is genuinely ABSENT while `state`/`bot_state` are present -- proving
    static/index.js's updateSectionMeta MUST read data.state/data.bot_state,
    never data.symphonies, to render correctly on both branches."""

    def test_closed_frozen_symphonies_key_is_absent(self):
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
        assert "symphonies" not in body, (
            "ground-truth sanity FAIL: 'symphonies' is present on the "
            "closed/frozen branch in this test's fixture -- either app.py's "
            "closed-branch response shape changed (update this test), or this "
            "fixture does not actually exercise the closed/frozen branch."
        )
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
