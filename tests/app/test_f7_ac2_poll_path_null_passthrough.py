"""
RED tests -- F7 AC-2: JSON-serialized None through the REAL poll paths.

Cycle context (feature-plans/math-f7.md + team-lead's LOCKED contract,
2026-07-18)
---------------------------------------------------------------------------
Two of the four AC-2 render surfaces (the main-table MC Prob column and the
detail Risk Math eager-populate, ``populateRiskMathFromState``) already
render an explicit "---"/"--" for a None ``mc_prob`` -- but "already works
is not evidence" (ADDENDUM). Both surfaces are driven by ``GET /api/state``'s
``symphonies`` field and ``GET /api/chart/<id>``'s ``data`` array
respectively. This file proves the ROUTE/JSON layer itself never coerces a
None ``mc_prob`` into 0, drops the key, or otherwise corrupts it in transit
-- exercising the REAL poll path end-to-end (Flask test client -> real
route -> real jsonify), not template logic tested in isolation.

The second test is also the golden fixture for the chart-fallback binding
case at the JSON layer: a MIXED pre/post-trigger day (some rows real floats,
some rows the honest sentinel) must pass through the ``/api/chart/<id>``
route byte-identical, per row -- this is the data ``static/index.js``'s
chart-fallback / MC-dial rendering consumes, so if the route ever corrupted
this, no client-side fix could compensate.

RED STATUS (HEAD a904374d, no F7 code yet)
-------------------------------------------
Both routes already pass ``None`` through unmodified today (verified by
source read: app.py's ``/api/state`` symphonies field does
``"mc_prob": _s.get("mc_prob")`` with no coercion; ``/api/chart/<id>``
returns chart_history rows verbatim). These tests are therefore expected to
PASS already (GREEN) -- they are regression pins proving the poll-path
consumers' existing None-safety, per the team-lead's explicit "already
works is not evidence" instruction: the PROOF must exist as a real,
runnable test against the real path, not remain an unverified claim.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "dashboard" / "f7_ac2_poll_path"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


@pytest.fixture()
def flask_client():
    """Flask test client with minimal stubs -- no live DB, no live network.
    Mirrors tests/dashboard/test_cards_live_updates.py's fixture."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with (
        patch.object(app_module, "analytics") as mock_analytics,
        patch.object(app_module, "schedule"),
    ):
        mock_analytics.get_portfolio_today_change.return_value = None
        mock_analytics.get_portfolio_cumulative_return.return_value = None
        mock_analytics.get_portfolio_max_drawdown.return_value = None
        mock_analytics.get_symphony_today_change.return_value = {"if_held": 0.0, "dry_run": 0.0}
        mock_analytics.get_symphony_cumulative_return.return_value = {
            "if_held": 0.0,
            "dry_run": 0.0,
        }
        mock_analytics.get_symphony_max_drawdown.return_value = {"if_held": 0.0, "dry_run": 0.0}
        with app_module.app.test_client() as client:
            yield client, app_module


# ---------------------------------------------------------------------------
# AC-2 / poll-path proof #1 -- GET /api/state symphonies["...']['mc_prob']
# ---------------------------------------------------------------------------
def test_api_state_triggered_symphony_mc_prob_is_real_json_null(flask_client) -> None:
    """A triggered symphony's mc_prob=None in bot_state must arrive as a
    genuine JSON null in the /api/state response -- never 0, never a
    stringified 'None', never a dropped key. The already-triggered fixture
    stands in for AC-1's post-fix persisted state; this test is about the
    ROUTE, not the engine."""
    client, app_module = flask_client
    fixture_state = _load_fixture("api_state_triggered_none_mc")

    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = fixture_state
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_last_trigger_per_symphony.return_value = {}
        db_mock.get_guard_alpha_by_symphony.return_value = {}

        monkeypatch_dotenv = patch.object(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch_render = patch.object(app_module, "render_template", lambda *_a, **_k: "")
        with monkeypatch_dotenv, monkeypatch_render:
            resp = client.get("/api/state")

    assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
    body = resp.get_json()
    symphonies = body.get("symphonies")
    assert symphonies is not None, (
        "AC-2 FAIL: /api/state response is missing the 'symphonies' field."
    )

    if isinstance(symphonies, list):
        sym_map = {s["id"]: s for s in symphonies if isinstance(s, dict) and "id" in s}
    else:
        sym_map = symphonies

    triggered = sym_map.get("sym_f7_triggered")
    assert triggered is not None, (
        "AC-2 FAIL: fixture symphony 'sym_f7_triggered' missing from response."
    )
    assert "mc_prob" in triggered, (
        "AC-2 FAIL: 'mc_prob' key dropped entirely from the triggered symphony's "
        "poll payload -- the detail-panel/table consumers key off this field's "
        "presence, not just its value."
    )
    assert triggered["mc_prob"] is None, (
        f"AC-2 FAIL: expected a real JSON null for the triggered symphony's "
        f"mc_prob through the REAL /api/state poll path; got "
        f"{triggered['mc_prob']!r} ({type(triggered['mc_prob']).__name__}). "
        f"A coercion to 0 or a stringified value anywhere in the route would "
        f"make the table/detail-panel 'already works' guards moot -- they'd "
        f"never see a real None to guard against."
    )

    untriggered = sym_map.get("sym_f7_untriggered")
    assert untriggered is not None, "Fixture symphony 'sym_f7_untriggered' missing from response."
    assert untriggered.get("mc_prob") is not None and isinstance(untriggered["mc_prob"], float), (
        f"Regression: the untriggered fixture symphony's real mc_prob must "
        f"pass through unchanged too -- got {untriggered.get('mc_prob')!r}."
    )


# ---------------------------------------------------------------------------
# AC-2 / poll-path proof #2 + chart-fallback golden -- GET /api/chart/<id>
# ---------------------------------------------------------------------------
def test_api_chart_mixed_pretrigger_posttrigger_day_passthrough_is_byte_identical() -> None:
    """A MIXED chart_history day -- some rows a real float mc_prob
    (pre-trigger and the triggering cycle itself, per ADDENDUM 2 timing),
    some rows the honest sentinel (post-trigger) -- must pass through
    ``GET /api/chart/<id>`` with every row's mc_prob byte-identical to the
    stored chart_history value. This is the golden fixture the
    chart-fallback / MC-dial JS logic consumes (static/index.js's backward
    scan and dial call sites) -- if the ROUTE ever corrupted a None into 0
    or dropped it, no client-side trigger-state short-circuit could recover
    the honest signal."""
    import app as app_module

    app_module.app.config["TESTING"] = True

    symphony_id = "sym_f7_mixed_day"
    mixed_rows = [
        {"time": "09:31", "return": 1.0, "stop": 0.5, "event": None, "mc_prob": 42.0, "vol": 0.2},
        {"time": "09:32", "return": 1.1, "stop": 0.5, "event": None, "mc_prob": 45.5, "vol": 0.2},
        # The triggering cycle itself: real value, per ADDENDUM 2 timing.
        {
            "time": "09:33",
            "return": -1.2,
            "stop": 0.5,
            "event": "Trailing Stop",
            "mc_prob": 100.0,
            "vol": 0.2,
        },
        # Post-trigger cycles: the honest sentinel.
        {"time": "09:34", "return": -1.2, "stop": 0.5, "event": None, "mc_prob": None, "vol": 0.2},
        {"time": "09:35", "return": -1.2, "stop": 0.5, "event": None, "mc_prob": None, "vol": 0.2},
    ]

    with patch.object(app_module, "database") as db_mock:
        db_mock.load_chart_history.return_value = {
            "date": "2026-05-01",
            "symphonies": {symphony_id: mixed_rows},
        }
        db_mock.get_ro_connection.return_value = MagicMock(
            execute=MagicMock(side_effect=Exception("no shadow rows in this fixture"))
        )

        with app_module.app.test_client() as client:
            resp = client.get(f"/api/chart/{symphony_id}")

    assert resp.status_code == 200, f"/api/chart/{symphony_id} returned {resp.status_code}"
    body = resp.get_json()
    assert body.get("status") == "success", f"Unexpected route status: {body!r}"
    data = body.get("data")
    assert isinstance(data, list) and len(data) == len(mixed_rows), (
        f"AC-2 FAIL: expected {len(mixed_rows)} rows passed through unchanged; got {data!r}"
    )

    for i, (expected_row, actual_row) in enumerate(zip(mixed_rows, data)):
        assert "mc_prob" in actual_row, (
            f"AC-2 FAIL: row {i} ({expected_row['time']}) is missing the "
            f"'mc_prob' key entirely in the route response."
        )
        assert actual_row["mc_prob"] == expected_row["mc_prob"], (
            f"AC-2 FAIL: row {i} ({expected_row['time']}) mc_prob changed in "
            f"transit -- stored {expected_row['mc_prob']!r}, route returned "
            f"{actual_row['mc_prob']!r}. Pre-trigger/triggering-cycle real "
            f"floats and post-trigger sentinels must both survive the route "
            f"byte-identical."
        )
        if expected_row["mc_prob"] is None:
            assert actual_row["mc_prob"] is None, (
                f"AC-2 FAIL: row {i} sentinel coerced to a non-None falsy "
                f"value ({actual_row['mc_prob']!r}) -- '== None' would pass "
                f"accidentally for 0 too; this stricter identity check is "
                f"the real contract."
            )
