"""RED: ``GET /api/armed-symphonies``, the small read the flush confirm opens with.

CONTRACT PINNED
* Body ``{"armed": [{"name": <display name or key>, "flags": [...], "state": "live"}, ...]}``, built
  from the LIVE ``bot_state`` (``database.load_state()``), never the frozen close snapshot.
* An entry is listed when it is a symphony entry (``app._is_symphony_state_entry``, so the metadata
  keys ``date``, ``last_execution_mode``, ``last_market_close_snapshot``,
  ``last_successful_cycle_at`` and ``post_mortem_run`` are never listed) and at least one of its flags
  is truthy. ``flags`` is the truthy ones in the order ``ARMED`` (``armed``), ``TP`` (``tp_armed``),
  ``PARA`` (``para_armed``), ``TRIGGERED`` (``triggered``). These are the entries the flush resets.
* The answer is the same in the open and the closed-frozen market states, is under 100 KB for the
  captured eleven symphonies, is behind the auth gate, GET-only and opens no read-write connection.

The state is the captured production ``bot_state`` (``tests/fixtures/prod_droplet/
bot_state_positions.json``, written through ``database.save_state``) with four named-field edits of
real records (arming flags, which the capture omits): see ``tests/fixtures/refresh_timing/04-c6/
PROVENANCE.md``. Expected entries are recomputed here from the seeded state, never typed.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import app as app_module
import database
from tests._ux_truth_support import load_captured_bot_state

ROUTE = "/api/armed-symphonies"
MAX_BYTES = 100 * 1024
FLAGS = (("armed", "ARMED"), ("tp_armed", "TP"), ("para_armed", "PARA"), ("triggered", "TRIGGERED"))
METADATA = {
    "date": "2026-10-22",
    "last_execution_mode": "dry_run",
    "last_successful_cycle_at": "2026-10-22T10:42:40-04:00",
    "post_mortem_run": "2026-10-21",
    "last_market_close_snapshot": {"frozen_at": "2026-10-21T16:00:02-04:00", "armed": True},
}


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def state():
    """Captured symphonies with arming flags set on four of them (named-field edits)."""
    symphonies = load_captured_bot_state()
    ids = sorted(k for k, v in symphonies.items() if isinstance(v, dict) and "name" in v)
    symphonies[ids[0]]["armed"] = True
    symphonies[ids[1]]["tp_armed"] = True
    symphonies[ids[1]]["para_armed"] = True
    symphonies[ids[2]]["triggered"] = True
    # Present but with every flag off: must not be listed. The capture itself carries
    # ``triggered=True`` on most symphonies, so this one is switched off on purpose.
    for field, _label in FLAGS:
        symphonies[ids[3]][field] = False
    symphonies.update(METADATA)
    database.save_state(symphonies)
    return symphonies


def expected_entries(state):
    entries = []
    for key, value in state.items():
        if not (isinstance(value, dict) and "name" in value):
            continue
        flags = [label for field, label in FLAGS if value.get(field)]
        if flags:
            entries.append({"name": value.get("name") or key, "flags": flags, "state": "live"})
    return sorted(entries, key=lambda e: e["name"])


def get(client):
    response = client.get(ROUTE)
    assert response.status_code != 404, f"GET {ROUTE} is not registered"
    assert response.status_code == 200
    return response


def listed(client):
    return sorted(get(client).get_json()["armed"], key=lambda e: e["name"])


def test_lists_the_armed_symphonies_with_their_flags_labelled_live(client, state):
    got = listed(client)
    assert got == expected_entries(state)
    names = {entry["name"] for entry in got}
    edited = [v for v in state.values() if isinstance(v, dict) and "name" in v and v.get("armed")]
    edited += [v for v in state.values() if isinstance(v, dict) and v.get("tp_armed")]
    assert len(edited) >= 2 and {v["name"] for v in edited} <= names, (
        "fixture integrity: the symphonies given arming flags are listed"
    )
    # The capture's own ``triggered`` flags are real flags the flush resets, so they are listed too.
    assert all(entry["state"] == "live" for entry in got)


def test_a_flag_that_is_off_is_not_listed(client, state):
    flag_fields = [field for field, _label in FLAGS]
    off = [
        v["name"]
        for v in state.values()
        if isinstance(v, dict) and "name" in v and not any(v.get(f) for f in flag_fields)
    ]
    assert off, "fixture integrity: one symphony has every flag off"
    assert not set(off) & {e["name"] for e in listed(client)}


@pytest.mark.parametrize("market", ["open", "closed"])
def test_the_same_in_the_open_and_the_closed_frozen_market_states(
    client, state, market, monkeypatch
):
    monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: market)
    assert listed(client) == expected_entries(state)


def test_the_close_snapshot_and_other_metadata_are_never_listed(client, state):
    names = json.dumps(listed(client))
    for key in METADATA:
        assert key not in names
    assert "frozen_at" not in names


def test_nothing_armed_is_an_empty_list(client):
    database.save_state({"sym": {"name": "Calm", "armed": False}, **METADATA})
    assert get(client).get_json() == {"armed": []}


def test_the_answer_is_small(client, state):
    assert len(get(client).get_data()) < MAX_BYTES


def test_get_only_behind_the_auth_gate_and_read_only(client, state, monkeypatch):
    assert client.post(ROUTE).status_code in (404, 405)
    assert "armed_symphonies" not in app_module._AUTH_EXEMPT_ENDPOINTS
    spy = MagicMock(side_effect=AssertionError("the armed read opened a read-write connection"))
    monkeypatch.setattr(database, "get_connection", spy)
    get(client)
    spy.assert_not_called()
    monkeypatch.setenv("DASHBOARD_PASSWORD", "placeholder-password")
    monkeypatch.setenv("SECRET_KEY", "placeholder-secret-key")
    monkeypatch.setattr(app_module, "_auth_check_enabled", True)
    with app_module.app.test_client() as anonymous:
        assert anonymous.get(ROUTE).status_code == 401


def test_a_failed_state_read_is_a_500_without_exception_text(client, monkeypatch):
    def boom():
        raise RuntimeError("cannot read /private/path/alphabot_state.db")

    monkeypatch.setattr(database, "load_state", boom)
    monkeypatch.setitem(app_module.app.config, "PROPAGATE_EXCEPTIONS", False)
    response = client.get(ROUTE)
    assert response.status_code != 404
    assert response.status_code == 500
    assert "/private/path" not in response.get_data(as_text=True)
