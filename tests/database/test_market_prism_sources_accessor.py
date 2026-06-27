"""
RED tests for database accessors — DE-PRISM-SOURCES-001 v2.

Guards the v2 design: MARKET_PRISM_SOURCES is an APPEND-ONLY distinct-role row
(not a mutation of the MARKET_PRISM row).

Coverage:
  A-1  update_advisor_observation_raw_response is ABSENT from database module
       (the v1 mutation accessor that failed test_017; its removal is the CI fix).
  A-2  get_latest_market_prism_sources_for_run(run_id) returns the matching
       MARKET_PRISM_SOURCES row when one exists with that run_id.
  A-3  get_latest_market_prism_sources_for_run returns None when no SOURCES row
       exists for the given run_id — even if a SOURCES row from a DIFFERENT run exists.
       (No stale citation bleed — AC-9.)
  A-4  get_latest_market_prism_sources_for_run returns None when no SOURCES rows
       exist at all.
  A-7  get_latest_market_prism_summary() NEVER returns a MARKET_PRISM_SOURCES row —
       it is hard-filtered to advisor_role='MARKET_PRISM'.
  A-8  get_latest_market_prism_sources_for_run uses get_ro_connection (read-only path).
  A-10 The returned row has all expected shape keys (id, advisor_role, raw_response as dict).
  A-11 get_latest_market_prism_sources_for_run uses exact SQL match (json_extract), not
       prefix/substring match — a run_id that is a prefix of another must NOT match.

Note: get_latest_market_prism_sources() (no run_id arg) was deleted — zero production
callers; tests A-5, A-6, A-9 removed alongside it.

Fixture provenance (D-2): rows inserted via insert_advisor_observation (public write
accessor) with production-shape raw_response. The MARKET_PRISM raw_response shape follows
the synthesizer role file contract: {"run_id": <uuid4-str>, "run_ts": <uuid4-str>, ...}.
run_id == run_ts (both set to the same UUID) per prism-synthesizer.md:130-132.

DB isolation: the global conftest._isolate_db autouse fixture redirects DB_PATH to a
per-test tempfile and calls init_db(). Every test starts with a clean, fully-migrated DB.
"""

from __future__ import annotations

import inspect

import pytest

import database as db_module

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_RUN_ID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_RUN_ID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"

# Production-shape MARKET_PRISM raw_response (prism-synthesizer.md:130-132).
# run_id == run_ts, both set to the same UUID4 string.
_MARKET_PRISM_RAW_A = {
    "run_id": _RUN_ID_A,
    "run_ts": _RUN_ID_A,
    "overall_sentiment": "neutral",
    "sentiment_rationale": "Mixed signals.",
    "per_lens_digest": {
        "technicals": {"available": True, "summary": "SMA above", "sources": []},
        "sentiment": {"available": True, "summary": "Neutral", "sources": []},
    },
}

# Production-shape MARKET_PRISM_SOURCES raw_response (v2 design).
# Only per_lens_digest + run_id — no other MARKET_PRISM fields.
_SOURCES_RAW_A = {
    "run_id": _RUN_ID_A,
    "per_lens_digest": {
        "sentiment": {
            "article_corpus": [
                {
                    "title": "Reuters Markets",
                    "url": "https://www.reuters.com/markets/test-a",
                    "published": "2026-06-24",
                },
            ]
        },
        "macro": {
            "article_corpus": [
                {
                    "title": "FRED T10Y2Y",
                    "url": "https://fred.stlouisfed.org/series/T10Y2Y",
                    "published": "2026-06-24",
                },
            ]
        },
    },
}

_SOURCES_RAW_B = {
    "run_id": _RUN_ID_B,
    "per_lens_digest": {
        "sentiment": {
            "article_corpus": [
                {
                    "title": "AP News Markets",
                    "url": "https://apnews.com/markets/test-b",
                    "published": "2026-06-24",
                },
            ]
        },
    },
}


def _insert_market_prism_row(run_id: str = _RUN_ID_A) -> int:
    """Insert a MARKET_PRISM row (production shape) and return its row id."""
    raw = dict(_MARKET_PRISM_RAW_A)
    raw["run_id"] = run_id
    raw["run_ts"] = run_id
    return db_module.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="",
        verdict="neutral",
        raw_response=raw,
        symphony_id="",
    )


def _insert_sources_row(run_id: str = _RUN_ID_A, sources_raw: dict | None = None) -> int:
    """Insert a MARKET_PRISM_SOURCES row and return its row id."""
    raw = sources_raw if sources_raw is not None else dict(_SOURCES_RAW_A)
    raw = dict(raw)
    raw["run_id"] = run_id
    return db_module.insert_advisor_observation(
        advisor_role="MARKET_PRISM_SOURCES",
        subject_type="portfolio",
        subject_id="global",
        verdict=None,
        raw_response=raw,
        symphony_id="",
    )


# ---------------------------------------------------------------------------
# A-1: update_advisor_observation_raw_response is ABSENT
# ---------------------------------------------------------------------------


def test_update_advisor_observation_raw_response_is_absent():
    """A-1: database.py must NOT export update_advisor_observation_raw_response.

    This is the v1 mutation accessor that caused test_017 CI failure (PR #82).
    Its removal is the primary fix: the observation log is append-only.
    Any symbol with this exact name must be gone.
    """
    assert not hasattr(db_module, "update_advisor_observation_raw_response"), (
        "database.py must not export update_advisor_observation_raw_response — "
        "it was the v1 mutation accessor that violated the append-only contract "
        "(test_017::test_no_update_advisor_observation_symbol_in_database_module). "
        "Delete it from database.py."
    )


def test_no_mutation_accessor_for_advisor_observations():
    """A-1 (broad): No update_advisor_* function may be exported by database.py.

    Mirrors the test_017 mutation-prefix scan (update_/edit_/modify_/delete_/remove_/set_advisor).
    This test ensures the fix is comprehensive — not just removing one specific name.
    """
    mutation_prefixes = (
        "update_advisor",
        "edit_advisor",
        "modify_advisor",
        "delete_advisor",
        "remove_advisor",
        "set_advisor",
    )
    exposed = [
        name
        for name, obj in inspect.getmembers(db_module, inspect.isfunction)
        if any(name.startswith(p) for p in mutation_prefixes)
    ]
    assert not exposed, (
        f"database.py must not export mutation accessors for advisor_observations; "
        f"found: {exposed}. "
        "The observation log is append-only — existing rows must be immutable."
    )


# ---------------------------------------------------------------------------
# A-2: get_latest_market_prism_sources_for_run returns the matching row
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_sources_for_run_returns_matching_row():
    """A-2: get_latest_market_prism_sources_for_run(run_id) must return the
    MARKET_PRISM_SOURCES row whose raw_response["run_id"] matches.

    GIVEN a MARKET_PRISM_SOURCES row with run_id=_RUN_ID_A,
    WHEN get_latest_market_prism_sources_for_run(_RUN_ID_A) is called,
    THEN it returns a dict with advisor_role="MARKET_PRISM_SOURCES" and
    raw_response["run_id"] == _RUN_ID_A.
    """
    _insert_sources_row(run_id=_RUN_ID_A)

    row = db_module.get_latest_market_prism_sources_for_run(_RUN_ID_A)

    assert row is not None, (
        "get_latest_market_prism_sources_for_run must return the matching SOURCES row, "
        "got None. The accessor is not yet implemented or does not match on run_id."
    )
    assert row["advisor_role"] == "MARKET_PRISM_SOURCES", (
        f"Returned row must have advisor_role='MARKET_PRISM_SOURCES'; "
        f"got {row.get('advisor_role')!r}"
    )
    raw = row.get("raw_response") or {}
    assert isinstance(raw, dict), (
        f"raw_response must be a dict (deserialised from JSON); got {type(raw)}"
    )
    assert raw.get("run_id") == _RUN_ID_A, (
        f"raw_response['run_id'] must match {_RUN_ID_A!r}; got {raw.get('run_id')!r}"
    )


# ---------------------------------------------------------------------------
# A-3: get_latest_market_prism_sources_for_run returns None on run_id mismatch
# (no stale citation bleed — AC-9 critical guard)
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_sources_for_run_returns_none_on_mismatch():
    """A-3 / AC-9 CRITICAL: get_latest_market_prism_sources_for_run must return None
    when the latest SOURCES row is from a DIFFERENT run_id.

    This is the stale-citation-bleed guard. A night where all lenses are unavailable
    produces no SOURCES row for that run. If the accessor fell back to the latest SOURCES
    row regardless of run_id, it would inject YESTERDAY's citations into TONIGHT's
    MARKET_PRISM read — stale citations from a different council run.

    GIVEN two SOURCES rows: run_id=A (older) and run_id=B (newer),
    WHEN get_latest_market_prism_sources_for_run(_RUN_ID_A) is called,
    THEN it must return the row for A (not B), i.e., match by run_id — not just latest.

    AND GIVEN no SOURCES row exists for _RUN_ID_A (only B),
    WHEN get_latest_market_prism_sources_for_run(_RUN_ID_A) is called,
    THEN it must return None — NEVER the row for _RUN_ID_B.
    """
    # Insert B (no row for A).
    _insert_sources_row(run_id=_RUN_ID_B, sources_raw=dict(_SOURCES_RAW_B))

    # Request A: must return None, not the B row.
    result = db_module.get_latest_market_prism_sources_for_run(_RUN_ID_A)
    assert result is None, (
        "get_latest_market_prism_sources_for_run must return None when the only "
        f"existing SOURCES row is for a DIFFERENT run_id ({_RUN_ID_B!r}); "
        f"got {result!r}. "
        "This is the stale-citation-bleed guard (AC-9): falling back to a different "
        "run's citations would show last night's sources against tonight's read."
    )


def test_get_latest_market_prism_sources_for_run_matches_correct_row_when_both_exist():
    """A-3b: When SOURCES rows for both run_id=A and run_id=B exist,
    the accessor must return the row for A when called with _RUN_ID_A
    and the row for B when called with _RUN_ID_B — never a cross-run match.
    """
    id_a = _insert_sources_row(run_id=_RUN_ID_A, sources_raw=dict(_SOURCES_RAW_A))
    id_b = _insert_sources_row(run_id=_RUN_ID_B, sources_raw=dict(_SOURCES_RAW_B))

    row_a = db_module.get_latest_market_prism_sources_for_run(_RUN_ID_A)
    row_b = db_module.get_latest_market_prism_sources_for_run(_RUN_ID_B)

    assert row_a is not None, f"Expected a SOURCES row for run_id={_RUN_ID_A!r}; got None"
    assert row_b is not None, f"Expected a SOURCES row for run_id={_RUN_ID_B!r}; got None"

    raw_a = row_a.get("raw_response") or {}
    raw_b = row_b.get("raw_response") or {}

    assert raw_a.get("run_id") == _RUN_ID_A, (
        f"Row returned for {_RUN_ID_A!r} has raw_response.run_id={raw_a.get('run_id')!r} "
        "(cross-run mismatch)"
    )
    assert raw_b.get("run_id") == _RUN_ID_B, (
        f"Row returned for {_RUN_ID_B!r} has raw_response.run_id={raw_b.get('run_id')!r} "
        "(cross-run mismatch)"
    )


# ---------------------------------------------------------------------------
# A-4: get_latest_market_prism_sources_for_run returns None when no SOURCES rows
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_sources_for_run_returns_none_when_empty():
    """A-4: When no MARKET_PRISM_SOURCES rows exist at all,
    get_latest_market_prism_sources_for_run must return None — not raise.
    """
    result = db_module.get_latest_market_prism_sources_for_run(_RUN_ID_A)
    assert result is None, f"Expected None when no SOURCES rows exist; got {result!r}"


# ---------------------------------------------------------------------------
# A-7: get_latest_market_prism_summary NEVER returns a MARKET_PRISM_SOURCES row
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_summary_never_returns_sources_row():
    """A-7 / AC-8: get_latest_market_prism_summary() must NEVER return a
    MARKET_PRISM_SOURCES row — it is hard-filtered to advisor_role='MARKET_PRISM'.

    GIVEN only a MARKET_PRISM_SOURCES row in the DB (no MARKET_PRISM row),
    WHEN get_latest_market_prism_summary() is called,
    THEN it must return None — the SOURCES row must be invisible to it.
    """
    _insert_sources_row(run_id=_RUN_ID_A)

    result = db_module.get_latest_market_prism_summary()
    assert result is None, (
        "get_latest_market_prism_summary() must return None when only a "
        "MARKET_PRISM_SOURCES row exists — it must be hard-filtered to "
        "advisor_role='MARKET_PRISM'. Got a row with "
        f"advisor_role={result.get('advisor_role') if result else 'N/A'!r}."
    )


def test_get_latest_market_prism_summary_returns_market_prism_not_sources():
    """A-7b: When both a MARKET_PRISM row and a MARKET_PRISM_SOURCES row exist
    (with SOURCES inserted AFTER MARKET_PRISM so it has a higher id),
    get_latest_market_prism_summary() must return the MARKET_PRISM row — not SOURCES.
    """
    _insert_market_prism_row(run_id=_RUN_ID_A)
    _insert_sources_row(run_id=_RUN_ID_A)  # inserted after, higher id

    result = db_module.get_latest_market_prism_summary()
    assert result is not None, (
        "get_latest_market_prism_summary() must return the MARKET_PRISM row; got None."
    )
    assert result["advisor_role"] == "MARKET_PRISM", (
        f"get_latest_market_prism_summary() must return the MARKET_PRISM row, "
        f"not the SOURCES row; got advisor_role={result.get('advisor_role')!r}. "
        "The accessor must be filtered to advisor_role='MARKET_PRISM' — not ORDER BY id DESC alone."
    )


# ---------------------------------------------------------------------------
# A-8/A-9: RO connection enforcement (source-code check)
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_sources_for_run_uses_ro_connection():
    """A-8: get_latest_market_prism_sources_for_run must use get_ro_connection.

    Read-only query paths use get_ro_connection() (mode=ro at SQLite driver level)
    per architecture constraint 5 (dashboard read-only) and Advisor scope-boundary I-3.
    """
    assert hasattr(db_module, "get_latest_market_prism_sources_for_run"), (
        "get_latest_market_prism_sources_for_run is not yet implemented in database.py"
    )
    source = inspect.getsource(db_module.get_latest_market_prism_sources_for_run)
    assert "get_ro_connection" in source, (
        "get_latest_market_prism_sources_for_run must use get_ro_connection(), "
        "not get_connection(). Read paths must be enforced at the driver level."
    )
    assert "get_connection()" not in source, (
        "get_latest_market_prism_sources_for_run must not call get_connection() — "
        "use get_ro_connection() for all read-only query paths."
    )


# ---------------------------------------------------------------------------
# A-10: returned row has expected shape keys
# ---------------------------------------------------------------------------


def test_get_latest_market_prism_sources_for_run_returns_expected_shape():
    """A-10: The row returned by get_latest_market_prism_sources_for_run must carry
    the expected schema keys (id, advisor_role, raw_response as parsed dict, subject_id).
    """
    _insert_sources_row(run_id=_RUN_ID_A)

    row = db_module.get_latest_market_prism_sources_for_run(_RUN_ID_A)
    assert row is not None, "Precondition: row must exist"

    for key in ("id", "advisor_role", "raw_response", "subject_id"):
        assert key in row, (
            f"Returned SOURCES row is missing key {key!r}. Row keys: {list(row.keys())!r}"
        )

    assert isinstance(row["id"], int) and row["id"] > 0, (
        f"id must be a positive int; got {row['id']!r}"
    )
    assert row["advisor_role"] == "MARKET_PRISM_SOURCES"
    assert isinstance(row["raw_response"], dict), (
        f"raw_response must be a parsed dict (not a JSON string); got {type(row['raw_response'])}"
    )
    assert row["subject_id"] == "global", (
        f"subject_id must be 'global'; got {row.get('subject_id')!r}"
    )


# ---------------------------------------------------------------------------
# A-11: depth-independent match — json_extract finds any row regardless of table depth
# ---------------------------------------------------------------------------

# The exact SQL json_extract(raw_response, '$.run_id') = ? match finds the target row
# regardless of how many newer rows exist in the table. A prior bounded row-count
# scan would have missed a target row buried under more noise rows than its scan
# window — the noise rows below prove the current implementation is not subject to
# that failure mode.
_RUN_ID_TARGET = "11111111-1111-1111-1111-111111111111"
_RUN_ID_NOISE = "22222222-2222-2222-2222-222222222222"
_NOISE_ROW_COUNT = 20  # noise rows inserted after the target to prove depth-independence


def test_get_latest_market_prism_sources_for_run_finds_match_regardless_of_table_depth():
    """A-11: get_latest_market_prism_sources_for_run finds the target row regardless
    of how many newer rows exist in the table (depth-independence).

    The current implementation uses exact SQL json_extract(raw_response, '$.run_id') = ?
    — it scans ALL rows and returns the match wherever it sits in the table. A bounded
    row-count scan (e.g. a prior Python LIMIT-N scan) would have missed a target row
    buried under more noise rows than its scan window.

    GIVEN: 1 SOURCES row for _RUN_ID_TARGET (inserted first, lowest id), then
           (_NOISE_ROW_COUNT + 1) SOURCES rows for _RUN_ID_NOISE (higher ids),
    WHEN get_latest_market_prism_sources_for_run(_RUN_ID_TARGET) is called,
    THEN it must return the TARGET row — not None.

    A bounded row-count scan would return only the most recent rows (all NOISE) and
    miss TARGET. The exact SQL match must find TARGET regardless of table depth.
    """
    # Insert the target row first (lowest id).
    _insert_sources_row(run_id=_RUN_ID_TARGET)

    # Insert noise rows (higher ids, all with a different run_id).
    noise_raw = {"run_id": _RUN_ID_NOISE, "per_lens_digest": {}}
    for _ in range(_NOISE_ROW_COUNT + 1):
        _insert_sources_row(run_id=_RUN_ID_NOISE, sources_raw=dict(noise_raw))

    result = db_module.get_latest_market_prism_sources_for_run(_RUN_ID_TARGET)

    assert result is not None, (
        f"get_latest_market_prism_sources_for_run must find the TARGET row even when "
        f"{_NOISE_ROW_COUNT + 1} newer NOISE rows exist. "
        f"A bounded row-count scan would miss it; the exact SQL json_extract match must not. "
        f"Got None — the implementation did not find the target row."
    )
    raw = result.get("raw_response") or {}
    assert raw.get("run_id") == _RUN_ID_TARGET, (
        f"Returned row run_id must be {_RUN_ID_TARGET!r}; got {raw.get('run_id')!r}"
    )
