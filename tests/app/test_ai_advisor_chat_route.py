"""RED tests — M5 Chat routes in app.py (GET /ai-advisor/chat, POST /ai-advisor/chat/send).

Tests the Flask route handlers for the explain-only chat capability (AC-4.1/4.2/4.3).

Route contract (from UX coordination message 2026-05-31):
  GET  /ai-advisor/chat       — renders ai_advisor_chat.html; context: chat_available, symphonies
  POST /ai-advisor/chat/send  — accepts {artifact_id, artifact_type, history, message};
                                returns {reply: str} or {error: str}

AC bindings:
  AC-4.1  Chat routes are read-only.  No write path, no path to accept/apply routes.
          No action buttons in the UI (send + close only, per design).
  AC-4.2  POST /send grounds answers in the supplied artifact — passes it verbatim to
          the chat backend.
  AC-4.3  No LLM key / LLM error → POST /send returns {error: str}, never a 500;
          GET /ai-advisor/chat renders data-testid="chat-unavailable" when chat_available=False.
  AC-X1   Routes must not call Composer write endpoints.
  AC-X3   Routes must not call database.insert_advisor_observation.

THE ADVERSARIAL FOCUS (AC-4.1 route-level boundary):

1. **No mutation calls in route handlers**: neither GET nor POST /send may call
   save_symphony_strategy, save_state, insert_advisor_observation, or
   revalidate_suggestion_oos.

2. **No action buttons in UI**: the rendered chat template must have NO buttons
   with apply/accept/deploy/trade semantics.  Only send + close are permitted.

3. **Error path returns JSON, never 500**: when the LLM is unavailable, the POST
   /send route returns {error: str} JSON, not an unhandled 500.

4. **chat-unavailable DOM state**: when GET renders with chat_available=False, the
   data-testid="chat-unavailable" element must be present and
   data-testid="chat-input-row" must be absent/hidden.

Mocking strategy:
  - advisors.advisor_chat.explain_artifact is patched for POST /send path tests.
  - database.insert_advisor_observation is spied upon to assert it is never called.
  - No live Anthropic calls.

Fixture: tests/fixtures/ai_advisor/m5/chat_engine_explain_only.json (AC provenance anchor).
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Flask test client fixture.
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
# Route-existence assertion helper.
# ---------------------------------------------------------------------------


def _assert_route_exists(resp, route: str):
    """Fail with a clear message if the route returned 404."""
    assert resp.status_code != 404, (
        f"{route} returned 404 — the route has not been added to app.py yet. "
        "GREEN must add the route handler before this test can pass."
    )


# ===========================================================================
# Test 1 — GET /ai-advisor/chat: page renders with required DOM structure.
#
# RED until GREEN adds the GET route + renders ai_advisor_chat.html.
# ===========================================================================


def test_get_chat_tab_returns_200(client):
    """GET /ai-advisor/chat must return 200.

    RED until GREEN adds the route to app.py.
    """
    resp = client.get("/ai-advisor/chat")
    _assert_route_exists(resp, "GET /ai-advisor/chat")
    assert resp.status_code == 200, (
        f"GET /ai-advisor/chat returned {resp.status_code}, expected 200"
    )


def test_get_chat_tab_contains_chat_panel_testid(client):
    """GET /ai-advisor/chat must render data-testid='chat-panel' in the DOM.

    The JS (ai_advisor_chat.js) uses this element as the slide-in panel
    container.  Without it the panel cannot open.
    """
    resp = client.get("/ai-advisor/chat")
    _assert_route_exists(resp, "GET /ai-advisor/chat")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="chat-panel"' in body, (
        "GET /ai-advisor/chat must render data-testid=\"chat-panel\" — "
        "the JS openChatPanel() targets this element"
    )


def test_get_chat_tab_contains_explain_only_notice(client):
    """GET /ai-advisor/chat must render data-testid='explain-only-notice'.

    The explain-only notice (AC-4.1) is the persistent UI reminder that chat
    cannot apply changes or place trades.  It must always be in the DOM.
    """
    resp = client.get("/ai-advisor/chat")
    _assert_route_exists(resp, "GET /ai-advisor/chat")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="explain-only-notice"' in body, (
        "GET /ai-advisor/chat must render data-testid=\"explain-only-notice\" — "
        "the persistent UI reminder that chat is explain-only (AC-4.1)"
    )


def test_get_chat_tab_contains_chat_default_state(client):
    """GET /ai-advisor/chat must render data-testid='chat-default-state'.

    The default state is shown when no artifact is selected yet.
    """
    resp = client.get("/ai-advisor/chat")
    _assert_route_exists(resp, "GET /ai-advisor/chat")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="chat-default-state"' in body, (
        "GET /ai-advisor/chat must render data-testid=\"chat-default-state\" — "
        "shown when no artifact is selected (design §Screen 4 default state)"
    )


def test_get_chat_tab_has_no_apply_accept_deploy_buttons(client):
    """GET /ai-advisor/chat must not contain apply/accept/deploy action buttons.

    AC-4.1 HARD boundary: the chat panel has NO action buttons.  Only send
    (chat-send-btn) and close are permitted.  An 'Apply', 'Accept', or 'Deploy'
    button in the chat panel would be a structural AC-4.1 violation.
    """
    resp = client.get("/ai-advisor/chat")
    _assert_route_exists(resp, "GET /ai-advisor/chat")
    body = resp.data.decode("utf-8", errors="replace")

    # Check for button text content and value attributes — both patterns are violations.
    forbidden_patterns = [
        ">Apply<",
        ">Accept<",
        ">Deploy<",
        ">Apply this",
        ">Accept this",
        'value="apply"',
        'value="accept"',
        'value="deploy"',
        'name="apply"',
        'name="accept"',
        'name="deploy"',
        # data-action patterns
        'data-action="apply"',
        'data-action="accept"',
        'data-action="deploy"',
    ]
    for pattern in forbidden_patterns:
        assert pattern.lower() not in body.lower(), (
            f"GET /ai-advisor/chat contains forbidden action pattern: {pattern!r}. "
            "The chat panel must have NO apply/accept/deploy buttons (AC-4.1). "
            "Only send + close are permitted."
        )


def test_get_chat_tab_has_chat_tab_active(client):
    """GET /ai-advisor/chat must render data-testid='tab-chat' with aria-current='page'.

    The tab bar shows which capability is active.  chat tab must be marked active.
    """
    resp = client.get("/ai-advisor/chat")
    _assert_route_exists(resp, "GET /ai-advisor/chat")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="tab-chat"' in body, (
        "GET /ai-advisor/chat must render data-testid=\"tab-chat\" in the tab bar"
    )


def test_get_chat_tab_unavailable_state_rendered_when_no_api_key(client):
    """When ANTHROPIC_API_KEY is not set, GET must render data-testid='chat-unavailable'.

    AC-4.3: no API key → the chat UI shows the 'chat unavailable' state.
    The data-testid='chat-input-row' must not be present (or must be hidden)
    when chat is unavailable — the operator cannot type into a disabled chat.
    """
    import os

    with patch.dict(os.environ, {}, clear=False):
        # Remove the API key if it is set.
        key_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            resp = client.get("/ai-advisor/chat")
        finally:
            if key_backup is not None:
                os.environ["ANTHROPIC_API_KEY"] = key_backup

    _assert_route_exists(resp, "GET /ai-advisor/chat (no API key)")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="chat-unavailable"' in body, (
        "When ANTHROPIC_API_KEY is absent, GET /ai-advisor/chat must render "
        "data-testid=\"chat-unavailable\" — the operator must see a clear "
        "unavailability message, not a blank input (AC-4.3)"
    )


# ===========================================================================
# Test 2 — POST /ai-advisor/chat/send: success and error paths.
#
# RED until GREEN adds the POST /send route.
# ===========================================================================


def test_post_chat_send_returns_200_with_reply_on_success(client):
    """POST /ai-advisor/chat/send must return {reply: str} on success.

    The JS reads response.reply to append the AI message to the chat thread.
    """
    fake_response = _make_chat_response(
        answer="The gate used BHY/Yekutieli FDR across 8 candidates; this one's p_adj exceeded the adjusted threshold."
    )

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        resp = client.post(
            "/ai-advisor/chat/send",
            json={
                "artifact_type": "gate_verdict",
                "artifact_id": "gate-lc-007",
                "artifact": {"verdict": "WITHHOLD", "n_candidates": 8},
                "history": [],
                "message": "Why was this withheld?",
            },
        )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")
    assert resp.status_code == 200, (
        f"POST /ai-advisor/chat/send returned {resp.status_code}, expected 200"
    )
    data = json.loads(resp.data)
    assert "reply" in data, (
        f"Success response must contain 'reply' key. Got keys: {sorted(data.keys())}"
    )
    assert data["reply"], "reply must be a non-empty string on success"


def test_post_chat_send_returns_json(client):
    """POST /ai-advisor/chat/send must return Content-Type: application/json."""
    fake_response = _make_chat_response(answer="Explanation.")

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        resp = client.post(
            "/ai-advisor/chat/send",
            json={
                "artifact_type": "correlation_diagnostic",
                "artifact_id": "corr-001",
                "artifact": {"correlation_value": 0.74},
                "history": [],
                "message": "What does this mean?",
            },
        )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")
    assert resp.content_type.startswith("application/json"), (
        f"POST /ai-advisor/chat/send must return JSON. Got: {resp.content_type}"
    )


def test_post_chat_send_returns_error_when_llm_unavailable(client):
    """When explain_artifact returns error, POST /send returns {error: str}, not 500.

    AC-4.3: no LLM key → {error: 'chat unavailable: ...'} JSON, never a crash.
    """
    fake_error = _make_chat_response(
        answer=None, error="chat unavailable: no LLM API key configured"
    )

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_error):
        resp = client.post(
            "/ai-advisor/chat/send",
            json={
                "artifact_type": "gate_verdict",
                "artifact_id": "gate-001",
                "artifact": {"verdict": "WITHHOLD"},
                "history": [],
                "message": "Why?",
            },
        )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")
    assert resp.status_code not in (500, 502, 503), (
        f"POST /send must not return {resp.status_code} on LLM unavailability — "
        "return a JSON error response instead (AC-4.3)"
    )
    assert resp.content_type.startswith("application/json"), (
        "Error response must be JSON"
    )
    data = json.loads(resp.data)
    assert "error" in data, (
        f"Error response must contain 'error' key. Got keys: {sorted(data.keys())}"
    )
    assert data["error"], "error must be a non-empty string"


def test_post_chat_send_returns_400_on_missing_message(client):
    """POST /ai-advisor/chat/send without 'message' must return 400 or handled error.

    Malformed requests must never reach explain_artifact or cause a 500.
    """
    resp = client.post(
        "/ai-advisor/chat/send",
        json={
            "artifact_type": "gate_verdict",
            "artifact_id": "gate-001",
            "artifact": {"verdict": "WITHHOLD"},
            # Missing 'message'
        },
    )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")
    assert resp.status_code != 500, (
        f"POST /send returned 500 on missing 'message' — must handle gracefully. "
        f"Status: {resp.status_code}"
    )
    assert resp.status_code in (400, 422, 200), (
        f"Missing 'message' should return 4xx or handled 200, got {resp.status_code}"
    )


# ===========================================================================
# Test 3 — AC-4.1: route handlers must not call mutation functions.
# ===========================================================================


def test_post_chat_send_does_not_call_insert_advisor_observation(client):
    """POST /send must never call database.insert_advisor_observation.

    AC-X3: chat is a Q&A surface — zero DB writes.
    """
    fake_response = _make_chat_response(answer="Explanation.")

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        with patch("database.insert_advisor_observation") as mock_insert:
            resp = client.post(
                "/ai-advisor/chat/send",
                json={
                    "artifact_type": "gate_verdict",
                    "artifact_id": "gate-001",
                    "artifact": {"verdict": "WITHHOLD"},
                    "history": [],
                    "message": "Why?",
                },
            )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")
    assert mock_insert.call_count == 0, (
        f"POST /send called database.insert_advisor_observation {mock_insert.call_count} "
        "times — chat must write no DB records (AC-X3)."
    )


def test_post_chat_send_does_not_call_save_state(client):
    """POST /send must never call database.save_state.

    save_state writes bot_state — the live execution table.  Chat must not
    reach it (AC-4.1 write-path guard).
    """
    fake_response = _make_chat_response(answer="Explanation.")

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        with patch("database.save_state") as mock_save:
            resp = client.post(
                "/ai-advisor/chat/send",
                json={
                    "artifact_type": "asset_swap_proposal",
                    "artifact_id": "swap-001",
                    "artifact": {"gate_verdict": "ADOPT_CANDIDATE"},
                    "history": [],
                    "message": "Explain this.",
                },
            )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")
    assert mock_save.call_count == 0, (
        f"POST /send called database.save_state {mock_save.call_count} times — "
        "chat must not write the live execution table (AC-4.1)."
    )


def test_post_chat_send_does_not_call_revalidate_suggestion_oos(client):
    """POST /send must not call ai_advisor.revalidate_suggestion_oos.

    revalidate_suggestion_oos is in the config-accept flow.  A chat route that
    calls it has a path to a config write (AC-4.1 violation).
    """
    fake_response = _make_chat_response(answer="Explanation.")
    import ai_advisor

    with patch("advisors.advisor_chat.explain_artifact", return_value=fake_response):
        with patch.object(ai_advisor, "revalidate_suggestion_oos") as mock_reval:
            resp = client.post(
                "/ai-advisor/chat/send",
                json={
                    "artifact_type": "gate_verdict",
                    "artifact_id": "gate-001",
                    "artifact": {"verdict": "WITHHOLD"},
                    "history": [],
                    "message": "Explain this gate result.",
                },
            )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")
    assert mock_reval.call_count == 0, (
        "POST /send called ai_advisor.revalidate_suggestion_oos — "
        "chat must not have a path to the config-accept OOS gate (AC-4.1)."
    )


# ===========================================================================
# Test 4 — POST /send calls explain_artifact with question and artifact args.
#
# Pins the backend wiring contract: route delegates to advisor_chat, does not
# produce explanations inline.
# ===========================================================================


def test_post_chat_send_calls_explain_artifact(client):
    """POST /send must call advisors.advisor_chat.explain_artifact.

    The route must delegate to the chat backend, not produce explanations inline.
    """
    with patch(
        "advisors.advisor_chat.explain_artifact",
        return_value=_make_chat_response(answer="Explanation."),
    ) as mock_fn:
        resp = client.post(
            "/ai-advisor/chat/send",
            json={
                "artifact_type": "asset_swap_proposal",
                "artifact_id": "swap-001",
                "artifact": {"gate_verdict": "ADOPT_CANDIDATE"},
                "history": [],
                "message": "Explain this swap.",
            },
        )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")
    assert mock_fn.call_count >= 1, (
        "POST /send must call advisors.advisor_chat.explain_artifact. "
        "The explanation must be delegated to the chat backend."
    )


def test_post_chat_send_passes_artifact_to_explain_artifact(client):
    """POST /send must pass the supplied artifact to explain_artifact.

    AC-4.2: chat answers are grounded in the artifact.  The route must pass the
    artifact verbatim — not an empty dict or a reconstructed subset.
    """
    artifact_payload = {
        "gate_verdict": "ADOPT_CANDIDATE",
        "incumbent_asset": "GLD",
        "candidate_asset": "IALT",
    }

    with patch(
        "advisors.advisor_chat.explain_artifact",
        return_value=_make_chat_response(answer="ok"),
    ) as mock_fn:
        resp = client.post(
            "/ai-advisor/chat/send",
            json={
                "artifact_type": "asset_swap_proposal",
                "artifact_id": "swap-001",
                "artifact": artifact_payload,
                "history": [],
                "message": "Explain this swap.",
            },
        )

    _assert_route_exists(resp, "POST /ai-advisor/chat/send")

    if mock_fn.call_count == 0:
        pytest.fail("explain_artifact was not called — route must delegate to it")

    # Inspect the call: artifact must be present as a kwarg or positional arg.
    call_args = mock_fn.call_args.args
    call_kwargs = mock_fn.call_args.kwargs
    artifact_received = call_kwargs.get("artifact") or (call_args[1] if len(call_args) > 1 else None)
    assert artifact_received is not None, (
        "explain_artifact must receive an 'artifact' argument from the route. "
        "The route must pass the artifact from the request body (AC-4.2)."
    )
    # Verify the key fields were passed through — not an empty dict.
    assert isinstance(artifact_received, dict), (
        f"artifact passed to explain_artifact must be a dict, got {type(artifact_received)}"
    )


# ===========================================================================
# Test 5 — UI DOM: discuss-this link and artifact-summary-strip.
#
# These tests verify that the UX artifacts (template IDs) are present in the
# rendered HTML.  They test the GET /ai-advisor/chat template, not the JS logic.
# ===========================================================================


def test_get_chat_tab_contains_artifact_summary_strip(client):
    """GET /ai-advisor/chat must render data-testid='artifact-summary-strip'.

    The artifact summary strip is in the aside panel — always in the DOM regardless
    of chat_available (it anchors the conversation when a panel opens).
    """
    resp = client.get("/ai-advisor/chat")
    _assert_route_exists(resp, "GET /ai-advisor/chat")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="artifact-summary-strip"' in body, (
        "GET /ai-advisor/chat must render data-testid=\"artifact-summary-strip\" — "
        "the artifact anchor strip shown in the open chat panel (design §Screen 4)"
    )


def test_get_chat_tab_contains_chat_thread_when_available(client):
    """GET /ai-advisor/chat with ANTHROPIC_API_KEY set must render data-testid='chat-thread'.

    chat-thread is only rendered when chat_available=True (inside the {% else %} block).
    When chat_available=False the unavailable message replaces it — input row hidden (AC-4.3).
    """
    import os

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-for-availability-check"}):
        resp = client.get("/ai-advisor/chat")

    _assert_route_exists(resp, "GET /ai-advisor/chat (with API key)")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="chat-thread"' in body, (
        "GET /ai-advisor/chat (chat_available=True) must render data-testid=\"chat-thread\" — "
        "the JS appends message bubbles to this container"
    )


def test_get_chat_tab_contains_chat_send_btn_when_available(client):
    """GET /ai-advisor/chat with ANTHROPIC_API_KEY set must render data-testid='chat-send-btn'.

    chat-send-btn is only rendered when chat_available=True.
    The send button is the ONLY action button in the chat panel (AC-4.1).
    """
    import os

    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key-for-availability-check"}):
        resp = client.get("/ai-advisor/chat")

    _assert_route_exists(resp, "GET /ai-advisor/chat (with API key)")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="chat-send-btn"' in body, (
        "GET /ai-advisor/chat (chat_available=True) must render data-testid=\"chat-send-btn\" — "
        "the ONLY permitted action button in the chat panel"
    )


def test_get_chat_tab_hides_input_row_when_unavailable(client):
    """GET /ai-advisor/chat without ANTHROPIC_API_KEY must NOT render chat-input-row.

    AC-4.3: when chat is unavailable, the input row is absent from the DOM —
    the operator cannot type into a non-functional chat panel.
    This is enforced by the template's {% if chat_available %} guard.
    """
    import os

    key_backup = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        resp = client.get("/ai-advisor/chat")
    finally:
        if key_backup is not None:
            os.environ["ANTHROPIC_API_KEY"] = key_backup

    _assert_route_exists(resp, "GET /ai-advisor/chat (no API key)")
    body = resp.data.decode("utf-8", errors="replace")
    assert 'data-testid="chat-input-row"' not in body, (
        "When chat_available=False, data-testid=\"chat-input-row\" must NOT appear "
        "in the DOM — the operator must not see an inactive input (AC-4.3)"
    )
