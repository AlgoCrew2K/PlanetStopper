"""RED tests — M5 Chat route in app.py (/ai-advisor/chat).

Tests the Flask route handler for the explain-only chat capability (AC-4.1/4.2/4.3).

AC bindings:
  AC-4.1  Chat route must be read-only (or constrained POST returning only
          explanatory text).  No write path, no path to accept/apply routes.
          The route must not call mutation functions on the app or DB.
  AC-4.2  Chat route grounds answers in the supplied artifact — it calls
          advisor_chat.explain_artifact and returns the explanation.
  AC-4.3  No LLM key / LLM error → route returns a well-shaped JSON error with
          "chat unavailable"; never a 500, never a fabricated trade instruction.
  AC-X1   Route must not call Composer write endpoints.
  AC-X3   Route must not call database.insert_advisor_observation.

THE ADVERSARIAL FOCUS (AC-4.1 route-level boundary):

1. **Route is read-only**: POST /ai-advisor/chat returns JSON only.  It must not
   call save_symphony_strategy, save_state, insert_advisor_observation, or any
   mutation function in the route handler body.

2. **Route has no path to accept/apply**: the route handler must not call
   ai_advisor_accept, revalidate_suggestion_oos, or any config-write function.

3. **Error path returns JSON, not 500**: when advisor_chat.explain_artifact
   returns an error, the route returns a 200 or 4xx JSON with the error string,
   never an unhandled 500 or HTML error page.

Mocking strategy:
  - advisors.advisor_chat.explain_artifact is patched for all path tests.
  - database.insert_advisor_observation is spied upon to assert it is never called.
  - No live Anthropic calls.

Fixture: tests/fixtures/ai_advisor/m5/chat_engine_explain_only.json (AC provenance anchor).
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# These tests require the Flask test client.
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Return a Flask test client with app in testing mode."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Minimal ChatResponse stub for mocking explain_artifact.
# ---------------------------------------------------------------------------

def _make_chat_response(answer: str = None, error: str = None):
    """Build a minimal ChatResponse-like object."""
    obj = MagicMock()
    obj.answer = answer
    obj.error = error
    return obj


# ---------------------------------------------------------------------------
# Helper: assert a response is not 404 (route must exist).
# ---------------------------------------------------------------------------

def _assert_route_exists(resp, route: str = "POST /ai-advisor/chat"):
    """Fail with a clear message if the route returned 404."""
    assert resp.status_code != 404, (
        f"{route} returned 404 — the route has not been added to app.py. "
        "GREEN must add the route handler before this test can pass."
    )


# ===========================================================================
# Test 1 — Route existence: POST /ai-advisor/chat must exist.
#
# These tests are RED until GREEN adds the route to app.py.
# ===========================================================================


def test_chat_route_exists_and_returns_200_on_valid_request(client):
    """POST /ai-advisor/chat must exist and return a 200 response.

    RED until GREEN adds the route to app.py (currently returns 404).
    """
    fake_response = _make_chat_response(
        answer="This swap reduces correlation between Symphony Alpha and Symphony Beta."
    )

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        resp = client.post(
            "/ai-advisor/chat",
            json={
                "artifact_type": "asset_swap_proposal",
                "artifact_id": "swap-001",
                "artifact": {"incumbent_asset": "GLD", "candidate_asset": "IALT"},
                "question": "Why did this swap survive the gate?",
            },
        )

    _assert_route_exists(resp)
    assert resp.status_code == 200, (
        f"POST /ai-advisor/chat returned {resp.status_code}, expected 200"
    )


def test_chat_route_returns_json(client):
    """POST /ai-advisor/chat must return a JSON response (not HTML).

    The chat route is an AJAX endpoint — it returns JSON so the JS panel can
    render the explanation without a page reload.
    """
    fake_response = _make_chat_response(
        answer="The gate used a BHY/Yekutieli FDR correction across all candidates."
    )

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        resp = client.post(
            "/ai-advisor/chat",
            json={
                "artifact_type": "gate_verdict",
                "artifact_id": "gate-lc-007",
                "artifact": {"verdict": "WITHHOLD", "n_candidates": 8},
                "question": "Why was this withheld?",
            },
        )

    _assert_route_exists(resp)
    assert resp.content_type.startswith("application/json"), (
        f"POST /ai-advisor/chat must return JSON, got content-type: {resp.content_type}"
    )


def test_chat_route_response_contains_explanation_on_success(client):
    """On success, POST /ai-advisor/chat JSON must contain an 'explanation' key.

    The JS panel reads response.explanation to render the AI message bubble.
    A response without this key leaves the panel blank.
    """
    explanation_text = (
        "The correlation coefficient measures the linear association between return series."
    )

    fake_response = _make_chat_response(answer=explanation_text)

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        resp = client.post(
            "/ai-advisor/chat",
            json={
                "artifact_type": "correlation_diagnostic",
                "artifact_id": "corr-001",
                "artifact": {"correlation_value": 0.74, "obs_count": 62},
                "question": "What does this correlation mean?",
            },
        )

    _assert_route_exists(resp)
    data = json.loads(resp.data)
    assert "explanation" in data, (
        f"POST /ai-advisor/chat response must contain 'explanation' key. "
        f"Got keys: {sorted(data.keys())}"
    )
    assert data["explanation"], (
        "POST /ai-advisor/chat 'explanation' must be a non-empty string on success"
    )


def test_chat_route_calls_explain_artifact(client):
    """Route handler must call advisors.advisor_chat.explain_artifact.

    This pins the wiring: the route must delegate to the chat backend, not
    produce explanations inline (which would be untestable and violate separation).
    """
    with patch("advisors.advisor_chat.explain_artifact",
               return_value=_make_chat_response(answer="Explanation.")) as mock_fn:
        resp = client.post(
            "/ai-advisor/chat",
            json={
                "artifact_type": "asset_swap_proposal",
                "artifact_id": "swap-001",
                "artifact": {"gate_verdict": "ADOPT_CANDIDATE"},
                "question": "Explain this swap.",
            },
        )

    _assert_route_exists(resp)
    assert mock_fn.call_count >= 1, (
        "Route handler must call advisors.advisor_chat.explain_artifact. "
        "The explanation must be delegated to the chat backend, not produced inline."
    )


# ===========================================================================
# Test 2 — AC-4.1: route must not call mutation functions.
#
# These tests pass once the route exists AND does not call mutation functions.
# They are RED now because the route doesn't exist; they remain RED if GREEN
# adds a route that incorrectly calls mutation functions.
# ===========================================================================


def test_chat_route_does_not_call_insert_advisor_observation(client):
    """POST /ai-advisor/chat must never call database.insert_advisor_observation.

    AC-X3: chat is a Q&A surface — zero DB writes.  A route that calls
    insert_advisor_observation would be creating a new observation from chat,
    which violates the explain-only contract.
    """
    fake_response = _make_chat_response(
        answer="The FDR correction applied across 8 candidates."
    )

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        with patch("database.insert_advisor_observation") as mock_insert:
            resp = client.post(
                "/ai-advisor/chat",
                json={
                    "artifact_type": "gate_verdict",
                    "artifact_id": "gate-lc-007",
                    "artifact": {"verdict": "WITHHOLD"},
                    "question": "Why was this withheld?",
                },
            )

    _assert_route_exists(resp)
    assert mock_insert.call_count == 0, (
        f"POST /ai-advisor/chat called database.insert_advisor_observation "
        f"{mock_insert.call_count} times — chat must write no DB records (AC-X3)."
    )


def test_chat_route_does_not_call_save_state(client):
    """POST /ai-advisor/chat must never call database.save_state.

    save_state writes bot_state — the live execution table.  A chat route that
    reaches it has a write path to the execution surface (AC-4.1 violation).
    """
    fake_response = _make_chat_response(answer="Explanation text.")

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        with patch("database.save_state") as mock_save:
            resp = client.post(
                "/ai-advisor/chat",
                json={
                    "artifact_type": "asset_swap_proposal",
                    "artifact_id": "swap-001",
                    "artifact": {"gate_verdict": "ADOPT_CANDIDATE"},
                    "question": "Explain this.",
                },
            )

    _assert_route_exists(resp)
    assert mock_save.call_count == 0, (
        f"POST /ai-advisor/chat called database.save_state {mock_save.call_count} times — "
        "save_state writes the live execution table; chat must not reach it (AC-4.1)."
    )


def test_chat_route_does_not_call_revalidate_suggestion_oos(client):
    """POST /ai-advisor/chat must not call ai_advisor.revalidate_suggestion_oos.

    revalidate_suggestion_oos is a step in the config-accept flow — it checks
    whether a config suggestion beats the OOS baseline and is a direct gateway
    to config writes.  A chat route that calls it would have a path to the
    accept mutation (AC-4.1 violation).
    """
    fake_response = _make_chat_response(answer="Explanation text.")

    import ai_advisor
    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        with patch.object(ai_advisor, "revalidate_suggestion_oos") as mock_reval:
            resp = client.post(
                "/ai-advisor/chat",
                json={
                    "artifact_type": "gate_verdict",
                    "artifact_id": "gate-001",
                    "artifact": {"verdict": "WITHHOLD"},
                    "question": "Explain this gate result.",
                },
            )

    _assert_route_exists(resp)
    assert mock_reval.call_count == 0, (
        f"POST /ai-advisor/chat called ai_advisor.revalidate_suggestion_oos — "
        "this is the config-accept OOS gate; chat must not have a path to it (AC-4.1)."
    )


# ===========================================================================
# Test 3 — AC-4.3: error path returns JSON, not a crash.
# ===========================================================================


def test_chat_route_returns_json_error_when_llm_unavailable(client):
    """When explain_artifact returns an error, route returns JSON error, not 500.

    AC-4.3: no LLM key → clear 'chat unavailable' JSON response, never a crash.
    The JSON must have an 'error' key containing the unavailability message.
    """
    fake_error = _make_chat_response(
        answer=None,
        error="chat unavailable: no LLM API key configured",
    )

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_error):
        resp = client.post(
            "/ai-advisor/chat",
            json={
                "artifact_type": "gate_verdict",
                "artifact_id": "gate-lc-007",
                "artifact": {"verdict": "WITHHOLD"},
                "question": "Why?",
            },
        )

    _assert_route_exists(resp)
    assert resp.status_code in (200, 503), (
        f"On LLM unavailability, route must return 200 or 503 (not crash). "
        f"Got {resp.status_code}"
    )
    assert resp.content_type.startswith("application/json"), (
        f"Error response must be JSON, got: {resp.content_type}"
    )
    data = json.loads(resp.data)
    assert "error" in data, (
        f"Error response must contain 'error' key so the JS panel shows the "
        f"'chat unavailable' state. Got keys: {sorted(data.keys())}"
    )
    assert data["error"], "Error key must be a non-empty string"


def test_chat_route_never_returns_500_on_explain_artifact_error(client):
    """Route must not return 500 when explain_artifact yields an error response.

    A 500 means an unhandled exception propagated out of the route handler —
    which violates AC-4.3 (never a crash) and breaks the UI.
    """
    fake_error = _make_chat_response(
        answer=None,
        error="chat unavailable: connection timeout",
    )

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_error):
        resp = client.post(
            "/ai-advisor/chat",
            json={
                "artifact_type": "asset_swap_proposal",
                "artifact_id": "swap-001",
                "artifact": {"gate_verdict": "ADOPT_CANDIDATE"},
                "question": "Explain.",
            },
        )

    _assert_route_exists(resp)
    assert resp.status_code != 500, (
        f"Route returned 500 when explain_artifact returned an error response — "
        "the route handler must handle the error gracefully and return a "
        "well-formed JSON error (AC-4.3). Status: {resp.status_code}"
    )


def test_chat_route_returns_400_on_missing_required_fields(client):
    """POST /ai-advisor/chat with missing required fields must return a 4xx, not 500.

    A malformed request (no 'question' or no 'artifact') must be handled gracefully.
    """
    resp = client.post(
        "/ai-advisor/chat",
        json={
            # Missing 'artifact' and 'question'
            "artifact_type": "gate_verdict",
        },
    )

    _assert_route_exists(resp)
    assert resp.status_code != 500, (
        f"Route returned 500 on missing required fields — must handle gracefully. "
        f"Status: {resp.status_code}"
    )
    assert resp.status_code in (400, 422, 200), (
        f"Missing required fields must return 4xx or handled 200, not {resp.status_code}"
    )


# ===========================================================================
# Test 4 — AC-4.1 adversarial: route calls explain_artifact with only
# (question, artifact) — no extra kwargs that could reach a write path.
# ===========================================================================


def test_chat_route_calls_explain_artifact_with_question_and_artifact_only(client):
    """The route handler must call explain_artifact with only (question, artifact).

    Any extra kwargs on the call to explain_artifact could indicate a write path
    embedded in the function signature — an AC-4.1 risk.  The call must be
    tightly scoped.
    """
    with patch(
        "advisors.advisor_chat.explain_artifact",
        return_value=_make_chat_response(answer="Explanation."),
    ) as mock_fn:
        resp = client.post(
            "/ai-advisor/chat",
            json={
                "artifact_type": "asset_swap_proposal",
                "artifact_id": "swap-001",
                "artifact": {"gate_verdict": "ADOPT_CANDIDATE"},
                "question": "Explain this swap.",
            },
        )

    _assert_route_exists(resp)
    assert mock_fn.call_count >= 1, (
        "Route must call advisors.advisor_chat.explain_artifact at least once."
    )

    call_kwargs = mock_fn.call_args.kwargs
    call_args = mock_fn.call_args.args

    if call_args:
        assert len(call_args) <= 2, (
            f"explain_artifact was called with {len(call_args)} positional args, "
            "expected at most 2 (question, artifact)"
        )
    forbidden_kwargs = set(call_kwargs.keys()) - {"question", "artifact"}
    assert not forbidden_kwargs, (
        f"explain_artifact was called with unexpected kwargs: {sorted(forbidden_kwargs)}. "
        "The call signature must be explain_artifact(question, artifact) only — "
        "extra kwargs suggest a hidden write or action path (AC-4.1)."
    )
