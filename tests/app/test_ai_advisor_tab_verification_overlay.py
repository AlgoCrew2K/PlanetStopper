"""
RED tests for the AC-10 numeric-verification render overlay + AC-9 role-exclusion guard
(DE-PRISM-NUMERIC-VERIFY-001).

AC-10: app.py's ai_advisor_tab() additively fetches the MARKET_PRISM_VERIFICATION row by
run_id (after the existing SOURCES merge, app.py:3862-3904) and attaches per-lens/overall
verification annotations (flag/override badges + the corrected value for overrides) to
the template context. Honest empty-state when the row is absent. The MARKET_PRISM row
object is deep-copied before any attachment (mirrors the SOURCES merge at
app.py:3862-3904) — overridden values render a "council cited X; source says Y"
annotation.

AC-9: "MARKET_PRISM_VERIFICATION" must NOT be added to app.py's _ADVISOR_ROLES list
(keeps it out of the Overview observations loop + _preview_text stamp, same reasoning
as the MARKET_PRISM_SOURCES exclusion).

Design mirrors tests/app/test_ai_advisor_tab_sources_merge.py exactly: full Flask test
client, patch the database accessors, assert on the rendered HTML (not a mocked
render_template context) — proves the route performs the real merge before rendering.
patch(..., create=True) is required because get_latest_market_prism_verification_for_run
does not exist on database yet (pre-GREEN) — patching still succeeds (database module
itself exists) so these tests RED on the BEHAVIORAL assertion (route never calls the
accessor / never renders the annotation), not on an import error.

DB isolation: N/A (route-level, DB accessors are mocked). _disable_auth_for_tests and
_disable_csrf_for_tests (conftest autouse fixtures) handle the auth/CSRF gates.
"""

from __future__ import annotations

import copy
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Shared fixture data (production-shape)
# ---------------------------------------------------------------------------

_RUN_ID_CURRENT = "abababab-abab-abab-abab-abababababab"
_RUN_ID_STALE = "cdcdcdcd-cdcd-cdcd-cdcd-cdcdcdcdcdcd"

_MARKET_PRISM_ROW = {
    "id": 700,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "created_at": "2026-07-02 03:00:00",
    "raw_response": {
        "run_id": _RUN_ID_CURRENT,
        "run_ts": _RUN_ID_CURRENT,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Overlay test.",
        "cited_numbers": [
            {"indicator": "VIX", "value": 22.0, "lens": "derivatives"},
        ],
        "per_lens_digest": {
            "derivatives": {"available": True, "summary": "VIX near 22", "sources": []},
        },
    },
}

_VERIFICATION_ROW_WITH_OVERRIDE = {
    "id": 701,
    "advisor_role": "MARKET_PRISM_VERIFICATION",
    "subject_id": "global",
    "verdict": None,
    "created_at": "2026-07-02 03:05:00",
    "raw_response": {
        "run_id": _RUN_ID_CURRENT,
        "verified_at": "2026-07-02T03:05:00Z",
        "checks": [
            {
                "indicator": "VIX",
                "lens": "derivatives",
                "cited_value": 22.0,
                "ground_truth_value": 18.1,
                "classification": "overridden",
            },
        ],
        "summary": {
            "n_checks": 1,
            "n_pass": 0,
            "n_flagged": 0,
            "n_overridden": 1,
            "n_unverifiable": 0,
        },
        "verdict": "override-found",
    },
}


# ---------------------------------------------------------------------------
# AC-10: verification row fetched by run_id and attached to render
# ---------------------------------------------------------------------------


def test_ai_advisor_tab_fetches_verification_row_by_run_id(client):
    """AC-10: the route must call get_latest_market_prism_verification_for_run with
    the MARKET_PRISM row's run_id — mirrors the M-3 SOURCES pattern.

    RED intent: the current route does not call this accessor at all.
    """
    with (
        patch("database.get_latest_market_prism_summary", return_value=_MARKET_PRISM_ROW),
        patch("database.get_latest_market_prism_sources_for_run", return_value=None),
        patch(
            "database.get_latest_market_prism_verification_for_run",
            return_value=None,
            create=True,
        ) as mock_accessor,
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    mock_accessor.assert_called_once_with(_RUN_ID_CURRENT), (
        f"AC-10: get_latest_market_prism_verification_for_run must be called with the "
        f"MARKET_PRISM row's run_id={_RUN_ID_CURRENT!r}; actual calls: "
        f"{mock_accessor.call_args_list!r}"
    )


def test_ai_advisor_tab_renders_overridden_annotation(client):
    """AC-10: an overridden check must render a 'council cited X; source says Y'
    style annotation — both the cited value and the ground-truth value must appear
    in the rendered HTML (values sourced from the fixture, never hardcoded)."""
    with (
        patch("database.get_latest_market_prism_summary", return_value=_MARKET_PRISM_ROW),
        patch("database.get_latest_market_prism_sources_for_run", return_value=None),
        patch(
            "database.get_latest_market_prism_verification_for_run",
            return_value=_VERIFICATION_ROW_WITH_OVERRIDE,
            create=True,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    cited = _VERIFICATION_ROW_WITH_OVERRIDE["raw_response"]["checks"][0]["cited_value"]
    truth = _VERIFICATION_ROW_WITH_OVERRIDE["raw_response"]["checks"][0]["ground_truth_value"]

    assert str(cited) in html, (
        f"AC-10: the overridden check's cited value ({cited!r}) must appear in the "
        f"rendered Overview HTML annotation. HTML snippet (first 3000 chars): "
        f"{html[:3000]!r}"
    )
    assert str(truth) in html, (
        f"AC-10: the overridden check's ground-truth value ({truth!r}) must appear in "
        f"the rendered HTML annotation (\"council cited X; source says Y\"). "
        f"HTML snippet (first 3000 chars): {html[:3000]!r}"
    )


def test_ai_advisor_tab_honest_empty_state_when_no_verification_row(client):
    """AC-10: accessor returns None -> 200, no crash, no fabricated annotation."""
    with (
        patch("database.get_latest_market_prism_summary", return_value=_MARKET_PRISM_ROW),
        patch("database.get_latest_market_prism_sources_for_run", return_value=None),
        patch(
            "database.get_latest_market_prism_verification_for_run",
            return_value=None,
            create=True,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        "Route must return 200 when the VERIFICATION row is None — honest empty-state, no crash"
    )
    html = resp.get_data(as_text=True)
    assert "18.1" not in html, (
        "When the VERIFICATION accessor returns None, no override annotation content "
        "must appear — honest empty-state, no fabricated verification badge."
    )


def test_ai_advisor_tab_no_stale_verification_bleed_on_run_id_mismatch(client):
    """AC-10 (mirrors AC-9's SOURCES no-stale-bleed guard): when the VERIFICATION
    accessor returns None for the CURRENT run_id (because no VERIFICATION row exists
    for it yet), a STALE run's override annotation must never leak into the render."""
    with (
        patch("database.get_latest_market_prism_summary", return_value=_MARKET_PRISM_ROW),
        patch("database.get_latest_market_prism_sources_for_run", return_value=None),
        patch(
            "database.get_latest_market_prism_verification_for_run",
            return_value=None,  # accessor correctly returns None for run_id=CURRENT
            create=True,
        ) as mock_accessor,
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    mock_accessor.assert_called_once_with(_RUN_ID_CURRENT)
    html = resp.get_data(as_text=True)
    assert "18.1" not in html, (
        "Stale VERIFICATION content must not appear when the accessor returns None "
        "for the current run_id (no-stale-bleed guard, mirrors AC-9)."
    )


def test_ai_advisor_tab_does_not_mutate_market_prism_row_in_place(client):
    """AC-10: the MARKET_PRISM row object must be deep-copied before any verification
    annotation is attached — mirrors the SOURCES merge guard at app.py:3862-3904.

    Proof technique: pass a row object that is a fresh, independent deep copy of the
    canonical fixture as the mock's return_value. After the request, compare it against
    ANOTHER fresh deep copy of the canonical fixture — if the route mutated the
    mock-returned object in place (rather than deep-copying before attaching), the two
    would diverge.
    """
    row_returned_by_mock = copy.deepcopy(_MARKET_PRISM_ROW)

    with (
        patch("database.get_latest_market_prism_summary", return_value=row_returned_by_mock),
        patch("database.get_latest_market_prism_sources_for_run", return_value=None),
        patch(
            "database.get_latest_market_prism_verification_for_run",
            return_value=_VERIFICATION_ROW_WITH_OVERRIDE,
            create=True,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    pristine_copy = copy.deepcopy(_MARKET_PRISM_ROW)
    assert row_returned_by_mock == pristine_copy, (
        "AC-10: the route must not mutate the MARKET_PRISM row object returned by "
        "get_latest_market_prism_summary() in place — it must deep-copy before "
        "attaching verification annotations (mirrors the SOURCES merge guard). The "
        "object returned by the mocked accessor was mutated by the route."
    )


# ---------------------------------------------------------------------------
# AC-9: MARKET_PRISM_VERIFICATION excluded from _ADVISOR_ROLES
# ---------------------------------------------------------------------------


def test_market_prism_verification_not_in_advisor_roles():
    """AC-9: MARKET_PRISM_VERIFICATION must NOT be in app.py's _ADVISOR_ROLES list —
    adding it would pull VERIFICATION rows into the Overview observations loop and
    the _preview_text stamp (same reasoning as the MARKET_PRISM_SOURCES exclusion).

    This is expected to PASS trivially on day 1 (app.py does not reference
    MARKET_PRISM_VERIFICATION at all yet) — it is a regression guard, not a RED gate,
    mirroring the CC-2 import-boundary precedent in test_cycle4_lens_pipeline.py.
    """
    import app as app_module

    assert "MARKET_PRISM_VERIFICATION" not in app_module._ADVISOR_ROLES, (
        "AC-9 violation: MARKET_PRISM_VERIFICATION must never be added to "
        f"app.py's _ADVISOR_ROLES ({app_module._ADVISOR_ROLES!r})."
    )
