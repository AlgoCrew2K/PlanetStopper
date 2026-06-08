"""
Sprint 3 audit-fix Cycle A — RED tests for S3-AUDIT-004 (HIGH) + S3-AUDIT-010
(LOW, folded into 004) on the Flask route side:

  /api/advisor-observations?symphony_id=<id> MUST return ALL rows whose
  symphony_id matches the supplied filter, via a SINGLE database query against
  the new advisor_observations.symphony_id column (migration 025).

Today the route at app.py:2438-2460 issues THREE separate SELECTs
(subject_type='autotune_run', 'spec_bundle', 'cvar_diagnostic') and filters
by subject_id == symphony_id — but producers store subject_id as a numeric
PK or a bundle_hash, never the symphony name.  Every symphony filter today
returns an empty list even when matching observations exist.

These tests assert:
  1. The route uses the new single-query symphony-filter accessor — verified
     by patching the legacy multi-subject accessor and confirming it is NOT
     called when a symphony_id is supplied.
  2. The route returns ALL matching rows (OC + SC + DE) for the symphony.
  3. Rows for other symphonies are excluded.

S3-AUDIT-008 (MEDIUM) is also covered here: _ADVISOR_ROLES retains the
NARRATOR entry with an inline comment marking it deferred — a separate
test below pins the comment via text scan on the source file.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Flask test-client fixture — mirrors tests/ai_advisor/test_advisor_observations_ui.py.
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_client():
    with patch("database.init_db"):
        import app as flask_app

    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


def _make_row(
    obs_id: int,
    *,
    role: str,
    subject_type: str,
    subject_id: str,
    verdict: str,
    symphony_id: str,
) -> dict:
    """Build a row shaped like database.get_advisor_observations_for_symphony output."""
    return {
        "id": obs_id,
        "created_at": "2026-05-27T00:00:00",
        "advisor_role": role,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "verdict": verdict,
        "raw_response": {},
        "is_advisory_only": 1,
        "spec_bundle_id": None,
        "symphony_id": symphony_id,
    }


# ---------------------------------------------------------------------------
# S3-AUDIT-004: route uses single-query symphony filter (migration 025 column).
# ---------------------------------------------------------------------------


def test_advisor_observations_route_uses_single_query_symphony_filter(flask_client):
    """When symphony_id is supplied, the route MUST call the new single-query
    accessor (get_advisor_observations_for_symphony or for_symphony variant)
    and MUST NOT issue the legacy 3x subject-type fan-out.

    The legacy fan-out is the bug at S3-AUDIT-004/010: subject_id==symphony_id
    never matches producer-persisted rows whose subject_id is a numeric PK
    or a bundle_hash.
    """
    # Build a row that should be returned by the new accessor.
    expected_rows = [
        _make_row(1, role="OVERFITTING_CONSCIENCE", subject_type="autotune_run",
                  subject_id="7", verdict="CLEAR", symphony_id="DefensiveAlpha"),
        _make_row(2, role="SPEC_CRITIC", subject_type="spec_bundle",
                  subject_id="bundle-hash-abc", verdict="CLEAR", symphony_id="DefensiveAlpha"),
        _make_row(3, role="DIVERGENCE_EXPLAINER", subject_type="autotune_run",
                  subject_id="7", verdict="NOT_APPLICABLE", symphony_id="DefensiveAlpha"),
    ]

    # Patch both candidate accessor names — the implementer's choice is OK; we
    # just need to verify SOME single-query symphony accessor is called.
    import database as db_module

    called_new_accessor = {"count": 0, "with_arg": None}

    def fake_new_accessor(symphony_id):
        called_new_accessor["count"] += 1
        called_new_accessor["with_arg"] = symphony_id
        return list(expected_rows)

    legacy_called = {"count": 0}

    def fake_legacy(subject_type, subject_id):
        legacy_called["count"] += 1
        return []

    # Patch whichever names exist.  Both names are accepted; at least one
    # should be the active accessor.
    patches = []
    for name in ("get_advisor_observations_for_symphony",
                 "get_advisor_observations_by_symphony"):
        if hasattr(db_module, name):
            patches.append(patch.object(db_module, name, side_effect=fake_new_accessor))

    assert patches, (
        "Neither get_advisor_observations_for_symphony nor "
        "get_advisor_observations_by_symphony exists in database.py — the "
        "single-query accessor must be added for S3-AUDIT-004."
    )

    with patch.object(db_module, "get_advisor_observations_for_subject",
                      side_effect=fake_legacy):
        # Apply all candidate patches.
        for p in patches:
            p.start()
        try:
            resp = flask_client.get("/api/advisor-observations?symphony_id=DefensiveAlpha")
        finally:
            for p in patches:
                p.stop()

    assert resp.status_code == 200, (
        f"Route must return 200; got {resp.status_code}.  Body: {resp.data!r}"
    )
    assert called_new_accessor["count"] >= 1, (
        "The new single-query symphony-filter accessor was not called.  "
        "S3-AUDIT-004 + S3-AUDIT-010: the route must use a one-shot SELECT "
        "via the symphony_id column added by migration 025."
    )
    assert legacy_called["count"] == 0, (
        f"Legacy get_advisor_observations_for_subject was called "
        f"{legacy_called['count']}x during a symphony-filter request.  "
        "S3-AUDIT-010: a single-query path replaces the 3x fan-out."
    )
    assert called_new_accessor["with_arg"] == "DefensiveAlpha", (
        f"Single-query accessor invoked with {called_new_accessor['with_arg']!r}; "
        "expected 'DefensiveAlpha' (the operator-supplied symphony_id)."
    )


def test_advisor_observations_route_returns_all_matching_symphony_rows(flask_client):
    """The route must return ALL rows whose symphony_id matches — not a subset."""
    rows = [
        _make_row(1, role="OVERFITTING_CONSCIENCE", subject_type="autotune_run",
                  subject_id="7", verdict="CLEAR", symphony_id="DefensiveAlpha"),
        _make_row(2, role="SPEC_CRITIC", subject_type="spec_bundle",
                  subject_id="bundle-hash-abc", verdict="CLEAR", symphony_id="DefensiveAlpha"),
        _make_row(3, role="DIVERGENCE_EXPLAINER", subject_type="autotune_run",
                  subject_id="7", verdict="NOT_APPLICABLE", symphony_id="DefensiveAlpha"),
    ]

    import database as db_module
    patches = []
    for name in ("get_advisor_observations_for_symphony",
                 "get_advisor_observations_by_symphony"):
        if hasattr(db_module, name):
            patches.append(patch.object(db_module, name, return_value=list(rows)))

    assert patches, "Single-query symphony accessor missing — see migration 025 tests."

    for p in patches:
        p.start()
    try:
        resp = flask_client.get("/api/advisor-observations?symphony_id=DefensiveAlpha")
    finally:
        for p in patches:
            p.stop()

    assert resp.status_code == 200
    body = resp.get_json()
    assert isinstance(body, list), f"Route body must be a list; got {type(body).__name__}"
    assert len(body) == 3, (
        f"Expected all 3 symphony-matching observations; got {len(body)}.  "
        "The route must surface every row returned by the single-query accessor."
    )
    returned_roles = {r["advisor_role"] for r in body}
    assert returned_roles == {"OVERFITTING_CONSCIENCE", "SPEC_CRITIC", "DIVERGENCE_EXPLAINER"}, (
        f"Returned roles {returned_roles!r} mismatch the seeded set.  All 3 "
        "producer types under the same symphony must surface."
    )


# ---------------------------------------------------------------------------
# S3-AUDIT-008: NARRATOR retained in _ADVISOR_ROLES with a deferred comment.
# ---------------------------------------------------------------------------


_APP_PY_PATH = Path(__file__).parents[2] / "app.py"


def test_advisor_roles_list_retains_narrator_with_deferred_comment():
    """app.py:_ADVISOR_ROLES MUST keep "NARRATOR" and the line MUST carry an
    inline comment marking the role deferred per Sprint 3 scope.

    The audit recommendation (S3-AUDIT-008 option (b)): keep the enum stable
    across DB rows when the producer ships in a later sprint, but document
    the deferred status inline so a future contributor reading the list does
    not assume a producer exists.

    Verified by scanning app.py for a NARRATOR line in the _ADVISOR_ROLES
    block and asserting an inline comment is present on the same line OR
    the immediately following line.
    """
    assert _APP_PY_PATH.is_file(), f"app.py not found at {_APP_PY_PATH}"
    src = _APP_PY_PATH.read_text(encoding="utf-8")

    # Locate _ADVISOR_ROLES block.
    m = re.search(r"_ADVISOR_ROLES\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert m, "Could not locate _ADVISOR_ROLES list literal in app.py"
    block = m.group(1)

    # Locate the NARRATOR line within the block.
    narrator_lines = [ln for ln in block.splitlines() if "NARRATOR" in ln]
    assert narrator_lines, (
        "NARRATOR entry missing from _ADVISOR_ROLES.  Option (b) of S3-AUDIT-008 "
        "keeps the enum stable; do not remove the entry — annotate it."
    )
    # At least one NARRATOR line must carry an inline comment marking it deferred.
    # Accept either same-line `# ... DEFERRED` or directly-adjacent comment line.
    block_lines = block.splitlines()
    deferred_marker_seen = False
    for idx, ln in enumerate(block_lines):
        if "NARRATOR" in ln:
            # Same-line comment
            if "#" in ln and re.search(r"defer", ln, re.IGNORECASE):
                deferred_marker_seen = True
                break
            # Adjacent-line comment (immediately above or below)
            neighbours = []
            if idx > 0:
                neighbours.append(block_lines[idx - 1])
            if idx + 1 < len(block_lines):
                neighbours.append(block_lines[idx + 1])
            for nb in neighbours:
                if "#" in nb and re.search(r"defer", nb, re.IGNORECASE):
                    deferred_marker_seen = True
                    break
            if deferred_marker_seen:
                break

    assert deferred_marker_seen, (
        "NARRATOR entry in _ADVISOR_ROLES is not annotated as deferred.  "
        "S3-AUDIT-008 option (b): add an inline comment near the NARRATOR line "
        "matching /defer/i so future contributors know no producer exists yet "
        "(per project memory project_sprint_3_scope_and_team)."
    )


def test_advisor_roles_list_retains_active_producer_roles():
    """The active producers must still be enumerated.

    Regression guard against accidentally removing OC/SC while annotating
    NARRATOR.

    DIVERGENCE_EXPLAINER was retired in Cycle C (2026-06-08, PM decision):
    its CVaR-divergence core is permanently rejected; with the feature flag
    off it contributes only NOT_APPLICABLE rows that read as a dead producer.
    It must NOT appear in _ADVISOR_ROLES — the new pin is in
    TestDivergenceExplainerRetiredFromSurface (tests/advisors/test_advisor_liveness_routes.py).
    """
    assert _APP_PY_PATH.is_file()
    src = _APP_PY_PATH.read_text(encoding="utf-8")
    m = re.search(r"_ADVISOR_ROLES\s*=\s*\[(.*?)\]", src, re.DOTALL)
    assert m, "Could not locate _ADVISOR_ROLES"
    block = m.group(1)
    for role in ("OVERFITTING_CONSCIENCE", "SPEC_CRITIC"):
        assert role in block, (
            f"Active producer role {role!r} missing from _ADVISOR_ROLES.  "
            "S3-AUDIT-008 is a comment-only change; do not drop active roles."
        )
    assert "DIVERGENCE_EXPLAINER" not in block, (
        "DIVERGENCE_EXPLAINER must NOT be in _ADVISOR_ROLES after Cycle C DE-retire "
        "(2026-06-08 PM decision). See TestDivergenceExplainerRetiredFromSurface."
    )
