"""LOGIC-L-2 — RED: ``/api/advisor-observations`` route docstring must match code.

vision-audit LOW finding (doc-only).  The route docstring at ``app.py:2430-2433``
describes the *pre-Sprint-3* implementation:

    "?symphony_id=<id>  — filter to rows whose subject_id matches; calls
                         database.get_advisor_observations_for_subject."

But Sprint 3 audit-fix Cycle A (S3-AUDIT-004 + S3-AUDIT-010, migration 025)
replaced that path:

  * The actual helper called at ``app.py:2444`` is
    ``database.get_advisor_observations_for_symphony`` (NOT
    ``get_advisor_observations_for_subject``).
  * The filter column is the denormalized ``symphony_id`` (NOT ``subject_id``).
    The legacy ``subject_id``-comparison never matched, because producers store
    subject_id as a numeric PK (OC/DE) or bundle_hash (SC), not the symphony
    name — that is exactly the bug the migration closed.

These tests pin the *contract between the route docstring and its body* — they
fail RED until ``app.py:2428-2437`` is corrected to describe the
single-query / denormalized-``symphony_id`` pattern.  No production behaviour
changes; the route body is already correct post-Sprint-3 audit-fix Cycle A.

Scope:
  - Source-text contract pins (read ``app.py`` as text, assert substrings).
  - Function-object docstring pin via importing the Flask view function.
  - Anchor / regression guard: the route must continue to call
    ``get_advisor_observations_for_symphony`` (i.e., docstring drift must NOT
    be "fixed" by reverting the implementation).

Tier 1 — pure-text + import-only.  No DB, no network, no Flask test-client.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import app as app_mod
from app import api_advisor_observations

# ---------------------------------------------------------------------------
# Source-text helpers
# ---------------------------------------------------------------------------


def _app_source_text() -> str:
    """Return the raw text of app.py.

    Read from disk (not __doc__) so we catch drift in *every* comment / docstring
    in the route region, not just the view function's docstring.
    """
    path = Path(inspect.getsourcefile(app_mod))
    return path.read_text(encoding="utf-8")


def _route_docstring() -> str:
    """Return ``api_advisor_observations.__doc__`` — the route function docstring."""
    return api_advisor_observations.__doc__ or ""


def _route_source_region() -> str:
    """Return the source of the route function (def line through ``return jsonify``).

    Uses ``inspect.getsource`` so the region tracks the function even if its
    line numbers shift.  This is the docstring + body the tests pin against.
    """
    return inspect.getsource(api_advisor_observations)


# ---------------------------------------------------------------------------
# Test 1 — Anchor: the route body actually calls the *symphony* helper.
# ---------------------------------------------------------------------------


def test_route_body_calls_for_symphony_helper():
    """Anchor: confirm production route uses ``get_advisor_observations_for_symphony``.

    If this ever flips back to ``get_advisor_observations_for_subject``, the
    docstring-drift fix went the wrong way (reverted the Sprint-3 audit-fix
    Cycle A implementation).  Pin first; everything below relies on this.
    """
    region = _route_source_region()
    assert "database.get_advisor_observations_for_symphony(" in region, (
        "Regression: /api/advisor-observations must call "
        "database.get_advisor_observations_for_symphony (single-query via the "
        "denormalized symphony_id column, migration 025). "
        f"Current route source:\n{region}"
    )
    assert "database.get_advisor_observations_for_subject(" not in region, (
        "Regression: /api/advisor-observations must NOT call "
        "database.get_advisor_observations_for_subject — that helper filters on "
        "subject_id which never matches symphony names (Sprint-3 audit-fix "
        "Cycle A closed this). "
        f"Current route source:\n{region}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Route docstring must NOT name the wrong helper.
# ---------------------------------------------------------------------------


def test_route_docstring_does_not_name_subject_helper():
    """Pin: docstring must not say ``get_advisor_observations_for_subject``.

    Pre-fix docstring (app.py:2430-2431) reads:
        "?symphony_id=<id>  — filter to rows whose subject_id matches; calls
                             database.get_advisor_observations_for_subject."
    But the body calls ``get_advisor_observations_for_symphony``.  After fix,
    the docstring must name the *actual* helper called by the body.
    """
    doc = _route_docstring()
    assert "get_advisor_observations_for_subject" not in doc, (
        "Route docstring names the wrong helper "
        "(``get_advisor_observations_for_subject``).  The body calls "
        "``get_advisor_observations_for_symphony`` — Sprint-3 audit-fix "
        "Cycle A.  Docstring must be updated to match. "
        f"Current docstring:\n{doc}"
    )


# ---------------------------------------------------------------------------
# Test 3 — Route docstring must NAME the correct helper.
# ---------------------------------------------------------------------------


def test_route_docstring_names_for_symphony_helper():
    """Pin: docstring must mention ``get_advisor_observations_for_symphony``.

    Positive contract — not just "absence of the wrong name", but "presence of
    the right name".  Prevents a fix that simply deletes the helper reference
    without re-anchoring the docstring to the actual code path.
    """
    doc = _route_docstring()
    assert "get_advisor_observations_for_symphony" in doc, (
        "Route docstring must name the actual helper "
        "``get_advisor_observations_for_symphony`` so the docstring describes "
        "the real code path (Sprint-3 audit-fix Cycle A — migration 025). "
        f"Current docstring:\n{doc}"
    )


# ---------------------------------------------------------------------------
# Test 4 — Route docstring must NOT describe a ``subject_id`` filter.
# ---------------------------------------------------------------------------


def test_route_docstring_does_not_describe_subject_id_filter():
    """Pin: docstring must not say the symphony_id query filters by ``subject_id``.

    The legacy framing "filter to rows whose subject_id matches" is the exact
    drift the audit cites — producers persist ``subject_id`` as a numeric PK
    (OC / DE) or a bundle_hash (SC), never the symphony name, so a
    subject_id-comparison filter never matched.  The real filter is on the
    denormalized ``symphony_id`` column added by migration 025.
    """
    doc = _route_docstring()
    flat = re.sub(r"\s+", " ", doc)

    forbidden_phrases = [
        "subject_id matches",
        "filter to rows whose subject_id",
        "WHERE subject_id =",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in flat, (
            f"Route docstring still contains drift phrase {phrase!r}. "
            "The route filters on the denormalized ``symphony_id`` column "
            "(migration 025), not on ``subject_id``. "
            f"Current docstring (flattened):\n{flat}"
        )


# ---------------------------------------------------------------------------
# Test 5 — Route docstring must describe the denormalized symphony_id column.
# ---------------------------------------------------------------------------


def test_route_docstring_describes_symphony_id_column():
    """Pin: docstring must reference the denormalized ``symphony_id`` column.

    Positive contract describing the actual filter mechanism so future readers
    understand the route is a single-query lookup against the migration-025
    column, not a subject-type fan-out.
    """
    doc = _route_docstring().lower()
    assert "symphony_id" in doc, (
        "Route docstring must reference the ``symphony_id`` column / parameter "
        "so the documented filter mechanism matches the body's single-query "
        f"SELECT.  Current docstring:\n{_route_docstring()}"
    )


# ---------------------------------------------------------------------------
# Test 6 — Route source region must not describe a 3-subject fan-out.
# ---------------------------------------------------------------------------


def test_route_source_no_three_subject_fanout_claim():
    """Whole-route scan: no docstring/comment claims a 3-subject fan-out.

    The pre-fix implementation iterated over ``(autotune_run, spec_bundle,
    cvar_diagnostic)`` subject_types and unioned three SELECTs in Python.  The
    Sprint-3 audit-fix Cycle A collapsed that to a single SELECT on the
    denormalized symphony_id column.  Any surviving comment/docstring that
    still describes the old fan-out is drift.
    """
    region = _route_source_region()
    flat = re.sub(r"\s+", " ", region)

    forbidden = [
        "3-subject fan-out",
        "three-subject fan-out",
        "fan-out across autotune_run",
        "iterates over autotune_run",
        "autotune_run + spec_bundle + cvar_diagnostic",
        "autotune_run, spec_bundle, cvar_diagnostic",
    ]
    for phrase in forbidden:
        assert phrase.lower() not in flat.lower(), (
            f"Route source still contains drift phrase {phrase!r}. "
            "Sprint-3 audit-fix Cycle A replaced the 3-subject fan-out with a "
            "single-query SELECT on the denormalized symphony_id column. "
            f"Offending region:\n{region}"
        )


# ---------------------------------------------------------------------------
# Test 7 — Route docstring must not claim the symphony_id branch returns
# observations across multiple advisor roles via fan-out.
# ---------------------------------------------------------------------------


def test_route_docstring_symphony_branch_is_single_query():
    """Pin: the docstring's ``?symphony_id=`` branch must read as a single query.

    Negative contract — the symphony_id branch must NOT be documented as
    "iterates over advisor roles" or "calls X three times".  The body executes
    one SELECT against advisor_observations filtered by symphony_id.
    """
    doc = _route_docstring()
    flat = re.sub(r"\s+", " ", doc).lower()

    # If the docstring describes the symphony_id branch, it must not also
    # describe it as a fan-out / loop over roles.
    if "symphony_id" in flat:
        forbidden_in_symphony_branch = [
            "iterates over",
            "fan-out",
            "fan out",
            "three queries",
            "3 queries",
            "loops over",
        ]
        for phrase in forbidden_in_symphony_branch:
            assert phrase not in flat, (
                f"Route docstring claims the symphony_id branch involves "
                f"{phrase!r}, but the body executes a single SELECT against "
                "the denormalized symphony_id column. "
                f"Current docstring (flattened):\n{flat}"
            )
