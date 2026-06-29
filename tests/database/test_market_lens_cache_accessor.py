"""
RED tests for database.get_latest_market_lens_cache() accessor.
DE-ADVISOR-LATENCY: Cache-Serve Market Lenses.

Coverage:
  L-1  get_latest_market_lens_cache() returns None when no MARKET_LENS_CACHE rows exist.
  L-2  get_latest_market_lens_cache() returns the latest MARKET_LENS_CACHE row as a
       parsed dict (raw_response deserialized from JSON); returns the NEWEST row
       (highest id) when multiple rows exist.
  L-3  get_latest_market_lens_cache() ignores rows with other advisor_roles:
       MARKET_PRISM, MARKET_PRISM_SOURCES, OVERFITTING_CONSCIENCE, etc. must be invisible.
  L-4  get_latest_market_lens_cache() returns a well-shaped row dict with expected keys:
       id (positive int), advisor_role == 'MARKET_LENS_CACHE', raw_response (dict),
       created_at (non-None).
  L-5  get_latest_market_lens_cache() uses get_ro_connection() — read-only at driver level
       per architecture constraint 5 (source-code inspection check).
  L-6  D-1: get_latest_market_lens_cache() never raises — any DB error degrades to None.

All tests fail today with AttributeError: module 'database' has no attribute
'get_latest_market_lens_cache' — the accessor is not yet implemented.

DB isolation: the global conftest._isolate_db autouse fixture + pytest_configure()
redirect DB_PATH to a per-test tempfile. Every test starts with a clean, fully-migrated DB.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone

import pytest

import database as db_module

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ROLE = "MARKET_LENS_CACHE"

_CACHE_RAW_FRESH = {
    "captured_at": "2026-06-29T03:00:00+00:00",
    "lenses": {
        "technicals": {
            "lens": "technicals",
            "available": True,
            "payload": {"ma_posture": None, "breadth": None, "momentum": None},
            "sources": [],
        },
        "sentiment": {
            "lens": "sentiment",
            "available": False,
            "reason": "unit-test-stub",
            "payload": None,
            "sources": [],
        },
        "derivatives": {
            "lens": "derivatives",
            "available": False,
            "reason": "unit-test-stub",
            "payload": None,
            "sources": [],
        },
        "macro": {
            "lens": "macro",
            "available": False,
            "reason": "unit-test-stub",
            "payload": None,
            "sources": [],
        },
        "fundamentals": {
            "lens": "fundamentals",
            "available": False,
            "reason": "unit-test-stub",
            "payload": None,
            "sources": [],
        },
    },
}


def _insert_cache_row(raw: dict | None = None) -> int:
    """Insert a MARKET_LENS_CACHE observation row and return its row id."""
    payload = raw if raw is not None else dict(_CACHE_RAW_FRESH)
    return db_module.insert_advisor_observation(
        advisor_role=_ROLE,
        subject_type="portfolio",
        subject_id="global",
        verdict=None,
        raw_response=payload,
        symphony_id="",
    )


def _insert_other_role_row(role: str = "MARKET_PRISM") -> int:
    """Insert a row with a different advisor_role as noise."""
    return db_module.insert_advisor_observation(
        advisor_role=role,
        subject_type="portfolio",
        subject_id="global",
        verdict="neutral",
        raw_response={"run_id": "noise-row", "per_lens_digest": {}},
        symphony_id="",
    )


# ---------------------------------------------------------------------------
# L-1: returns None when absent
# ---------------------------------------------------------------------------


def test_get_latest_market_lens_cache_returns_none_when_no_rows_exist():
    """L-1: get_latest_market_lens_cache() must return None when the table has no
    MARKET_LENS_CACHE rows.

    Fails today with AttributeError — the accessor is not yet implemented.
    """
    result = db_module.get_latest_market_lens_cache()

    assert result is None, (
        "get_latest_market_lens_cache() must return None when no MARKET_LENS_CACHE "
        f"rows exist in the DB; got {result!r}. "
        "The accessor is not yet implemented in database.py."
    )


# ---------------------------------------------------------------------------
# L-2: returns the latest row; multiple rows → newest wins
# ---------------------------------------------------------------------------


def test_get_latest_market_lens_cache_returns_latest_row_when_one_exists():
    """L-2a: When exactly one MARKET_LENS_CACHE row exists, it is returned
    with raw_response deserialized from JSON (not a raw string).

    Fails today with AttributeError — accessor not implemented.
    """
    _insert_cache_row()

    result = db_module.get_latest_market_lens_cache()

    assert result is not None, (
        "get_latest_market_lens_cache() returned None despite one MARKET_LENS_CACHE "
        "row being present. The accessor is not yet implemented or does not find the row."
    )
    assert result.get("advisor_role") == _ROLE, (
        f"Returned row must have advisor_role='{_ROLE}'; got {result.get('advisor_role')!r}"
    )
    raw = result.get("raw_response")
    assert isinstance(raw, dict), (
        f"raw_response must be a deserialized dict, not {type(raw).__name__!r}. "
        "The accessor must call json.loads() on the stored JSON string."
    )
    assert "captured_at" in raw, (
        "raw_response must carry 'captured_at' — the persisted timestamp from the producer. "
        f"Keys present: {list(raw.keys())!r}"
    )
    assert "lenses" in raw, (
        "raw_response must carry 'lenses' — the dict of structured lens payloads. "
        f"Keys present: {list(raw.keys())!r}"
    )


def test_get_latest_market_lens_cache_returns_newest_row_when_multiple_exist():
    """L-2b: When multiple MARKET_LENS_CACHE rows exist, the accessor returns
    the one with the highest id (newest insertion).

    Verifies ORDER BY id DESC LIMIT 1 semantics.
    Fails today with AttributeError — accessor not implemented.
    """
    raw_old = dict(_CACHE_RAW_FRESH)
    raw_old["captured_at"] = "2026-06-28T03:00:00+00:00"  # older
    id_old = _insert_cache_row(raw=raw_old)

    raw_new = dict(_CACHE_RAW_FRESH)
    raw_new["captured_at"] = "2026-06-29T03:00:00+00:00"  # newer
    id_new = _insert_cache_row(raw=raw_new)

    result = db_module.get_latest_market_lens_cache()

    assert result is not None, "get_latest_market_lens_cache() returned None with two rows present"
    returned_id = result.get("id")
    assert returned_id == id_new, (
        f"get_latest_market_lens_cache() must return the NEWEST row (id={id_new}), "
        f"got id={returned_id}. Use ORDER BY id DESC LIMIT 1."
    )
    raw = result.get("raw_response", {})
    assert raw.get("captured_at") == "2026-06-29T03:00:00+00:00", (
        f"Returned row must have the newest captured_at; got {raw.get('captured_at')!r}"
    )


# ---------------------------------------------------------------------------
# L-3: ignores other advisor roles
# ---------------------------------------------------------------------------


def test_get_latest_market_lens_cache_ignores_market_prism_rows():
    """L-3a: MARKET_PRISM rows must be invisible to get_latest_market_lens_cache().

    GIVEN only a MARKET_PRISM row (no MARKET_LENS_CACHE row),
    WHEN get_latest_market_lens_cache() is called,
    THEN it must return None.

    Fails today with AttributeError — accessor not implemented.
    """
    _insert_other_role_row(role="MARKET_PRISM")

    result = db_module.get_latest_market_lens_cache()

    assert result is None, (
        "get_latest_market_lens_cache() must ignore MARKET_PRISM rows and return None "
        f"when only MARKET_PRISM rows exist; got {result!r}. "
        "The accessor must filter WHERE advisor_role = 'MARKET_LENS_CACHE'."
    )


def test_get_latest_market_lens_cache_ignores_market_prism_sources_rows():
    """L-3b: MARKET_PRISM_SOURCES rows must be invisible to get_latest_market_lens_cache().

    Fails today with AttributeError — accessor not implemented.
    """
    _insert_other_role_row(role="MARKET_PRISM_SOURCES")

    result = db_module.get_latest_market_lens_cache()

    assert result is None, (
        f"get_latest_market_lens_cache() must ignore MARKET_PRISM_SOURCES rows. Got {result!r}."
    )


def test_get_latest_market_lens_cache_returns_cache_row_among_noise():
    """L-3c: When MARKET_LENS_CACHE and other-role rows coexist (MARKET_PRISM inserted AFTER,
    so higher id), the accessor must return the MARKET_LENS_CACHE row — not the noise.

    Adversarial: noise row has a higher id, so a non-filtered ORDER BY id DESC would
    return the noise row.

    Fails today with AttributeError — accessor not implemented.
    """
    id_cache = _insert_cache_row()
    _insert_other_role_row(role="MARKET_PRISM")  # higher id, must be ignored

    result = db_module.get_latest_market_lens_cache()

    assert result is not None, (
        "get_latest_market_lens_cache() returned None despite a MARKET_LENS_CACHE row existing. "
        "Check that the WHERE advisor_role filter is correct."
    )
    assert result.get("advisor_role") == _ROLE, (
        f"Returned row must be a MARKET_LENS_CACHE row; got advisor_role={result.get('advisor_role')!r}. "
        "The accessor must filter to advisor_role='MARKET_LENS_CACHE' — not just ORDER BY id DESC."
    )
    assert result.get("id") == id_cache, (
        f"Returned id must be the MARKET_LENS_CACHE row (id={id_cache}); got id={result.get('id')}."
    )


# ---------------------------------------------------------------------------
# L-4: returned row has expected shape keys
# ---------------------------------------------------------------------------


def test_get_latest_market_lens_cache_row_has_expected_shape():
    """L-4: The returned dict must carry the standard advisor_observations columns.

    Required keys: id (positive int), advisor_role == 'MARKET_LENS_CACHE',
    raw_response (dict), created_at (non-None).

    Fails today with AttributeError — accessor not implemented.
    """
    _insert_cache_row()

    result = db_module.get_latest_market_lens_cache()
    assert result is not None, "Precondition: row must exist"

    for key in ("id", "advisor_role", "raw_response", "created_at"):
        assert key in result, (
            f"Returned MARKET_LENS_CACHE row is missing key {key!r}. "
            f"Row keys: {list(result.keys())!r}"
        )

    assert isinstance(result["id"], int) and result["id"] > 0, (
        f"'id' must be a positive int; got {result['id']!r}"
    )
    assert result["advisor_role"] == _ROLE, (
        f"advisor_role must be '{_ROLE}'; got {result['advisor_role']!r}"
    )
    assert isinstance(result["raw_response"], dict), (
        f"raw_response must be a parsed dict; got {type(result['raw_response']).__name__!r}"
    )
    assert result["created_at"] is not None, "created_at must not be None"


# ---------------------------------------------------------------------------
# L-5: uses get_ro_connection (source-code check)
# ---------------------------------------------------------------------------


def test_get_latest_market_lens_cache_uses_ro_connection():
    """L-5: get_latest_market_lens_cache() must use get_ro_connection() — read-only at the
    driver level per architecture constraint 5.

    This is a source-code inspection test. If the accessor is not yet implemented,
    this test fails with AttributeError (which is the correct RED failure).
    """
    assert hasattr(db_module, "get_latest_market_lens_cache"), (
        "database.py must export get_latest_market_lens_cache() — "
        "the accessor is not yet implemented."
    )

    source = inspect.getsource(db_module.get_latest_market_lens_cache)
    assert "get_ro_connection" in source, (
        "get_latest_market_lens_cache() must use get_ro_connection() for its DB query. "
        "Read-only query paths use get_ro_connection() per architecture constraint 5. "
        f"Source inspected:\n{source}"
    )
    assert "get_connection()" not in source, (
        "get_latest_market_lens_cache() must not call get_connection() — "
        "use get_ro_connection() for all read-only paths."
    )


# ---------------------------------------------------------------------------
# L-6: D-1 — accessor never raises
# ---------------------------------------------------------------------------


def test_get_latest_market_lens_cache_never_raises_on_empty_table(monkeypatch):
    """L-6: get_latest_market_lens_cache() must never raise — degrading to None on any error.

    Simulates a cursor.fetchone() returning None (empty table). Since real DB I/O
    uses the isolated temp DB, this test just calls the function and asserts it does
    not raise, covering the normal empty-table case.

    Fails today with AttributeError — accessor not implemented.
    """
    try:
        result = db_module.get_latest_market_lens_cache()
    except Exception as exc:
        if isinstance(exc, AttributeError) and "get_latest_market_lens_cache" in str(exc):
            pytest.fail(
                "get_latest_market_lens_cache() does not exist in database.py — "
                "implement it (SELECT ... WHERE advisor_role='MARKET_LENS_CACHE' "
                "ORDER BY id DESC LIMIT 1, returns None when absent, D-1 never-raises)."
            )
        pytest.fail(
            f"get_latest_market_lens_cache() must never raise; got {type(exc).__name__}: {exc}"
        )

    assert result is None, (
        "Empty table: expected None, got a row. Something seeded data unexpectedly."
    )
