"""
RED tests for database.get_latest_market_prism_verification_for_run
(DE-PRISM-NUMERIC-VERIFY-001, AC-9).

AC-9: a byte-for-byte structural mirror of get_latest_market_prism_sources_for_run
(database.py:1205-1234) with the role string swapped to MARKET_PRISM_VERIFICATION.
No schema migration — the row is a JSON-blob advisor_observations row via the existing
insert_advisor_observation, identical to how MARKET_PRISM_SOURCES shipped.

Coverage:
  B-1  get_latest_market_prism_verification_for_run(run_id) returns the matching row.
  B-2  Returns None when the latest VERIFICATION row is from a DIFFERENT run_id
       (no stale-bleed guard, mirrors AC-9's sibling accessor).
  B-3  Returns None when no VERIFICATION rows exist at all.
  B-4  Uses get_ro_connection() (read-only path), not get_connection().
  B-5  Returned row has expected shape keys (id, advisor_role, raw_response as dict).
  B-6  Exact SQL match (json_extract) finds the target row regardless of table depth.
  B-7  get_latest_market_prism_summary() NEVER returns a MARKET_PRISM_VERIFICATION row —
       hard-filtered to advisor_role='MARKET_PRISM' (mirrors the SOURCES exclusion).

Fixture provenance: rows inserted via insert_advisor_observation (public write accessor)
with production-shape raw_response (run_id + verified_at + checks + summary + verdict per
AC-7). No hardcoded producer-computed values — assertions check shape/presence/match, not
computed classification counts.

DB isolation: conftest._isolate_db autouse fixture redirects DB_PATH to a per-test
tempfile and calls init_db(). Run protocol: targeted -n0 only.
"""

from __future__ import annotations

import inspect

import database as db_module

_RUN_ID_A = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_RUN_ID_B = "dddddddd-dddd-dddd-dddd-dddddddddddd"

_VERIFICATION_RAW_A = {
    "run_id": _RUN_ID_A,
    "verified_at": "2026-07-02T03:05:00Z",
    "checks": [
        {
            "indicator": "VIX",
            "lens": "derivatives",
            "cited_value": 21.8,
            "ground_truth_value": 22.0,
            "classification": "pass",
        },
    ],
    "summary": {"n_checks": 1, "n_pass": 1, "n_flagged": 0, "n_overridden": 0, "n_unverifiable": 0},
    "verdict": "clean",
}

_VERIFICATION_RAW_B = {
    "run_id": _RUN_ID_B,
    "verified_at": "2026-07-01T03:05:00Z",
    "checks": [],
    "summary": {"n_checks": 0, "n_pass": 0, "n_flagged": 0, "n_overridden": 0, "n_unverifiable": 0},
    "verdict": "no-numeric-claims",
}


def _insert_market_prism_row(run_id: str = _RUN_ID_A) -> int:
    """Insert a MARKET_PRISM row (production shape) and return its row id."""
    raw = {
        "run_id": run_id,
        "run_ts": run_id,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Accessor test row.",
    }
    return db_module.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="",
        verdict="neutral",
        raw_response=raw,
        symphony_id="",
    )


def _insert_verification_row(run_id: str = _RUN_ID_A, raw: dict | None = None) -> int:
    """Insert a MARKET_PRISM_VERIFICATION row and return its row id."""
    payload = dict(raw if raw is not None else _VERIFICATION_RAW_A)
    payload = dict(payload)
    payload["run_id"] = run_id
    return db_module.insert_advisor_observation(
        advisor_role="MARKET_PRISM_VERIFICATION",
        subject_type="portfolio",
        subject_id="global",
        verdict=None,
        raw_response=payload,
        symphony_id="",
    )


# ---------------------------------------------------------------------------
# B-1: returns the matching row
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_verification_for_run_returns_matching_row():
    """B-1: must return the MARKET_PRISM_VERIFICATION row whose raw_response['run_id']
    matches the requested run_id."""
    _insert_verification_row(run_id=_RUN_ID_A)

    row = db_module.get_latest_market_prism_verification_for_run(_RUN_ID_A)

    assert row is not None, (
        "get_latest_market_prism_verification_for_run must return the matching row, "
        "got None. The accessor is not yet implemented or does not match on run_id."
    )
    assert row["advisor_role"] == "MARKET_PRISM_VERIFICATION", (
        f"Returned row must have advisor_role='MARKET_PRISM_VERIFICATION'; "
        f"got {row.get('advisor_role')!r}"
    )
    raw = row.get("raw_response") or {}
    assert isinstance(raw, dict), f"raw_response must be a parsed dict; got {type(raw)}"
    assert raw.get("run_id") == _RUN_ID_A, (
        f"raw_response['run_id'] must match {_RUN_ID_A!r}; got {raw.get('run_id')!r}"
    )


# ---------------------------------------------------------------------------
# B-2: no stale-bleed on run_id mismatch
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_verification_for_run_returns_none_on_mismatch():
    """B-2: must return None when the only existing VERIFICATION row is for a
    DIFFERENT run_id — no stale-bleed guard (mirrors AC-9's sibling SOURCES accessor)."""
    _insert_verification_row(run_id=_RUN_ID_B, raw=dict(_VERIFICATION_RAW_B))

    result = db_module.get_latest_market_prism_verification_for_run(_RUN_ID_A)
    assert result is None, (
        "get_latest_market_prism_verification_for_run must return None when the only "
        f"existing VERIFICATION row is for a DIFFERENT run_id ({_RUN_ID_B!r}); "
        f"got {result!r}. Falling back to a different run's verification would show "
        "last night's checks against tonight's read."
    )


def test_get_latest_market_prism_verification_for_run_matches_correct_row_when_both_exist():
    """B-2b: rows for both run_id=A and run_id=B exist — the accessor must return the
    row for A when called with _RUN_ID_A and B when called with _RUN_ID_B."""
    _insert_verification_row(run_id=_RUN_ID_A, raw=dict(_VERIFICATION_RAW_A))
    _insert_verification_row(run_id=_RUN_ID_B, raw=dict(_VERIFICATION_RAW_B))

    row_a = db_module.get_latest_market_prism_verification_for_run(_RUN_ID_A)
    row_b = db_module.get_latest_market_prism_verification_for_run(_RUN_ID_B)

    assert row_a is not None and row_b is not None, "Both rows must be found"
    assert row_a["raw_response"]["run_id"] == _RUN_ID_A, "Cross-run mismatch for A"
    assert row_b["raw_response"]["run_id"] == _RUN_ID_B, "Cross-run mismatch for B"


# ---------------------------------------------------------------------------
# B-3: returns None when no VERIFICATION rows exist
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_verification_for_run_returns_none_when_empty():
    """B-3: no VERIFICATION rows exist at all -> None, never raise."""
    result = db_module.get_latest_market_prism_verification_for_run(_RUN_ID_A)
    assert result is None, f"Expected None when no VERIFICATION rows exist; got {result!r}"


# ---------------------------------------------------------------------------
# B-4: uses get_ro_connection (read-only path)
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_verification_for_run_uses_ro_connection():
    """B-4: must use get_ro_connection(), not get_connection() (architecture constraint 5)."""
    assert hasattr(db_module, "get_latest_market_prism_verification_for_run"), (
        "get_latest_market_prism_verification_for_run is not yet implemented in database.py"
    )
    source = inspect.getsource(db_module.get_latest_market_prism_verification_for_run)
    assert "get_ro_connection" in source, (
        "get_latest_market_prism_verification_for_run must use get_ro_connection(), "
        "not get_connection(). Read paths must be enforced at the driver level."
    )
    assert "get_connection()" not in source, (
        "get_latest_market_prism_verification_for_run must not call get_connection() — "
        "use get_ro_connection() for all read-only query paths."
    )


# ---------------------------------------------------------------------------
# B-5: returned row has expected shape keys
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_verification_for_run_returns_expected_shape():
    """B-5: the row must carry id, advisor_role, raw_response (dict), subject_id."""
    _insert_verification_row(run_id=_RUN_ID_A)

    row = db_module.get_latest_market_prism_verification_for_run(_RUN_ID_A)
    assert row is not None, "Precondition: row must exist"

    for key in ("id", "advisor_role", "raw_response", "subject_id"):
        assert key in row, f"Returned row is missing key {key!r}. Row keys: {list(row.keys())!r}"

    assert isinstance(row["id"], int) and row["id"] > 0, f"id must be a positive int; got {row['id']!r}"
    assert row["advisor_role"] == "MARKET_PRISM_VERIFICATION"
    assert isinstance(row["raw_response"], dict), (
        f"raw_response must be a parsed dict (not a JSON string); got {type(row['raw_response'])}"
    )
    assert row["subject_id"] == "global", f"subject_id must be 'global'; got {row.get('subject_id')!r}"


# ---------------------------------------------------------------------------
# B-6: depth-independent exact match
# ---------------------------------------------------------------------------

_RUN_ID_TARGET = "33333333-3333-3333-3333-333333333333"
_RUN_ID_NOISE = "44444444-4444-4444-4444-444444444444"
_NOISE_ROW_COUNT = 20


def test_get_latest_market_prism_verification_for_run_finds_match_regardless_of_table_depth():
    """B-6: the exact json_extract(run_id)=? match must find the target row regardless
    of how many newer rows exist — a bounded row-count scan would miss it."""
    _insert_verification_row(run_id=_RUN_ID_TARGET)

    noise_raw = {
        "run_id": _RUN_ID_NOISE,
        "verified_at": "2026-07-01T03:05:00Z",
        "checks": [],
        "summary": {"n_checks": 0, "n_pass": 0, "n_flagged": 0, "n_overridden": 0, "n_unverifiable": 0},
        "verdict": "no-numeric-claims",
    }
    for _ in range(_NOISE_ROW_COUNT + 1):
        _insert_verification_row(run_id=_RUN_ID_NOISE, raw=dict(noise_raw))

    result = db_module.get_latest_market_prism_verification_for_run(_RUN_ID_TARGET)

    assert result is not None, (
        f"Must find the TARGET row even when {_NOISE_ROW_COUNT + 1} newer NOISE rows "
        "exist — a bounded row-count scan would miss it; the exact SQL json_extract "
        "match must not."
    )
    assert result["raw_response"]["run_id"] == _RUN_ID_TARGET


# ---------------------------------------------------------------------------
# B-7: get_latest_market_prism_summary never returns a VERIFICATION row
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_summary_never_returns_verification_row():
    """B-7: with only a MARKET_PRISM_VERIFICATION row present (no MARKET_PRISM row),
    get_latest_market_prism_summary() must return None — hard-filtered to
    advisor_role='MARKET_PRISM' (mirrors the AC-8 SOURCES exclusion)."""
    _insert_verification_row(run_id=_RUN_ID_A)

    result = db_module.get_latest_market_prism_summary()
    assert result is None, (
        "get_latest_market_prism_summary() must return None when only a "
        "MARKET_PRISM_VERIFICATION row exists — it must be hard-filtered to "
        f"advisor_role='MARKET_PRISM'. Got advisor_role="
        f"{result.get('advisor_role') if result else 'N/A'!r}."
    )


def test_get_latest_market_prism_summary_returns_market_prism_not_verification():
    """B-7b: both a MARKET_PRISM row and (inserted after, higher id) a
    MARKET_PRISM_VERIFICATION row exist — get_latest_market_prism_summary() must
    return the MARKET_PRISM row, not VERIFICATION."""
    _insert_market_prism_row(run_id=_RUN_ID_A)
    _insert_verification_row(run_id=_RUN_ID_A)  # inserted after, higher id

    result = db_module.get_latest_market_prism_summary()
    assert result is not None, "get_latest_market_prism_summary() must return the MARKET_PRISM row"
    assert result["advisor_role"] == "MARKET_PRISM", (
        f"get_latest_market_prism_summary() must return the MARKET_PRISM row, not the "
        f"VERIFICATION row; got advisor_role={result.get('advisor_role')!r}. The "
        "accessor must be filtered to advisor_role='MARKET_PRISM' — not ORDER BY id DESC alone."
    )
