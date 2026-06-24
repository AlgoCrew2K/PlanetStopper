"""
RED tests for database.update_advisor_observation_raw_response (DE-PRISM-SOURCES-001).

The new additive UPDATE accessor patches the raw_response JSON blob of an existing
advisor_observations row by id, without requiring a DB migration (raw_response is
an existing JSON blob column).

Acceptance criteria:
  AC-7  No DB migration; additive update accessor round-trips raw_response JSON;
        non-existent row → no exception and no phantom row created.

Design:
  - Uses the _isolate_db autouse fixture from tests/conftest.py — each test starts
    with a clean, fully-migrated DB in a tempfile.
  - Inserts rows via insert_advisor_observation (the existing public write accessor)
    then calls update_advisor_observation_raw_response, then reads back via
    get_latest_market_prism_summary or a direct parameterized SELECT.
  - Never hardcodes producer-computed values; asserts shape, keys, and round-trip
    fidelity only.
"""

from __future__ import annotations

import json

import pytest

import database

# ---------------------------------------------------------------------------
# T10 — AC-7: update_advisor_observation_raw_response round-trips JSON
# ---------------------------------------------------------------------------


def test_update_advisor_observation_raw_response_round_trips_json():
    """AC-7: update_advisor_observation_raw_response(row_id, new_raw) must persist
    new_raw as a JSON-decoded dict on the row, additive (new keys present, no
    other row affected).
    """
    # Seed a MARKET_PRISM row with initial raw_response.
    initial_raw = {"run_id": "test-run-ac7", "foo": "bar"}
    row_id = database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="",
        verdict="neutral",
        raw_response=initial_raw,
        symphony_id="",
    )
    assert isinstance(row_id, int) and row_id > 0

    # Apply the patch: add an "extra" key that was not in the original.
    patched_raw = {"run_id": "test-run-ac7", "foo": "bar", "extra": "patched"}
    database.update_advisor_observation_raw_response(row_id, patched_raw)

    # Verify via get_latest_market_prism_summary (the canonical read path for prism rows).
    updated_row = database.get_latest_market_prism_summary()
    assert updated_row is not None, "Row must still be readable after update"
    assert updated_row["id"] == row_id, "Must be the same row"

    raw = updated_row["raw_response"]
    assert isinstance(raw, dict), f"raw_response must deserialise to a dict, got {type(raw)}"
    assert raw.get("extra") == "patched", (
        f"Additive key 'extra' must be present and equal 'patched'; got {raw!r}"
    )
    assert raw.get("foo") == "bar", f"Pre-existing key 'foo' must be preserved; got {raw!r}"
    assert raw.get("run_id") == "test-run-ac7", (
        f"Pre-existing key 'run_id' must be preserved; got {raw!r}"
    )


# ---------------------------------------------------------------------------
# T11 — AC-7: non-existent row → no exception + no phantom row
# ---------------------------------------------------------------------------


def test_update_advisor_observation_raw_response_nonexistent_row_is_noop():
    """AC-7: Calling update_advisor_observation_raw_response with a row_id that does
    not exist must not raise AND must not create a phantom row.
    """
    nonexistent_id = 999999

    # Must not raise.
    database.update_advisor_observation_raw_response(nonexistent_id, {"phantom": True})

    # No MARKET_PRISM row should exist after this call (DB was empty before).
    latest = database.get_latest_market_prism_summary()
    assert latest is None, (
        f"No phantom row must be created for a non-existent row_id; found row: {latest!r}"
    )


# ---------------------------------------------------------------------------
# T10b — Multiple rows: update patches ONLY the target row
# ---------------------------------------------------------------------------


def test_update_advisor_observation_raw_response_patches_only_target_row():
    """AC-7: When multiple MARKET_PRISM rows exist, update_advisor_observation_raw_response
    must only modify the targeted row_id; the other rows' raw_response must be unchanged.
    """
    raw_a = {"run_id": "run-a", "key": "value-a"}
    raw_b = {"run_id": "run-b", "key": "value-b"}

    row_id_a = database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="",
        verdict="neutral",
        raw_response=raw_a,
        symphony_id="",
    )
    row_id_b = database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="",
        verdict="neutral",
        raw_response=raw_b,
        symphony_id="",
    )

    # Patch only row_id_a.
    database.update_advisor_observation_raw_response(
        row_id_a, {"run_id": "run-a", "key": "value-a", "patched": True}
    )

    # Read row_b back directly via a read connection.
    conn = database.get_ro_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT raw_response FROM advisor_observations WHERE id = ?",
        (row_id_b,),
    )
    result = cursor.fetchone()
    conn.close()

    assert result is not None, f"Row B (id={row_id_b}) must still exist"
    raw_b_after = json.loads(result[0])
    assert raw_b_after.get("patched") is None, (
        f"Row B must not be patched; raw_response after: {raw_b_after!r}"
    )
    assert raw_b_after.get("key") == "value-b", (
        f"Row B's original data must be intact; got {raw_b_after!r}"
    )
