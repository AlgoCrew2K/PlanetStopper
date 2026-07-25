"""
RED -- AC-8 (exit-friction-realized-savings): GET /api/exit-turnover route.

Contract (frozen in .claude/tdd-handoff.md v1, test-writer-pinned route
choice): a new sibling route (NOT folded into guard-alpha-summary) exposing
database.get_exit_turnover_stats + compute_est_annual_friction_drag_pct.
Read-only, covered by the existing global auth hook (autouse
_disable_auth_for_tests fixture handles the test side), not in the settings
write-allowlist, no LIVE_EXECUTION interaction.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import app as app_module
import database

_SYM = "sym-turnover-route-test"


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _seed(symphony_id: str, days_ago_list: list[int]) -> None:
    now_utc = datetime.now(UTC)
    for days_ago in days_ago_list:
        ts = (now_utc - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        row_id = database.record_exit_trigger(
            symphony_id=symphony_id, triggered_reason="Trailing Stop", ts_utc=ts, ts_et=ts
        )
        assert row_id is not None


def test_missing_symphony_id_returns_400_not_500(client):
    resp = client.get("/api/exit-turnover")
    assert resp.status_code == 400, (
        f"Missing symphony_id must return 400 (bad request), never a 500 or a "
        f"silent empty 200. Got {resp.status_code}."
    )
    body = resp.get_json()
    assert body is not None and "error" in body, (
        "400 response must carry a JSON error body, not an empty response."
    )


def test_empty_symphony_id_returns_400(client):
    resp = client.get("/api/exit-turnover?symphony_id=")
    assert resp.status_code == 400


def test_unknown_symphony_id_returns_200_with_zeroed_windows(client):
    """A symphony_id with zero exit_triggers rows is a valid, honest
    empty-state -- 200, not 404 (mirrors guard-alpha-summary's empty-state
    convention)."""
    resp = client.get("/api/exit-turnover?symphony_id=totally-unknown-symphony")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["symphony_id"] == "totally-unknown-symphony"
    for window_key in ("30", "90", "365"):
        assert body["windows"][window_key] == {"exit_count": 0, "coverage_days": 0}
    assert body["est_annual_friction_drag_pct"] == pytest.approx(0.0, abs=1e-9)


def test_response_shape_and_values_derive_from_seeded_rows(client):
    seed_ages = [1, 5, 45, 200]
    _seed(_SYM, seed_ages)

    resp = client.get(f"/api/exit-turnover?symphony_id={_SYM}")
    assert resp.status_code == 200
    body = resp.get_json()

    assert set(body.keys()) == {"symphony_id", "windows", "est_annual_friction_drag_pct"}, (
        f"Unexpected top-level response keys: {sorted(body.keys())}"
    )
    assert body["symphony_id"] == _SYM
    assert set(body["windows"].keys()) == {"30", "90", "365"}, (
        f"windows must have exactly the 30/90/365 string keys "
        f"(JSON serializes int dict keys as strings), got "
        f"{sorted(body['windows'].keys())}"
    )

    expected_30 = sum(1 for a in seed_ages if a <= 30)
    expected_90 = sum(1 for a in seed_ages if a <= 90)
    expected_365 = sum(1 for a in seed_ages if a <= 365)

    assert body["windows"]["30"]["exit_count"] == expected_30
    assert body["windows"]["90"]["exit_count"] == expected_90
    assert body["windows"]["365"]["exit_count"] == expected_365

    assert isinstance(body["est_annual_friction_drag_pct"], (int, float)), (
        "est_annual_friction_drag_pct must be a numeric value."
    )
    assert body["est_annual_friction_drag_pct"] >= 0.0, (
        "A friction drag estimate must be non-negative (exits_per_year and "
        "SIM_EXIT_FRICTION_PCT are both non-negative quantities)."
    )


def test_route_is_read_only_no_write_allowlist_entry():
    """AC-8: the route must not be listed in _SETTINGS_WRITE_ALLOWLIST -- it
    is advisory-only, never a config-write surface."""
    allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
    assert allowlist is not None, "app._SETTINGS_WRITE_ALLOWLIST must exist."
    assert "exit-turnover" not in str(allowlist).lower(), (
        "The exit-turnover route/keys must never appear in the settings "
        "write-allowlist -- AC-8 is a read-only surface."
    )
