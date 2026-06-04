"""advisor-fix cycle — RED: AC-4 the per-symphony gear modal queries the WRONG key.

The modal route ``get_symphony_settings`` (app.py:2293) resolves the advisor-observations
query key by walking bot_state and using ``sym_key`` — the COMPOSER HASH (e.g.
'iaSOOUsmnCJHiZvbrWfs').  But advisor_observations.symphony_id stores the canonical
NORMALIZED NAME (e.g. '(invest) lqd + eyeg 5 ways full market', verified READ-ONLY
against the live DB).  Hash != name -> the modal always renders ZERO observations,
which is one half of the "every symphony looks identical/empty" complaint.

Canonical key (team-agreed, cross-cutting): symphony_id == database.normalize_name(name).
The route MUST query get_advisor_observations_for_symphony with the normalized name.

Provenance: tests/fixtures/math/advisor_identical_output_ground_truth.json (hash<->name
pairs captured READ-ONLY from the live DB).

Mocking strategy:
  * database.get_advisor_observations_for_symphony is patched to CAPTURE the key
    the route passes (the assertion target) and to return a sentinel row.
  * database.load_state / get_symphony_strategy patched to inject the hash->name
    mapping without a live DB.
  * No live API, no live DB writes; all fixtures function-scoped.
"""

from __future__ import annotations

import json
import pathlib
from unittest.mock import patch

import pytest


def _ground_truth() -> dict:
    fixture_path = (
        pathlib.Path(__file__).parents[1]
        / "fixtures"
        / "math"
        / "advisor_identical_output_ground_truth.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture(scope="function")
def flask_client():
    with patch("database.init_db"):
        import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


@pytest.fixture(scope="function")
def hash_name_pair() -> dict:
    """One real (composer_hash, name, normalized_name) triple from the live DB."""
    return _ground_truth()["hash_name_pairs"][0]


# ===========================================================================
# AC-4 — the modal route must query by the canonical normalized NAME, not hash.
# ===========================================================================


def test_modal_queries_observations_by_normalized_name_not_hash(flask_client, hash_name_pair):
    """get_symphony_settings MUST call get_advisor_observations_for_symphony with
    the canonical normalized NAME, never the Composer hash.

    We inject a bot_state whose top-level key is the Composer hash and whose
    'name' is the display name (the live shape).  The route resolves the modal
    for the normalized name; the observations accessor must be invoked with that
    normalized name — NOT the hash that the buggy code pulls from sym_key.
    """
    import database as db_module

    composer_hash = hash_name_pair["composer_hash"]
    display_name = hash_name_pair["name"]
    normalized = hash_name_pair["normalized_name"]

    # Live-shaped bot_state: hash key -> {name: DisplayName}.
    bot_state = {composer_hash: {"name": display_name, "account_uuid": "acc-1"}}

    captured = {"key": None, "calls": 0}

    def _capture_accessor(symphony_id):
        captured["calls"] += 1
        captured["key"] = symphony_id
        # Only return a row when queried by the canonical name (live behaviour).
        if symphony_id == normalized:
            return [{
                "id": 1, "created_at": "2026-06-04T00:00:00",
                "advisor_role": "OVERFITTING_CONSCIENCE", "subject_type": "autotune_run",
                "subject_id": "2156", "verdict": "WATCH", "raw_response": {},
                "is_advisory_only": 1, "spec_bundle_id": None, "symphony_id": normalized,
            }]
        return []

    with patch.object(db_module, "load_state", return_value=bot_state), \
         patch.object(db_module, "get_symphony_strategy",
                      return_value={"params": {}, "locked_vars": [], "live_mode": False}), \
         patch.object(db_module, "get_advisor_observations_for_symphony",
                      side_effect=_capture_accessor):
        resp = flask_client.get(f"/api/symphony-settings/{display_name}")

    assert resp.status_code == 200, f"got {resp.status_code}: {resp.data!r}"
    assert captured["calls"] >= 1, "observations accessor was never called."
    assert captured["key"] == normalized, (
        f"Modal queried observations with {captured['key']!r}; the canonical key "
        f"is the normalized NAME {normalized!r}. AC-4: the route must NOT pass the "
        f"Composer hash {composer_hash!r}."
    )
    assert captured["key"] != composer_hash, (
        "Modal queried observations with the Composer HASH — the exact AC-4 bug "
        "that yields 0 rows because advisor_observations is keyed by the name."
    )


def test_modal_returns_real_observation_rows_for_a_symphony(flask_client, hash_name_pair):
    """End-to-end: the modal payload's advisor_observations must contain the row
    that exists for the canonical name (not an empty list).
    """
    import database as db_module

    composer_hash = hash_name_pair["composer_hash"]
    display_name = hash_name_pair["name"]
    normalized = hash_name_pair["normalized_name"]

    bot_state = {composer_hash: {"name": display_name, "account_uuid": "acc-1"}}

    def _accessor(symphony_id):
        if symphony_id == normalized:
            return [{
                "id": 7, "created_at": "2026-06-04T00:00:00",
                "advisor_role": "OVERFITTING_CONSCIENCE", "subject_type": "autotune_run",
                "subject_id": "2156", "verdict": "WATCH", "raw_response": {"note": "x"},
                "is_advisory_only": 1, "spec_bundle_id": None, "symphony_id": normalized,
            }]
        return []

    with patch.object(db_module, "load_state", return_value=bot_state), \
         patch.object(db_module, "get_symphony_strategy",
                      return_value={"params": {}, "locked_vars": [], "live_mode": False}), \
         patch.object(db_module, "get_advisor_observations_for_symphony",
                      side_effect=_accessor):
        resp = flask_client.get(f"/api/symphony-settings/{display_name}")

    assert resp.status_code == 200
    body = resp.get_json()
    obs = body.get("advisor_observations")
    assert obs, (
        "Modal returned an empty advisor_observations list for a symphony that "
        "HAS a row under its canonical name — the AC-4 hash/name mismatch is not "
        "fixed (the route is querying by the wrong key)."
    )
    assert obs[0]["symphony_id"] == normalized
