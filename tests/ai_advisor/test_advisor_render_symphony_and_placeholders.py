"""advisor-fix cycle — RED: AC-3 + AC-6 /ai-advisor render is symphony-blind and
shows placeholder rows as if they were recommendations.

Today (templates/ai_advisor.html:528-566) the observations table renders a Subject
column showing subject_type ('autotune_run') and the row's verdict, but NOT which
SYMPHONY the row belongs to — so every row reads the same.  It also renders
DIVERGENCE_EXPLAINER / NOT_APPLICABLE feature-off stub rows (verdict NOT_APPLICABLE,
raw_response {"feature_flag":"off"}) in the same table as genuine recommendations,
which is fake/misleading (those are the ~33 identical rows in the live DB).

Contract pinned here (coordinated with flask-dashboard-specialist):
  AC-3a: every rendered observation row carries the symphony name, exposed via a
         dedicated cell with data-testid="obs-symphony" (so the operator can tell
         which symphony each row is about).
  AC-3b / AC-6: pure feature-off / NOT_APPLICABLE placeholder rows MUST NOT appear
         as recommendations.  Either they are omitted from the recommendations
         table, or they are clearly labelled as not-a-recommendation via
         data-testid="obs-placeholder" on the row.  A NOT_APPLICABLE row rendered
         indistinguishably from a real verdict row is a FAIL.

Mocking strategy:
  * database.get_advisor_observations_for_role patched to return crafted rows
    (one genuine WATCH for symphony A, one NOT_APPLICABLE feature-off stub).
  * No live DB, no network; math engine never mocked; fixtures function-scoped.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest


@pytest.fixture(scope="function")
def flask_client():
    with patch("database.init_db"):
        import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


def _obs_row(
    obs_id: int,
    *,
    role: str,
    verdict: str,
    symphony_id: str,
    raw_response: dict,
    subject_type: str = "autotune_run",
    subject_id: str = "2156",
) -> dict:
    return {
        "id": obs_id,
        "created_at": "2026-06-04T00:00:00",
        "advisor_role": role,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "verdict": verdict,
        "raw_response": raw_response,
        "is_advisory_only": 1,
        "spec_bundle_id": None,
        "symphony_id": symphony_id,
    }


# Two distinct symphony names (no producer-computed values — identity only).
_SYM_A = "(invest) lqd + eyeg 5 ways full market"
_SYM_B = "corporate chaos 5 ways"


@pytest.fixture(scope="function")
def patched_role_accessor():
    """Patch the per-role accessor so /ai-advisor renders our crafted rows.

    Returns one genuine per-symphony OC row (verdict WATCH, symphony A) and one
    DIVERGENCE_EXPLAINER feature-off placeholder (NOT_APPLICABLE, symphony B).
    """
    genuine = _obs_row(
        101, role="OVERFITTING_CONSCIENCE", verdict="WATCH",
        symphony_id=_SYM_A, raw_response={"s_count": 3, "note": "selection drift"},
    )
    placeholder = _obs_row(
        102, role="DIVERGENCE_EXPLAINER", verdict="NOT_APPLICABLE",
        symphony_id=_SYM_B, raw_response={"feature_flag": "off"},
    )

    def _by_role(role, limit=50):
        if role == "OVERFITTING_CONSCIENCE":
            return [genuine]
        if role == "DIVERGENCE_EXPLAINER":
            return [placeholder]
        return []

    with patch("database.get_advisor_observations_for_role", side_effect=_by_role):
        yield {"genuine": genuine, "placeholder": placeholder}


# ===========================================================================
# AC-3a — the render shows WHICH symphony each row belongs to.
# ===========================================================================


def test_render_shows_symphony_name_per_observation_row(flask_client, patched_role_accessor):
    """Every rendered observation row must surface its symphony name in a dedicated
    cell, hooked by data-testid="obs-symphony" OR class="obs-symphony".

    Today the template renders subject_type/subject_id but no symphony — so every
    row reads identically.  AC-3: the operator must see which symphony each row
    is about.

    The load-bearing assertion is that the genuine row's symphony NAME is actually
    rendered AND that a stable per-row symphony cell hook exists — a cell hook with
    an empty value would not satisfy both.
    """
    resp = flask_client.get("/ai-advisor")
    assert resp.status_code == 200
    html_text = resp.get_data(as_text=True)

    # Accept either the data-testid (preferred, advisor-markup convention) or the
    # CSS class — the selector convention is the implementer's choice, the HOOK
    # existing is the contract.
    has_symphony_cell = (
        'data-testid="obs-symphony"' in html_text
        or 'class="obs-symphony"' in html_text
        or "obs-symphony" in html_text
    )
    assert has_symphony_cell, (
        "No per-row symphony cell hook (data-testid=\"obs-symphony\" or "
        "class=\"obs-symphony\") in the rendered observations table. "
        "AC-3: each row must show which symphony it belongs to."
    )
    # The genuine OC row's symphony name must appear in the rendered HTML — proves
    # the cell carries the real value, not an empty placeholder.
    assert _SYM_A in html_text or _SYM_A.replace("&", "&amp;") in html_text, (
        f"Symphony name {_SYM_A!r} not rendered. The symphony cell must display "
        "the row's actual symphony, not an empty/dash placeholder."
    )


# ===========================================================================
# AC-3b / AC-6 — placeholder / feature-off rows are not shown as recommendations.
# ===========================================================================


def test_feature_off_placeholder_not_rendered_as_recommendation(flask_client, patched_role_accessor):
    """A DIVERGENCE_EXPLAINER NOT_APPLICABLE feature-off stub must NOT appear as a
    genuine recommendation row.

    Acceptable implementations:
      (a) the placeholder row is OMITTED from the recommendations table, OR
      (b) the row is rendered but flagged with data-testid="obs-placeholder" so it
          is visually/structurally distinct from a real verdict row.

    A NOT_APPLICABLE row rendered with the same markup as a real verdict row FAILS.
    """
    resp = flask_client.get("/ai-advisor")
    assert resp.status_code == 200
    html_text = resp.get_data(as_text=True)

    placeholder_symphony_present = _SYM_B in html_text
    placeholder_flagged = 'data-testid="obs-placeholder"' in html_text
    # The feature-off marker string must not be presented as a recommendation body
    # without being flagged as a placeholder.
    feature_off_shown = "feature_flag" in html_text and '"off"' in html_text

    if placeholder_symphony_present or feature_off_shown:
        assert placeholder_flagged, (
            "A NOT_APPLICABLE / feature-off placeholder row is rendered in the "
            "recommendations view WITHOUT a data-testid=\"obs-placeholder\" marker. "
            "AC-6: either omit feature-off stub rows or clearly label them so they "
            "do not read as recommendations."
        )


def test_not_applicable_verdict_is_visually_distinct_from_real_verdicts(flask_client, patched_role_accessor):
    """NOT_APPLICABLE must not be styled identically to a CLEAR/WATCH/BREACH verdict.

    The template's _VERDICT_CLASS maps only CLEAR/WATCH/BREACH; NOT_APPLICABLE
    falls through to a generic class today and reads like a real verdict. The
    render must give NOT_APPLICABLE its own class/marker (e.g. verdict-na or the
    obs-placeholder marker) so it is not mistaken for an actionable verdict.
    """
    resp = flask_client.get("/ai-advisor")
    assert resp.status_code == 200
    html_text = resp.get_data(as_text=True)

    if "NOT_APPLICABLE" in html_text:
        has_na_marker = bool(
            re.search(r"verdict-na\b", html_text)
            or 'data-testid="obs-placeholder"' in html_text
            or re.search(r'data-verdict-kind="not_applicable"', html_text, re.IGNORECASE)
        )
        assert has_na_marker, (
            "NOT_APPLICABLE is rendered without a distinct marker (verdict-na / "
            "obs-placeholder / data-verdict-kind). It reads like a real verdict. "
            "AC-6: make feature-off rows structurally distinct or omit them."
        )
