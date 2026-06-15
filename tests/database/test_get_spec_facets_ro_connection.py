"""
RED tests — F-1 from spec-scrit DB review on ec18745.

Contract under test:
  get_spec_facets_for_bundle(bundle_hash) must use get_ro_connection(), not get_connection()

The function performs a pure-read SELECT on spec_facets. Using get_connection()
(read-write) on a pure-read path acquires an unnecessary write-lock that contends
with concurrent readers under WAL mode. The architecture mandates get_ro_connection()
for all read-only paths (database.py:58-63 docstring; project CLAUDE.md
architecture constraint 3).

Callers include autotuner.py haircut loop (lines 1288 and 1415) and
get_or_create_phase1_theory_bundle_id (database.py:1148) — the read-only contract
matters most on the hot autotuner path.

Enforcement strategy: same pattern as test_spec_bundle_accessors_ro_connection.py —
patch get_connection to raise if called; spy on get_ro_connection to confirm it is used.
Source-inspection guard added as belt-and-suspenders.

Fixture provenance: synthetic bundle_hash inserted via the accessor surface;
no producer-computed quant values.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest

import database as db_module


# ---------------------------------------------------------------------------
# Helper: sentinel that blows up if called
# ---------------------------------------------------------------------------


def _write_connection_forbidden(*args, **kwargs):
    """Stand-in for get_connection() — fails the test if called from a read-only accessor."""
    raise AssertionError(
        "get_connection() was called from a read-only accessor. "
        "get_spec_facets_for_bundle must use get_ro_connection() — "
        "a write-lock on a pure-read path contends with concurrent WAL readers."
    )


# ===========================================================================
# F-1 — get_spec_facets_for_bundle must use get_ro_connection, not get_connection
# ===========================================================================


def test_get_spec_facets_for_bundle_does_not_call_get_connection(tmp_path, monkeypatch):
    """get_spec_facets_for_bundle(bundle_hash) must not call get_connection().

    A write-lock acquisition on a pure-read path contends with concurrent
    execution-path reads under WAL mode. Only get_ro_connection() is correct here.

    Fix: replace get_connection() at database.py:1257 with get_ro_connection().
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()

    # Insert a bundle and two facets so the accessor returns rows.
    bundle_hash = "e" * 64  # synthetic 64-char hash
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json='{"gamma":"2.0"}')
    db_module.insert_spec_bundle_facet(
        bundle_hash=bundle_hash,
        facet_name="gamma",
        facet_value="2.0",
        freeze_discipline="THEORY",
    )

    # Patch get_connection to blow up — the accessor must not call it.
    with patch.object(db_module, "get_connection", side_effect=_write_connection_forbidden):
        # get_ro_connection still works normally (not patched).
        result = db_module.get_spec_facets_for_bundle(bundle_hash)

    assert isinstance(result, list), "get_spec_facets_for_bundle must return a list"
    assert len(result) == 1, f"get_spec_facets_for_bundle returned {len(result)} rows; expected 1"
    assert result[0]["facet_name"] == "gamma"


def test_get_spec_facets_for_bundle_uses_ro_connection(tmp_path, monkeypatch):
    """get_spec_facets_for_bundle(bundle_hash) must call get_ro_connection() at least once.

    Pairs with the previous test: absence of get_connection + presence of
    get_ro_connection = correct read-only access pattern.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()

    bundle_hash = "f" * 64
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json='{"gamma":"2.0"}')

    ro_calls: list = []
    _original_ro = db_module.get_ro_connection

    def _spy_ro(*args, **kwargs):
        ro_calls.append(1)
        return _original_ro(*args, **kwargs)

    with patch.object(db_module, "get_ro_connection", side_effect=_spy_ro):
        db_module.get_spec_facets_for_bundle(bundle_hash)

    assert len(ro_calls) >= 1, (
        "get_spec_facets_for_bundle must call get_ro_connection() — it was never called. "
        "Replace the get_connection() call at database.py:1257 with get_ro_connection()."
    )


def test_get_spec_facets_for_bundle_returns_empty_for_unknown_bundle(tmp_path, monkeypatch):
    """get_spec_facets_for_bundle must return [] for an unknown bundle_hash.

    Contract unchanged from existing behaviour — this test ensures the ro_connection
    fix does not break the empty-result path.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()

    result = db_module.get_spec_facets_for_bundle("0" * 64)

    assert result == [], (
        f"get_spec_facets_for_bundle returned {result!r} for unknown hash; expected []"
    )


# ===========================================================================
# Source-inspection guard (belt-and-suspenders)
# ===========================================================================


def test_get_spec_facets_for_bundle_source_uses_ro_connection():
    """Source-inspection: get_spec_facets_for_bundle function body must reference
    get_ro_connection, not a bare get_connection call.

    Same pattern as test_spec_bundle_accessors_ro_connection.py. Fails immediately
    if the wrong connection function is still present in the source.
    """
    import inspect

    source = inspect.getsource(db_module.get_spec_facets_for_bundle)
    non_comment_lines = [line for line in source.splitlines() if not line.lstrip().startswith("#")]
    non_comment = "\n".join(non_comment_lines)

    assert "get_ro_connection" in non_comment, (
        "get_spec_facets_for_bundle must call get_ro_connection() — "
        "not found in function source. Replace get_connection() at "
        "database.py:1257 with get_ro_connection()."
    )
    # Ensure no bare get_connection() call remains (get_ro_connection contains
    # the substring 'get_connection' so we must check for the bare form).
    bare_calls = [
        line
        for line in non_comment_lines
        if "get_connection()" in line and "get_ro_connection()" not in line
    ]
    assert not bare_calls, (
        f"get_spec_facets_for_bundle still calls get_connection() on "
        f"line(s): {bare_calls}. Replace with get_ro_connection()."
    )
