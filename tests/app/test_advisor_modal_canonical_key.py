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
    """get_symphony_settings MUST resolve the Composer HASH (the live URL param)
    to the canonical normalized NAME before querying observations.

    PRODUCTION SHAPE: the gear icon calls openSymphonySettings(event, sym.id)
    where sym.id is the Composer HASH (templates/index.html:1040), so the URL is
    /api/symphony-settings/<HASH>.  advisor_observations is keyed by the lowercased
    NAME.  The route must walk bot_state hash->name and query by the NAME — NOT by
    the (normalized) hash, which matches nothing.

    This test passes the HASH as the URL param (the real scenario), not the
    display name, so it exercises the actual hash->name resolution branch.
    """
    import database as db_module

    composer_hash = hash_name_pair["composer_hash"]
    display_name = hash_name_pair["name"]
    normalized = hash_name_pair["normalized_name"]
    normalized_hash = db_module.normalize_name(composer_hash)

    # Live-shaped bot_state: hash key -> {name: DisplayName}.
    bot_state = {composer_hash: {"name": display_name, "account_uuid": "acc-1"}}

    captured = {"key": None, "calls": 0}

    def _capture_accessor(symphony_id):
        captured["calls"] += 1
        captured["key"] = symphony_id
        # Only return a row when queried by the canonical name (live behaviour).
        if symphony_id == normalized:
            return [
                {
                    "id": 1,
                    "created_at": "2026-06-04T00:00:00",
                    "advisor_role": "OVERFITTING_CONSCIENCE",
                    "subject_type": "autotune_run",
                    "subject_id": "2156",
                    "verdict": "WATCH",
                    "raw_response": {},
                    "is_advisory_only": 1,
                    "spec_bundle_id": None,
                    "symphony_id": normalized,
                }
            ]
        return []

    with (
        patch.object(db_module, "load_state", return_value=bot_state),
        patch.object(
            db_module,
            "get_symphony_strategy",
            return_value={"params": {}, "locked_vars": [], "live_mode": False},
        ),
        patch.object(
            db_module, "get_advisor_observations_for_symphony", side_effect=_capture_accessor
        ),
    ):
        # The URL param is the COMPOSER HASH — exactly what the gear icon sends.
        resp = flask_client.get(f"/api/symphony-settings/{composer_hash}")

    assert resp.status_code == 200, f"got {resp.status_code}: {resp.data!r}"
    assert captured["calls"] >= 1, "observations accessor was never called."
    assert captured["key"] == normalized, (
        f"Modal queried observations with {captured['key']!r}; the canonical key "
        f"is the normalized NAME {normalized!r}. AC-4: the route must resolve the "
        f"hash {composer_hash!r} to the name, NOT query by the hash."
    )
    # The two failure modes the bug produced: querying by the raw hash, or by the
    # normalized (lowercased) hash. Neither matches the name-keyed rows.
    assert captured["key"] not in (composer_hash, normalized_hash), (
        f"Modal queried observations with the Composer hash form {captured['key']!r} "
        "— the exact AC-4 bug that yields 0 rows because advisor_observations is "
        "keyed by the name, not the hash."
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
            return [
                {
                    "id": 7,
                    "created_at": "2026-06-04T00:00:00",
                    "advisor_role": "OVERFITTING_CONSCIENCE",
                    "subject_type": "autotune_run",
                    "subject_id": "2156",
                    "verdict": "WATCH",
                    "raw_response": {"note": "x"},
                    "is_advisory_only": 1,
                    "spec_bundle_id": None,
                    "symphony_id": normalized,
                }
            ]
        return []

    with (
        patch.object(db_module, "load_state", return_value=bot_state),
        patch.object(
            db_module,
            "get_symphony_strategy",
            return_value={"params": {}, "locked_vars": [], "live_mode": False},
        ),
        patch.object(db_module, "get_advisor_observations_for_symphony", side_effect=_accessor),
    ):
        # URL param is the Composer HASH (production scenario via the gear icon).
        resp = flask_client.get(f"/api/symphony-settings/{composer_hash}")

    assert resp.status_code == 200
    body = resp.get_json()
    obs = body.get("advisor_observations")
    assert obs, (
        "Modal returned an empty advisor_observations list for a symphony that "
        "HAS a row under its canonical name — the AC-4 hash/name mismatch is not "
        "fixed (the route is querying by the wrong key)."
    )
    assert obs[0]["symphony_id"] == normalized
