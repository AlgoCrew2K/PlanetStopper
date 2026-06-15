"""
RED-phase tests for Change C — the /api/chart/<symphony_id> route must pass
the new ``breakeven`` and ``vwap`` overlay keys through into its JSON
response.

Cycle context
-------------
Changes A & B make ``alpha_bot_execution.main()`` emit ``breakeven`` and
``vwap`` keys into each ``chart_history`` row. Change C is the consumer side:
the dashboard route ``/api/chart/<symphony_id>`` reads ``chart_history`` and
serves it to the frontend overlay datasets. The route must surface BOTH new
keys in every row of its ``data`` array — otherwise the engine emits the
values but the frontend never receives them.

Two response paths exist in the route and BOTH are covered:
  1. chart_history HAS rows for the symphony -> the route returns those rows
     (merged with intraday ``held`` data when shadow_history exists).
  2. chart_history rows merged with a non-empty shadow_history ``held`` map.

The overlay keys originate in chart_history, so the contract is: whatever
keys a chart_history row carries, the route must NOT drop ``breakeven`` or
``vwap`` from the served payload.

Provenance
----------
These tests assert SHAPE and PASSTHROUGH, not producer math: the input
chart_history rows are authored by the test (sentinel overlay values), and
each assertion checks the same value survives into the response. No engine
math runs here; the engine-side derivation is covered by
tests/execution/test_chart_overlay_emission.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import app as app_module


@pytest.fixture
def client():
    """Flask test client bound to the in-process app instance."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database():
    """Patch the ``database`` namespace referenced by app.py.

    Mirrors the fixture in test_app_routes.py. ``get_ro_connection`` returns a
    MagicMock; tests that need shadow_history rows configure its ``execute``
    chain explicitly.
    """
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.load_chart_history.return_value = {"date": "2026-05-01", "symphonies": {}}
        db_mock.get_ro_connection.return_value = MagicMock()
        yield db_mock


# Sentinel overlay values authored by the test. Distinct, non-zero where it
# matters, so a dropped or aliased key is unambiguous in the assertion.
_BREAKEVEN_LOCKED_VALUE = 0.0  # breakeven overlay when locked (entry-relative axis)
_VWAP_OVERLAY_VALUE = 3.25  # arbitrary sentinel for the derived VWAP overlay


def _chart_row(time_str: str, breakeven, vwap_overlay):
    """A chart_history row carrying the two new overlay keys plus the
    pre-existing keys the engine already writes."""
    return {
        "time": time_str,
        "return": 5.0,
        "stop": 1.0,
        "event": None,
        "mc_prob": 42.0,
        "vol": 0.2,
        "vwap_diff": 0.0125,
        "base_atr_pct": 0.0,
        "dynamic_multiplier": 1.0,
        "breakeven": breakeven,
        "vwap": vwap_overlay,
    }


def test_chart_route_passes_breakeven_key_through(client, mock_database):
    """Every row in the route's ``data`` array must include ``breakeven``."""
    mock_database.load_chart_history.return_value = {
        "symphonies": {
            "sym_x": [
                _chart_row("11:30", _BREAKEVEN_LOCKED_VALUE, _VWAP_OVERLAY_VALUE),
            ]
        }
    }

    resp = client.get("/api/chart/sym_x")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"
    assert body["data"], "route returned an empty data array"

    row = body["data"][0]
    assert "breakeven" in row, (
        "/api/chart response row is missing the 'breakeven' key — the engine "
        "emits it into chart_history but the route dropped it. The frontend "
        f"Breakeven overlay would receive nothing. Row keys: {sorted(row.keys())}"
    )
    assert row["breakeven"] == _BREAKEVEN_LOCKED_VALUE, (
        "'breakeven' value was altered in transit through the route. "
        f"Expected {_BREAKEVEN_LOCKED_VALUE}, got {row['breakeven']!r}."
    )


def test_chart_route_passes_vwap_key_through(client, mock_database):
    """Every row in the route's ``data`` array must include ``vwap``."""
    mock_database.load_chart_history.return_value = {
        "symphonies": {
            "sym_x": [
                _chart_row("11:30", _BREAKEVEN_LOCKED_VALUE, _VWAP_OVERLAY_VALUE),
            ]
        }
    }

    resp = client.get("/api/chart/sym_x")
    assert resp.status_code == 200
    body = resp.get_json()
    row = body["data"][0]

    assert "vwap" in row, (
        "/api/chart response row is missing the 'vwap' key — the engine emits "
        "it into chart_history but the route dropped it. The frontend VWAP "
        f"overlay would receive nothing. Row keys: {sorted(row.keys())}"
    )
    assert row["vwap"] == _VWAP_OVERLAY_VALUE, (
        "'vwap' overlay value was altered in transit through the route. "
        f"Expected {_VWAP_OVERLAY_VALUE}, got {row['vwap']!r}."
    )


def test_chart_route_passes_both_keys_through_every_row(client, mock_database):
    """Multi-row history: BOTH keys survive in EVERY row, with per-row values
    preserved (None breakeven on an unlocked tick, 0.0 on a locked tick)."""
    mock_database.load_chart_history.return_value = {
        "symphonies": {
            "sym_x": [
                _chart_row("11:30", None, 1.5),
                _chart_row("11:31", _BREAKEVEN_LOCKED_VALUE, 2.0),
                _chart_row("11:32", _BREAKEVEN_LOCKED_VALUE, 2.5),
            ]
        }
    }

    resp = client.get("/api/chart/sym_x")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert len(data) == 3, f"expected 3 rows, got {len(data)}"

    expected_breakeven = [None, 0.0, 0.0]
    expected_vwap = [1.5, 2.0, 2.5]
    for i, row in enumerate(data):
        assert "breakeven" in row and "vwap" in row, (
            f"row {i} dropped an overlay key: {sorted(row.keys())}"
        )
        assert row["breakeven"] == expected_breakeven[i], (
            f"row {i} 'breakeven' mismatch: expected {expected_breakeven[i]!r}, "
            f"got {row['breakeven']!r}. Per-row overlay values must be "
            "preserved exactly (None gap vs 0.0 locked)."
        )
        assert row["vwap"] == expected_vwap[i], (
            f"row {i} 'vwap' mismatch: expected {expected_vwap[i]!r}, got {row['vwap']!r}."
        )


def test_chart_route_passes_overlay_keys_through_when_held_data_merged(client, mock_database):
    """When shadow_history supplies an intraday ``held`` map, the route merges
    it into each chart_history row via a dict spread. The overlay keys must
    survive that merge — a spread that rebuilt rows from a fixed key list
    would silently drop them.

    We configure ``get_ro_connection().execute().fetchall()`` to return one
    shadow_history row whose hh:mm matches the chart row's ``time`` so the
    merge branch (``held_by_time`` truthy) is exercised.
    """
    ro_conn = MagicMock()
    # shadow_history row tuple shape: (hhmm, shadow_return, current_return)
    ro_conn.execute.return_value.fetchall.return_value = [("11:30", 4.0, 5.0)]
    mock_database.get_ro_connection.return_value = ro_conn

    mock_database.load_chart_history.return_value = {
        "symphonies": {
            "sym_x": [
                _chart_row("11:30", _BREAKEVEN_LOCKED_VALUE, _VWAP_OVERLAY_VALUE),
            ]
        }
    }

    resp = client.get("/api/chart/sym_x")
    assert resp.status_code == 200
    data = resp.get_json()["data"]
    assert data, "route returned empty data on the held-merge path"
    row = data[0]

    # The merge must ADD 'held' without DROPPING the overlay keys.
    assert "held" in row, (
        "expected the held-merge branch to run (shadow_history row supplied), "
        f"but 'held' is absent. Row keys: {sorted(row.keys())}"
    )
    assert "breakeven" in row and "vwap" in row, (
        "the held-data merge dropped overlay keys. A dict spread over the "
        "chart row must preserve 'breakeven' and 'vwap'. "
        f"Row keys: {sorted(row.keys())}"
    )
    assert row["breakeven"] == _BREAKEVEN_LOCKED_VALUE, (
        f"'breakeven' altered by the held-merge. Got {row['breakeven']!r}."
    )
    assert row["vwap"] == _VWAP_OVERLAY_VALUE, (
        f"'vwap' altered by the held-merge. Got {row['vwap']!r}."
    )
