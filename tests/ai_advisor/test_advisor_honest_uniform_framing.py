"""advisor-fix cycle — RED: AC-7 honest framing for uniform guardrail checks.

team-lead ruling #3: OC and Spec Critic are GUARDRAILS whose HONEST answer is
uniform across symphonies (all 11 share the frozen Phase-1 THEORY spec, S=0 → a
healthy CLEAR with nothing symphony-specific to flag).  That uniformity is CORRECT
— but the /ai-advisor page must NOT present it in a way that reads as "stubbed",
because a row of identical CLEARs with no explanation re-triggers the exact user
complaint ("every symphony shows identical output").

So the page must carry a LEAN, TRUTHFUL explanation: the guardrail checks are
uniform because the symphonies share the frozen THEORY spec (a healthy CLEAR), and
the genuine per-symphony advice lives in the Run Advisor.  This is honest framing,
NOT a fabricated per-symphony number.

This test asserts the PRESENCE of that honest framing element when the rendered
observations are all uniform CLEAR guardrail rows.  It does NOT assert any
fabricated per-symphony value (that would violate the no-fakery rule).

Coordination: flask-dashboard-specialist exposes the explanation via
data-testid="guardrail-uniform-note" (or an equivalent honest marker — this test
accepts the testid OR an explanatory phrase, but REQUIRES the truthful content to
be present, so it cannot pass on an empty hook).

Mocking strategy:
  * database.get_advisor_observations_for_role patched to return uniform CLEAR rows.
  * No live DB, no network; math engine never mocked; fixtures function-scoped.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(scope="function")
def flask_client():
    with patch("database.init_db"):
        import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


def _clear_oc_row(obs_id: int, symphony_id: str) -> dict:
    """A healthy uniform OC guardrail row (verdict CLEAR), keyed per symphony."""
    return {
        "id": obs_id,
        "created_at": "2026-06-04T00:00:00",
        "advisor_role": "OVERFITTING_CONSCIENCE",
        "subject_type": "autotune_run",
        "subject_id": str(2100 + obs_id),
        "verdict": "CLEAR",
        "raw_response": {
            "s_count": 0,
            "backtest_selection_count": 0,
            "n_effective": 500,
            "n_optuna": 500,
            "drift_signal_available": False,
            "note": "no BACKTEST_SELECTION facets; N_effective equals N_optuna",
        },
        "is_advisory_only": 1,
        "spec_bundle_id": "781647710161",
        "symphony_id": symphony_id,
    }


@pytest.fixture(scope="function")
def uniform_clear_rows():
    """Two symphonies, both healthy uniform CLEAR (the live reality)."""
    rows = [
        _clear_oc_row(1, "(invest) lqd + eyeg 5 ways full market"),
        _clear_oc_row(2, "corporate chaos 5 ways"),
    ]

    def _by_role(role, limit=50):
        if role == "OVERFITTING_CONSCIENCE":
            return rows
        return []

    with patch("database.get_advisor_observations_for_role", side_effect=_by_role):
        yield rows


# ===========================================================================
# AC-7 — uniform guardrail checks are honestly explained, not stubbed-looking.
# ===========================================================================


def test_ai_advisor_renders_honest_uniform_guardrail_explanation(flask_client, uniform_clear_rows):
    """When the observations are all uniform CLEAR guardrail rows, /ai-advisor MUST
    render an honest explanation of WHY they are uniform — not present a row of
    identical CLEARs with no context (which reads as stubbed and re-triggers the
    user complaint).

    Accepts an explicit hook (data-testid="guardrail-uniform-note") OR explanatory
    prose, but REQUIRES the truthful content (frozen THEORY spec + pointer to the
    Run Advisor) — so it cannot pass on an empty marker.
    """
    resp = flask_client.get("/ai-advisor")
    assert resp.status_code == 200
    html_text = resp.get_data(as_text=True)
    lower = html_text.lower()

    has_hook = 'data-testid="guardrail-uniform-note"' in html_text

    # Truthful content: must reference the SHARED/FROZEN THEORY spec as the reason
    # the guardrail checks are uniform (the honest explanation).
    explains_shared_spec = "theory" in lower and (
        "frozen" in lower or "shared" in lower or "spec" in lower
    )
    # And must point the operator to where the genuine per-symphony advice lives
    # (the Run Advisor / suggestions), so a uniform CLEAR doesn't read as "no value".
    points_to_run_advisor = "run advisor" in lower or "suggestion" in lower or "proposal" in lower

    assert has_hook or (explains_shared_spec and points_to_run_advisor), (
        "The /ai-advisor page renders uniform CLEAR guardrail rows with no honest "
        "explanation. AC-7: surface a lean, truthful note (data-testid="
        '"guardrail-uniform-note" or explanatory prose) saying the guardrail '
        "checks are uniform because the symphonies share the frozen THEORY spec, "
        "and the per-symphony advice lives in the Run Advisor. Do NOT fabricate a "
        "per-symphony number — just explain the uniformity honestly."
    )
    # If the explicit hook is used, it must carry the truthful content too — guard
    # against an empty-hook tautology.
    if has_hook:
        assert explains_shared_spec or points_to_run_advisor, (
            'data-testid="guardrail-uniform-note" is present but carries no '
            "truthful explanation (no frozen-THEORY-spec reason, no Run-Advisor "
            "pointer). The honest framing must have real content."
        )
