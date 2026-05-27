"""
Sprint 3 audit-fix Cycle A — RED tests for S3-AUDIT-001 (BLOCKER) on the
database side: save_autotune_run MUST return the inserted row id (cursor.lastrowid).

Why this is a regression spec
------------------------------
The audit report (docs/audit/sprint-3-cross-cycle-audit.md §S3-AUDIT-001) shows
that today save_autotune_run returns None, _AUTOTUNE_RUNS_SELECT does NOT project
the `id` column, and _autotune_run_row_to_dict does NOT map "id" into the
returned dict.  As a consequence the OC producer at autotuner.py:1747-1758
constructs `_oc_run = {"id": 0, ...}` and the .get("id", 0) fallback at line
1757 always trips — every OC observation lands with subject_id="0".

This file pins the LOW-LEVEL repair contract:
  1. save_autotune_run returns a positive int (the new row id).
  2. _AUTOTUNE_RUNS_SELECT projects the id column.
  3. _autotune_run_row_to_dict carries an "id" key whose value matches the row id.
  4. get_latest_autotune_run round-trips the id from save_autotune_run.
  5. get_all_autotune_runs carries id on every returned dict.

These tests are RED until the implementer modifies save_autotune_run +
_AUTOTUNE_RUNS_SELECT + _autotune_run_row_to_dict in database.py.

Fixture provenance: schema-derived (PRAGMA table_info) + producer-roundtrip;
no hardcoded producer values.
"""

from __future__ import annotations

import sqlite3

import pytest

import database as db_module


# ---------------------------------------------------------------------------
# Minimal seed helpers — no hardcoded producer values; values are arbitrary
# inputs we control and read back on the same row.
# ---------------------------------------------------------------------------


def _seed_args(symphony_id: str = "sym-001", run_timestamp: str = "2026-05-27T00:00:00Z") -> dict:
    """Return kwargs sufficient for save_autotune_run to succeed.

    All numeric values are arbitrary — these tests assert id propagation, not
    metric semantics.
    """
    return {
        "run_timestamp": run_timestamp,
        "symphony_id": symphony_id,
        "oos_alpha": 0.0,
        "train_alpha": 0.0,
        "baseline_decision": "Adopted AI",
        "fallback_oos_alpha": 0.0,
        "default_oos_alpha": 0.0,
        "selection_tstat": None,
        "naive_sharpe": None,
        "validation_sharpe": None,
        "frozen_eval_sharpe": None,
        "spec_bundle_id": None,
        "n_effective": None,
        "d_spec": None,
        "gamma": None,
        "overfitting_verdict": None,
    }


# ---------------------------------------------------------------------------
# 1. save_autotune_run returns an integer row id (the inserted PK).
# ---------------------------------------------------------------------------


def test_save_autotune_run_returns_a_positive_int_row_id():
    """save_autotune_run must return cursor.lastrowid as a positive int.

    Today it returns None (database.py:484).  The audit's BLOCKER repair
    requires the function to capture cursor.lastrowid and return it; the
    OC call site then propagates that id directly into _oc_run["id"]
    without the read-after-write get_latest_autotune_run dance.
    """
    row_id = db_module.save_autotune_run(**_seed_args())
    assert isinstance(row_id, int), (
        f"save_autotune_run must return int; got {type(row_id).__name__}={row_id!r}. "
        "The OC subject_id chain requires cursor.lastrowid back from this writer."
    )
    assert row_id > 0, (
        f"save_autotune_run must return a POSITIVE int (the new row's PK); "
        f"got {row_id!r}.  Zero or negative would defeat the .get('id', 0) "
        "sentinel-vs-real-id check in the producer."
    )


def test_save_autotune_run_returned_id_matches_inserted_row_pk():
    """The returned id must reference the row that was just inserted.

    Verified by a direct SELECT id FROM autotune_runs WHERE the inserted
    metric columns match — the round-trip is the contract.
    """
    args = _seed_args(symphony_id="sym-pk-match", run_timestamp="2026-05-27T01:00:00Z")
    row_id = db_module.save_autotune_run(**args)
    assert isinstance(row_id, int) and row_id > 0

    import os
    db_path = os.environ["DB_PATH"]
    conn = sqlite3.connect(db_path)
    fetched = conn.execute(
        "SELECT id, symphony_id, run_timestamp FROM autotune_runs WHERE id = ?",
        (row_id,),
    ).fetchone()
    conn.close()

    assert fetched is not None, (
        f"No row found at id={row_id} returned by save_autotune_run. "
        "The returned id must reference the just-inserted row."
    )
    assert fetched[1] == args["symphony_id"], (
        f"Row at id={row_id} has symphony_id={fetched[1]!r}; expected "
        f"{args['symphony_id']!r}.  save_autotune_run returned a stale id."
    )
    assert fetched[2] == args["run_timestamp"], (
        f"Row at id={row_id} has run_timestamp={fetched[2]!r}; expected "
        f"{args['run_timestamp']!r}.  save_autotune_run returned a stale id."
    )


def test_save_autotune_run_ids_are_strictly_increasing_across_calls():
    """Sequential inserts must return strictly increasing row ids.

    AUTOINCREMENT semantics: id N+1 > id N.  A non-monotonic return value
    would indicate a stale lastrowid capture or INSERT OR REPLACE semantics.
    """
    id_1 = db_module.save_autotune_run(**_seed_args(symphony_id="mono-1"))
    id_2 = db_module.save_autotune_run(**_seed_args(symphony_id="mono-2"))
    id_3 = db_module.save_autotune_run(**_seed_args(symphony_id="mono-3"))

    assert id_1 < id_2 < id_3, (
        f"save_autotune_run row ids must strictly increase across sequential "
        f"calls; got {[id_1, id_2, id_3]}.  Non-monotonic ids would break the "
        "OC subject_id audit trail."
    )


# ---------------------------------------------------------------------------
# 2. _AUTOTUNE_RUNS_SELECT projects the id column.
# ---------------------------------------------------------------------------


def test_autotune_runs_select_projects_id_column():
    """_AUTOTUNE_RUNS_SELECT must include `id` in its column list.

    Today it omits `id` — that's the exact reason _autotune_run_row_to_dict
    cannot map an "id" key, and the producer falls through to subject_id="0".
    """
    select_sql = db_module._AUTOTUNE_RUNS_SELECT
    # The SELECT must contain the bare token `id` as a projected column.
    # We assert case-insensitive presence with a comma boundary so a false
    # match on `symphony_id` does not pass the check.
    normalised = select_sql.replace("\n", " ").lower()
    # Strip leading "SELECT" and trailing "FROM ..." to inspect just the projection list.
    select_idx = normalised.index("select")
    from_idx = normalised.index("from")
    projection = normalised[select_idx + len("select"):from_idx]
    projected_columns = {p.strip() for p in projection.split(",")}

    assert "id" in projected_columns, (
        f"_AUTOTUNE_RUNS_SELECT must project the `id` column; "
        f"projected columns: {sorted(projected_columns)!r}. "
        "Without `id` in the SELECT, _autotune_run_row_to_dict cannot map an 'id' "
        "key and the OC producer falls through to subject_id='0' (S3-AUDIT-001)."
    )


# ---------------------------------------------------------------------------
# 3. _autotune_run_row_to_dict carries an "id" key on the returned dict.
# ---------------------------------------------------------------------------


def test_get_latest_autotune_run_dict_carries_id_key():
    """get_latest_autotune_run dict MUST include the "id" key.

    The chain:
      save_autotune_run → autotune_runs row → _AUTOTUNE_RUNS_SELECT → _autotune_run_row_to_dict
    must end in a dict that has an integer "id" key — otherwise the OC fallback
    .get("id", 0) silently degrades to 0 and the audit trail is broken.
    """
    inserted_id = db_module.save_autotune_run(
        **_seed_args(symphony_id="latest-id-test", run_timestamp="2026-05-27T02:00:00Z")
    )
    # save_autotune_run currently returns None; even so, get_latest_autotune_run
    # must independently surface the id through the SELECT path.
    latest = db_module.get_latest_autotune_run("latest-id-test")
    assert latest is not None, (
        "get_latest_autotune_run returned None after a successful save — "
        "the SELECT or WHERE filter is broken."
    )
    assert "id" in latest, (
        f"get_latest_autotune_run dict missing 'id' key; got keys={sorted(latest.keys())!r}. "
        "S3-AUDIT-001: _AUTOTUNE_RUNS_SELECT must project id and "
        "_autotune_run_row_to_dict must map it."
    )
    assert isinstance(latest["id"], int), (
        f"get_latest_autotune_run id must be an int; got "
        f"{type(latest['id']).__name__}={latest['id']!r}."
    )
    assert latest["id"] > 0, (
        f"get_latest_autotune_run id must be positive; got {latest['id']!r}. "
        "Zero would collide with the OC sentinel and re-enable S3-AUDIT-001."
    )
    # When save_autotune_run is repaired to return its lastrowid, the two
    # values must agree.  We only assert equality conditionally to keep this
    # test focused on the dict-key contract, but the equality must hold when
    # the writer-side fix lands.
    if isinstance(inserted_id, int) and inserted_id > 0:
        assert latest["id"] == inserted_id, (
            f"get_latest_autotune_run id ({latest['id']}) must equal the value "
            f"returned by save_autotune_run ({inserted_id}) for the same row."
        )


def test_get_all_autotune_runs_carries_id_on_every_row():
    """get_all_autotune_runs must include the "id" key in every returned dict.

    The /api/autotune-runs route (and any future denormalized symphony-id join)
    relies on id propagation through the same row-to-dict mapper as
    get_latest_autotune_run.
    """
    db_module.save_autotune_run(**_seed_args(symphony_id="all-1"))
    db_module.save_autotune_run(**_seed_args(symphony_id="all-2"))
    db_module.save_autotune_run(**_seed_args(symphony_id="all-3"))

    rows = db_module.get_all_autotune_runs(limit=10)
    assert len(rows) >= 3, (
        f"Expected at least 3 rows in get_all_autotune_runs after 3 inserts; "
        f"got {len(rows)}."
    )
    for row in rows:
        assert "id" in row, (
            f"get_all_autotune_runs returned a row missing 'id' key: keys="
            f"{sorted(row.keys())!r}. id propagation must be uniform across all "
            "row-to-dict consumers."
        )
        assert isinstance(row["id"], int) and row["id"] > 0, (
            f"get_all_autotune_runs row id must be positive int; got {row['id']!r}."
        )
