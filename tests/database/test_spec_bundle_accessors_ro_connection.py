"""
RED tests — F-1 from rev-ocon review on 78bc707.

Contract under test:
  get_spec_bundle(bundle_hash)     must use get_ro_connection(), not get_connection()
  get_spec_bundle_by_id(id)        must use get_ro_connection(), not get_connection()

Both functions perform pure-read queries. Using get_connection() (read-write) on a
pure-read path needlessly acquires a write-lock that contends with concurrent readers
on the WAL-mode DB. The architecture mandates get_ro_connection() for all read-only
paths (database.py:58-63 docstring; project CLAUDE.md architecture constraint 3).

Enforcement strategy: patch get_connection to raise — if either accessor calls it,
the test fails. Patch get_ro_connection to return a real connection — if the accessor
calls it correctly, the test passes.

Fixture provenance: synthetic bundle_hash inserted via the accessor surface;
no producer-computed quant values.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import database as db_module

# ---------------------------------------------------------------------------
# Helper: sentinel that blows up if called
# ---------------------------------------------------------------------------


def _write_connection_forbidden(*args, **kwargs):
    """Stand-in for get_connection() — fails the test if called."""
    raise AssertionError(
        "get_connection() was called from a read-only accessor. "
        "get_spec_bundle / get_spec_bundle_by_id must use get_ro_connection()."
    )


# ===========================================================================
# F-1a — get_spec_bundle must use get_ro_connection, not get_connection
# ===========================================================================


def test_get_spec_bundle_does_not_call_get_connection(tmp_path, monkeypatch):
    """get_spec_bundle(bundle_hash) must not call get_connection().

    A write-lock acquisition on a pure-read path contends with concurrent
    execution-path reads under WAL mode. Only get_ro_connection() is correct.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()

    # Insert a bundle so the accessor has something to return.
    bundle_hash = "a" * 64  # synthetic 64-char hash
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json='{"k":"v"}')

    # Patch get_connection to blow up — the accessor must not call it.
    with patch.object(db_module, "get_connection", side_effect=_write_connection_forbidden):
        # get_ro_connection still works normally (not patched).
        result = db_module.get_spec_bundle(bundle_hash)

    assert result is not None, "get_spec_bundle must return the inserted bundle row"
    assert result["bundle_hash"] == bundle_hash


def test_get_spec_bundle_uses_ro_connection(tmp_path, monkeypatch):
    """get_spec_bundle(bundle_hash) must call get_ro_connection() at least once.

    Pairs with the previous test: absence of get_connection + presence of
    get_ro_connection = correct read-only access pattern.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()

    bundle_hash = "b" * 64
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json='{"k":"v"}')

    ro_calls: list = []
    _original_ro = db_module.get_ro_connection

    def _spy_ro(*args, **kwargs):
        ro_calls.append(1)
        return _original_ro(*args, **kwargs)

    with patch.object(db_module, "get_ro_connection", side_effect=_spy_ro):
        db_module.get_spec_bundle(bundle_hash)

    assert len(ro_calls) >= 1, (
        "get_spec_bundle must call get_ro_connection() — it was never called. "
        "Replace the get_connection() call with get_ro_connection()."
    )


# ===========================================================================
# F-1b — get_spec_bundle_by_id must use get_ro_connection, not get_connection
# ===========================================================================


def test_get_spec_bundle_by_id_does_not_call_get_connection(tmp_path, monkeypatch):
    """get_spec_bundle_by_id(id) must not call get_connection().

    Same rationale as get_spec_bundle: pure-read function, must use the
    read-only connection to avoid unnecessary write-lock contention.
    """
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()

    bundle_hash = "c" * 64
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json='{"k":"v"}')

    # Retrieve the integer id.
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()
    conn.close()
    assert row is not None, "bundle must exist in spec_bundles after insert"
    bundle_id = row[0]

    with patch.object(db_module, "get_connection", side_effect=_write_connection_forbidden):
        result = db_module.get_spec_bundle_by_id(bundle_id)

    assert result is not None, "get_spec_bundle_by_id must return the inserted bundle row"
    assert result["bundle_hash"] == bundle_hash


def test_get_spec_bundle_by_id_uses_ro_connection(tmp_path, monkeypatch):
    """get_spec_bundle_by_id(id) must call get_ro_connection() at least once."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB_FILE", db_path)
    db_module.init_db()

    bundle_hash = "d" * 64
    db_module.insert_spec_bundle(bundle_hash=bundle_hash, facets_json='{"k":"v"}')

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT id FROM spec_bundles WHERE bundle_hash = ?", (bundle_hash,)
    ).fetchone()
    conn.close()
    bundle_id = row[0]

    ro_calls: list = []
    _original_ro = db_module.get_ro_connection

    def _spy_ro(*args, **kwargs):
        ro_calls.append(1)
        return _original_ro(*args, **kwargs)

    with patch.object(db_module, "get_ro_connection", side_effect=_spy_ro):
        db_module.get_spec_bundle_by_id(bundle_id)

    assert len(ro_calls) >= 1, (
        "get_spec_bundle_by_id must call get_ro_connection() — it was never called. "
        "Replace the get_connection() call with get_ro_connection()."
    )


# ===========================================================================
# Source-inspection guard (belt-and-suspenders)
# ===========================================================================


def test_get_spec_bundle_source_uses_ro_connection():
    """Source-inspection: the get_spec_bundle function body must reference
    get_ro_connection, not get_connection.

    Inspects database.py as text after stripping comment lines — same pattern
    as the advisor wall-integrity tests. Fails immediately if the wrong
    connection function is still in the source.
    """
    import inspect

    source = inspect.getsource(db_module.get_spec_bundle)
    non_comment = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "get_ro_connection" in non_comment, (
        "get_spec_bundle must call get_ro_connection() — not found in function source. "
        "Replace get_connection() with get_ro_connection() in database.py."
    )
    assert "get_connection()" not in non_comment or "get_ro_connection()" in non_comment, (
        "get_spec_bundle calls get_connection() (read-write) on a pure-read path — "
        "replace with get_ro_connection()."
    )


def test_get_spec_bundle_by_id_source_uses_ro_connection():
    """Source-inspection: the get_spec_bundle_by_id function body must reference
    get_ro_connection, not get_connection.
    """
    import inspect

    source = inspect.getsource(db_module.get_spec_bundle_by_id)
    non_comment = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "get_ro_connection" in non_comment, (
        "get_spec_bundle_by_id must call get_ro_connection() — not found in function source. "
        "Replace get_connection() with get_ro_connection() in database.py."
    )
    assert "get_connection()" not in non_comment or "get_ro_connection()" in non_comment, (
        "get_spec_bundle_by_id calls get_connection() (read-write) on a pure-read path — "
        "replace with get_ro_connection()."
    )
