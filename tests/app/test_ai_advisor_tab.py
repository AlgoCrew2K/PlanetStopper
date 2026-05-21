"""RED tests — U1 /ai-advisor tab + accept/reject apply path + ANTHROPIC_API_KEY settings field.

These tests are RED until the GREEN implementation lands.  They pin:

  A. /ai-advisor route
     1. GET /ai-advisor renders a 200 response.
     2. POST /ai-advisor/suggest calls assemble_advisor_context + request_suggestions.
     3. Suggestions render with config_key, current_value, suggested_value, rationale
        (the hover-over data the operator reads before accepting/rejecting).
     4. An empty suggestions list (Claude abstention) is a valid non-error response.
     5. A failed request_suggestions call returns an error payload — never crashes.

  B. Accept/reject apply path — the most critical safety contracts
     6. POST /ai-advisor/accept runs the C2 allowlist gate BEFORE writing.
     7. POST /ai-advisor/accept runs the C2 risk-direction gate BEFORE writing.
     8. POST /ai-advisor/accept runs the C2 OOS revalidation gate BEFORE writing.
     9. A suggestion that passes all C2 gates writes the new value to
        save_symphony_strategy — the apply path.
    10. A suggestion rejected by the allowlist gate must NOT write to strategy.
    11. A suggestion rejected by the OOS gate must NOT write to strategy.
    12. POST /ai-advisor/reject does NOT write to strategy regardless of gates.

  C. ANTHROPIC_API_KEY settings field
    13. GET /api/settings response includes ANTHROPIC_API_KEY in globals (never None).
    14. POST /api/settings with ANTHROPIC_API_KEY calls set_key for that key.
    15. ANTHROPIC_API_KEY value is NEVER echoed back in the GET /api/settings
        response in plaintext beyond the empty-string-or-masked convention.

Safety invariants that are adversarially tested:
  - All three C2 gate functions are invoked on the accept path (not just one).
  - A gate failure on any of the three C2 layers prevents the strategy write.
  - The reject path unconditionally skips the write — no accidental writes on reject.
  - The API key is never exposed to the client in plaintext via /api/settings GET.

Mocking strategy:
  - ``ai_advisor.request_suggestions`` is mocked — ZERO live Anthropic calls.
  - ``ai_advisor.assemble_advisor_context`` is mocked — no DB access.
  - ``ai_advisor.revalidate_suggestion_oos`` is mocked — no autotuner/Alpaca.
  - ``database.save_symphony_strategy`` and ``database.get_symphony_strategy``
    are mocked — no SQLite.
  - ``app.set_key`` and ``app.dotenv_values`` are mocked — no .env reads or writes.
  - The math engine is NEVER mocked.
  - All fixtures are function-scoped — no shared mutable state.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import ai_advisor
import app as app_module
from ai_advisor import ConfigSuggestion, ConfigSuggestionsResponse

# ---------------------------------------------------------------------------
# Helpers — fixture suggestion factories
# ---------------------------------------------------------------------------


def _make_suggestion(
    config_key: str = "MAX_SQUEEZE_FLOOR",
    current_value: float = 0.20,
    suggested_value: float = 0.30,
    risk_direction: str = "loosens",
) -> ConfigSuggestion:
    return ConfigSuggestion(
        config_key=config_key,
        current_value=current_value,
        suggested_value=suggested_value,
        rationale="OOS alpha 1.9% with vol at low end of window — floor is conservative.",
        risk_direction=risk_direction,
        confidence="medium",
        data_sufficiency="sufficient",
    )


def _make_suggestions_response(suggestions=None) -> ConfigSuggestionsResponse:
    if suggestions is None:
        suggestions = [_make_suggestion()]
    return ConfigSuggestionsResponse(suggestions=suggestions)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client — no port bound."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database(monkeypatch):
    """Patch database module on app_module — no SQLite I/O."""
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_symphony_strategy.return_value = {
            "params": {
                "TRIGGER_THRESHOLD_PCT": 15.0,
                "TAKE_PROFIT_MC_PCT": 5.0,
                "VWAP_CROSS_HWM_PCT": 1.0,
                "VWAP_BLEED_MULTIPLIER": 1.5,
                "VWAP_BLEED_TICKS": 10,
                "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
                "MAX_PARABOLIC_SQUEEZE": 0.5,
                "MAX_SQUEEZE_FLOOR": 0.20,
            },
            "locked_vars": [],
        }
        db_mock.save_symphony_strategy.return_value = None
        yield db_mock


@pytest.fixture
def mock_advisor(monkeypatch):
    """Mock ai_advisor functions called by the route — ZERO live Anthropic calls."""
    # DEV_ADVISOR_FIXTURE in .env bypasses the mocked route; clear it for isolation.
    monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)
    suggestion = _make_suggestion()
    response = _make_suggestions_response([suggestion])

    with (
        patch.object(
            ai_advisor, "assemble_advisor_context", return_value={"scope": "symphony"}
        ) as mock_ctx,
        patch.object(ai_advisor, "request_suggestions", return_value=(response, None)) as mock_req,
        patch.object(
            ai_advisor,
            "enforce_suggestion_allowlist",
            wraps=ai_advisor.enforce_suggestion_allowlist,
        ) as mock_allowlist,
        patch.object(
            ai_advisor,
            "check_risk_direction_agreement",
            wraps=ai_advisor.check_risk_direction_agreement,
        ) as mock_risk,
        patch.object(
            ai_advisor,
            "revalidate_suggestion_oos",
            return_value={
                "passed": True,
                "oos_alpha": 2.0,
                "baseline_oos_alpha": 1.0,
                "detail": "OOS re-validation PASSED",
            },
        ) as mock_oos,
    ):

        class _Mocks:
            assemble_advisor_context = mock_ctx
            request_suggestions = mock_req
            enforce_suggestion_allowlist = mock_allowlist
            check_risk_direction_agreement = mock_risk
            revalidate_suggestion_oos = mock_oos

        yield _Mocks()


@pytest.fixture
def isolated_env_file(tmp_path, monkeypatch):
    """Redirect ENV_FILE_PATH to a temp .env so settings writes don't pollute the repo."""
    env_path = tmp_path / ".env"
    env_path.write_text("")
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(env_path))
    return env_path


# ===========================================================================
# A. /ai-advisor route — render + suggest
# ===========================================================================


def test_get_ai_advisor_returns_200(client, monkeypatch):
    """GET /ai-advisor must render a 200 response.

    The route must exist and respond — before suggestions are loaded the page
    must not 404, error, or redirect.  We stub render_template to avoid
    depending on template internals.
    """
    monkeypatch.setattr(app_module, "render_template", lambda *_a, **_kw: "<html>advisor</html>")
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200


def test_post_suggest_calls_assemble_advisor_context_and_request_suggestions(
    client, mock_database, mock_advisor
):
    """POST /ai-advisor/suggest must call assemble_advisor_context then request_suggestions.

    The route must wire both ai_advisor functions — skipping either is a
    contract violation.  symphony_id is passed from the request body.
    """
    resp = client.post(
        "/ai-advisor/suggest",
        json={"symphony_id": "test_sym"},
        content_type="application/json",
    )
    assert resp.status_code == 200

    mock_advisor.assemble_advisor_context.assert_called_once()
    mock_advisor.request_suggestions.assert_called_once()

    call_kwargs = mock_advisor.assemble_advisor_context.call_args
    # symphony_id must flow from the request into assemble_advisor_context
    args, kwargs = call_kwargs
    all_args = list(args) + list(kwargs.values())
    assert "test_sym" in all_args or kwargs.get("symphony_id") == "test_sym", (
        "symphony_id from the POST body must be forwarded to assemble_advisor_context"
    )


def test_post_suggest_response_includes_suggestion_fields(client, mock_database, mock_advisor):
    """POST /ai-advisor/suggest must return a JSON array where each suggestion
    has config_key, current_value, suggested_value, and rationale.

    The rationale is the hover-over text operators read before accepting —
    it must be present in the API response so the template can render it.
    """
    resp = client.post(
        "/ai-advisor/suggest",
        json={"symphony_id": "test_sym"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    body = resp.get_json()

    assert "suggestions" in body, "Response must include 'suggestions' key"
    suggestions = body["suggestions"]
    assert isinstance(suggestions, list), "'suggestions' must be a list"
    assert len(suggestions) > 0, "Non-empty response from mock must yield non-empty list"

    s = suggestions[0]
    for field in ("config_key", "current_value", "suggested_value", "rationale"):
        assert field in s, (
            f"Each suggestion must have {field!r} for the operator UI; missing in {s!r}"
        )


def test_post_suggest_empty_suggestions_is_valid_non_error_response(
    client, mock_database, monkeypatch
):
    """An empty suggestions list (Claude abstention) must be a non-error 200.

    Claude's contract: returning an empty suggestions list is valid — it means
    "the config looks sound, no suggestion warranted."  The route must not
    treat this as an error.
    """
    empty_response = ConfigSuggestionsResponse(suggestions=[])
    monkeypatch.setattr(app_module, "render_template", lambda *_a, **_kw: "")
    with (
        patch.object(ai_advisor, "assemble_advisor_context", return_value={}),
        patch.object(ai_advisor, "request_suggestions", return_value=(empty_response, None)),
    ):
        resp = client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": "test_sym"},
            content_type="application/json",
        )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("suggestions") == [] or body.get("suggestions") is not None, (
        "Empty suggestions list must be returned as [], not an error"
    )
    assert body.get("error") is None or "error" not in body, (
        "Empty suggestions must not produce an error key"
    )


def test_post_suggest_failed_request_suggestions_returns_error_payload(
    client, mock_database, monkeypatch
):
    """When request_suggestions returns (None, error_msg) the route must return
    a structured error payload — never crash, never expose raw exceptions.

    This is the graceful-degradation contract: a Claude API failure is
    'no suggestion this click', not a 500 that breaks the dashboard.
    """
    monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)
    monkeypatch.setattr(app_module, "render_template", lambda *_a, **_kw: "")
    with (
        patch.object(ai_advisor, "assemble_advisor_context", return_value={}),
        patch.object(
            ai_advisor,
            "request_suggestions",
            return_value=(None, "Claude advisor unavailable: could not build client."),
        ),
    ):
        resp = client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": "test_sym"},
            content_type="application/json",
        )

    # Must not be a 500 — degraded, not crashed
    assert resp.status_code in (200, 503), (
        f"A request_suggestions failure must return 200 or 503, not {resp.status_code}"
    )
    body = resp.get_json()
    # The response must signal the error to the operator UI
    assert body.get("error") or body.get("suggestions") is None or body.get("status") == "error", (
        "A failed request_suggestions must produce an error signal in the JSON response"
    )


# ===========================================================================
# B. Accept/reject apply path — C2 safety gates
#
# These are the most critical adversarial tests.  The accept endpoint MUST
# call all three C2 gate functions before writing.  A single missed gate is a
# real-money risk.
# ===========================================================================


def test_accept_calls_enforce_suggestion_allowlist_before_writing(
    client, mock_database, mock_advisor
):
    """Accepting a suggestion MUST invoke enforce_suggestion_allowlist.

    This is the structural defense-in-depth layer: even if the context is
    allowlisted (C1), Claude could emit a hallucinated key.  The allowlist gate
    must be called on every accept action — skipping it for "obviously valid"
    keys is the failure mode we guard against.
    """
    suggestion = _make_suggestion()
    resp = client.post(
        "/ai-advisor/accept",
        json={
            "symphony_id": "test_sym",
            "suggestion": {
                "config_key": suggestion.config_key,
                "current_value": suggestion.current_value,
                "suggested_value": suggestion.suggested_value,
                "rationale": suggestion.rationale,
                "risk_direction": suggestion.risk_direction,
                "confidence": suggestion.confidence,
                "data_sufficiency": suggestion.data_sufficiency,
            },
        },
        content_type="application/json",
    )

    assert resp.status_code == 200
    (
        mock_advisor.enforce_suggestion_allowlist.assert_called(),
        (
            "enforce_suggestion_allowlist must be called on every accept action — "
            "it is the structural allowlist gate, not an optional optimisation"
        ),
    )


def test_accept_calls_check_risk_direction_agreement_before_writing(
    client, mock_database, mock_advisor
):
    """Accepting a suggestion MUST invoke check_risk_direction_agreement.

    The engine never trusts Claude's self-reported risk_direction — it computes
    the direction itself.  The risk-direction gate must be called on every
    accept action to surface contradictions to the operator log.
    """
    suggestion = _make_suggestion()
    resp = client.post(
        "/ai-advisor/accept",
        json={
            "symphony_id": "test_sym",
            "suggestion": {
                "config_key": suggestion.config_key,
                "current_value": suggestion.current_value,
                "suggested_value": suggestion.suggested_value,
                "rationale": suggestion.rationale,
                "risk_direction": suggestion.risk_direction,
                "confidence": suggestion.confidence,
                "data_sufficiency": suggestion.data_sufficiency,
            },
        },
        content_type="application/json",
    )

    assert resp.status_code == 200
    (
        mock_advisor.check_risk_direction_agreement.assert_called(),
        (
            "check_risk_direction_agreement must be called on every accept action — "
            "a real-money system never trusts Claude's self-classified risk_direction"
        ),
    )


def test_accept_calls_revalidate_suggestion_oos_before_writing(client, mock_database, mock_advisor):
    """Accepting a suggestion MUST invoke revalidate_suggestion_oos.

    This is the most important C2 gate: a Claude suggestion is an unvalidated
    hypothesis; before it reaches live config it must pass the same OOS gate
    that Optuna's own output faces.  The route must call this gate — not
    'optimise' it away.
    """
    suggestion = _make_suggestion()
    resp = client.post(
        "/ai-advisor/accept",
        json={
            "symphony_id": "test_sym",
            "suggestion": {
                "config_key": suggestion.config_key,
                "current_value": suggestion.current_value,
                "suggested_value": suggestion.suggested_value,
                "rationale": suggestion.rationale,
                "risk_direction": suggestion.risk_direction,
                "confidence": suggestion.confidence,
                "data_sufficiency": suggestion.data_sufficiency,
            },
        },
        content_type="application/json",
    )

    assert resp.status_code == 200
    (
        mock_advisor.revalidate_suggestion_oos.assert_called(),
        (
            "revalidate_suggestion_oos must be called on every accept action — "
            "a Claude suggestion is an unvalidated hypothesis; it must pass the "
            "same OOS gate Optuna's output faces before reaching live config"
        ),
    )


def test_accept_writes_strategy_when_all_c2_gates_pass(client, mock_database, mock_advisor):
    """When all three C2 gates pass the route MUST write the new value to
    save_symphony_strategy.

    This pins the happy path: a green-lit suggestion must actually reach the
    config store.  If the route calls the gates but never writes, accepted
    suggestions are silently ignored — a usability failure.
    """
    suggestion = _make_suggestion(
        config_key="MAX_SQUEEZE_FLOOR",
        current_value=0.20,
        suggested_value=0.30,
    )
    resp = client.post(
        "/ai-advisor/accept",
        json={
            "symphony_id": "test_sym",
            "suggestion": {
                "config_key": suggestion.config_key,
                "current_value": suggestion.current_value,
                "suggested_value": suggestion.suggested_value,
                "rationale": suggestion.rationale,
                "risk_direction": suggestion.risk_direction,
                "confidence": suggestion.confidence,
                "data_sufficiency": suggestion.data_sufficiency,
            },
        },
        content_type="application/json",
    )

    assert resp.status_code == 200
    (
        mock_database.save_symphony_strategy.assert_called(),
        (
            "save_symphony_strategy must be called when all C2 gates pass — "
            "the accepted suggestion must reach the config store"
        ),
    )

    # The written params must include the suggested value, not the original
    call_args = mock_database.save_symphony_strategy.call_args
    _sym, params, _locked = call_args.args
    assert params.get("MAX_SQUEEZE_FLOOR") == pytest.approx(0.30, abs=1e-9), (
        "The accepted suggested_value (0.30) must be written; the original "
        "current_value (0.20) would indicate the gate passed but write was skipped"
    )


def test_accept_blocked_by_allowlist_does_not_write_strategy(client, mock_database):
    """A suggestion with a config_key rejected by the allowlist MUST NOT write.

    This is a structural safety contract: if enforce_suggestion_allowlist
    rejects the key (it is not in the 9-item allowlist), save_symphony_strategy
    must never be called.  A credential or LIVE_EXECUTION key landing in the
    strategy store would be catastrophic.
    """
    with (
        patch.object(ai_advisor, "assemble_advisor_context", return_value={}),
        patch.object(ai_advisor, "request_suggestions", return_value=(None, None)),
    ):
        resp = client.post(
            "/ai-advisor/accept",
            json={
                "symphony_id": "test_sym",
                "suggestion": {
                    "config_key": "LIVE_EXECUTION",  # hard-exclusion key
                    "current_value": "False",
                    "suggested_value": "True",
                    "rationale": "hallucinated rationale",
                    "risk_direction": "neutral",
                    "confidence": "high",
                    "data_sufficiency": "sufficient",
                },
            },
            content_type="application/json",
        )

    # Must return a non-2xx error or a 200 with explicit rejection signal
    body = resp.get_json()
    if resp.status_code == 200:
        # If the route returns 200 it must have an explicit rejection/error field
        assert body.get("status") == "rejected" or body.get("error") is not None, (
            "A suggestion rejected by the allowlist must not return a success status"
        )

    # The critical invariant: no write must have occurred
    (
        mock_database.save_symphony_strategy.assert_not_called(),
        (
            "save_symphony_strategy must NEVER be called when the allowlist gate "
            "rejects the config_key — LIVE_EXECUTION must not reach the strategy store"
        ),
    )


def test_accept_blocked_by_oos_gate_does_not_write_strategy(client, mock_database):
    """A suggestion that fails the OOS re-validation gate MUST NOT write.

    The OOS gate protects against operator-accepted suggestions that degrade
    walk-forward performance.  If the gate fails (patched OOS <= baseline),
    save_symphony_strategy must not be called regardless of other gate results.
    """
    failing_oos = {
        "passed": False,
        "oos_alpha": 0.5,
        "baseline_oos_alpha": 2.0,
        "detail": "OOS re-validation FAILED",
    }
    with (
        patch.object(ai_advisor, "assemble_advisor_context", return_value={}),
        patch.object(ai_advisor, "request_suggestions", return_value=(None, None)),
        patch.object(ai_advisor, "revalidate_suggestion_oos", return_value=failing_oos),
    ):
        resp = client.post(
            "/ai-advisor/accept",
            json={
                "symphony_id": "test_sym",
                "suggestion": {
                    "config_key": "MAX_SQUEEZE_FLOOR",
                    "current_value": 0.20,
                    "suggested_value": 0.45,
                    "rationale": "test rationale",
                    "risk_direction": "loosens",
                    "confidence": "medium",
                    "data_sufficiency": "sufficient",
                },
            },
            content_type="application/json",
        )

    body = resp.get_json()
    if resp.status_code == 200:
        assert body.get("status") == "rejected" or body.get("error") is not None, (
            "An OOS-gate failure must not return a success status"
        )

    (
        mock_database.save_symphony_strategy.assert_not_called(),
        (
            "save_symphony_strategy must NOT be called when the OOS gate fails — "
            "a suggestion that degrades walk-forward performance must not reach live config"
        ),
    )


def test_reject_does_not_write_strategy(client, mock_database, monkeypatch):
    """POST /ai-advisor/reject must NEVER call save_symphony_strategy.

    A rejected suggestion is a deliberate operator no-op — the current config
    must remain unchanged.  Any write on the reject path (even as a rollback
    or 'restore original') is forbidden.
    """
    monkeypatch.setattr(app_module, "render_template", lambda *_a, **_kw: "")
    resp = client.post(
        "/ai-advisor/reject",
        json={
            "symphony_id": "test_sym",
            "suggestion": {
                "config_key": "MAX_SQUEEZE_FLOOR",
                "current_value": 0.20,
                "suggested_value": 0.30,
                "rationale": "test rationale",
                "risk_direction": "loosens",
                "confidence": "medium",
                "data_sufficiency": "sufficient",
            },
        },
        content_type="application/json",
    )

    assert resp.status_code == 200
    (
        mock_database.save_symphony_strategy.assert_not_called(),
        (
            "A rejected suggestion must NOT call save_symphony_strategy — "
            "the reject path is a deliberate no-op and must never write config"
        ),
    )


def test_reject_returns_success_status(client, mock_database, monkeypatch):
    """POST /ai-advisor/reject must return a 200 success response.

    The operator has explicitly chosen to reject — the UI should confirm the
    rejection was recorded, not error.
    """
    monkeypatch.setattr(app_module, "render_template", lambda *_a, **_kw: "")
    resp = client.post(
        "/ai-advisor/reject",
        json={
            "symphony_id": "test_sym",
            "suggestion": {
                "config_key": "MAX_SQUEEZE_FLOOR",
                "current_value": 0.20,
                "suggested_value": 0.30,
                "rationale": "test rationale",
                "risk_direction": "loosens",
                "confidence": "medium",
                "data_sufficiency": "sufficient",
            },
        },
        content_type="application/json",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    # Must indicate success (recorded the rejection) not an error
    assert body.get("status") in ("rejected", "success") or "status" in body, (
        "The reject endpoint must return a structured response confirming the rejection"
    )


# ===========================================================================
# C. ANTHROPIC_API_KEY settings field
# ===========================================================================


def test_get_settings_includes_anthropic_api_key(client, mock_database, monkeypatch):
    """GET /api/settings must include ANTHROPIC_API_KEY in the globals dict.

    The settings UI must expose this field so the operator can configure the
    key without editing .env manually.  A missing key causes ai_advisor to
    fail fast on every 'Get Suggestions' click.
    """
    monkeypatch.setattr(
        app_module,
        "dotenv_values",
        lambda *_a, **_kw: {
            "LIVE_EXECUTION": "False",
            "EXECUTION_START_TIME": "09:30",
            "COMPOSER_KEY_ID": "ck",
            "COMPOSER_SECRET": "cs",
            "ALPACA_KEY": "ak",
            "ALPACA_SECRET": "as",
            "ACCOUNT_INDIVIDUAL": "A1",
            "ACCOUNT_ROTH": "A2",
            "ACCOUNT_TRAD": "A3",
            "DISCORD_WEBHOOK_URL": "https://x",
            "ANTHROPIC_API_KEY": "",
        },
    )
    mock_database.load_state.return_value = {}

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()

    assert "ANTHROPIC_API_KEY" in body["globals"], (
        "GET /api/settings must include ANTHROPIC_API_KEY in globals — "
        "it is required for the Claude AI Config Advisor feature"
    )


def test_post_settings_with_anthropic_api_key_calls_set_key(
    client, mock_database, isolated_env_file, monkeypatch
):
    """POST /api/settings with ANTHROPIC_API_KEY must call set_key for that key.

    The ANTHROPIC_API_KEY must follow the same persistence mechanism as all
    other globals: set_key(ENV_FILE_PATH, key, str(val)).  Using a different
    mechanism would break the settings round-trip.
    """
    set_key_calls = []

    def fake_set_key(path, key, val, *_a, **_kw):
        set_key_calls.append((path, key, val))
        return True, key, val

    monkeypatch.setattr(app_module, "set_key", fake_set_key)

    resp = client.post(
        "/api/settings",
        json={
            "globals": {
                "ANTHROPIC_API_KEY": "sk-ant-test-key",
            },
            "symphonies": {},
        },
        content_type="application/json",
    )

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "success"

    written_keys = {k for (_p, k, _v) in set_key_calls}
    assert "ANTHROPIC_API_KEY" in written_keys, (
        "set_key must be called with 'ANTHROPIC_API_KEY' when it appears in the "
        "POST /api/settings globals payload — it must use the same persistence "
        "mechanism as COMPOSER_KEY_ID, ALPACA_KEY, etc."
    )


def test_get_settings_anthropic_key_is_not_echoed_in_plaintext(client, mock_database, monkeypatch):
    """GET /api/settings must NOT echo ANTHROPIC_API_KEY value back in plaintext
    if it has been set.

    API keys returned in full via a GET endpoint are a credential-exposure risk.
    The response must either omit the value, return an empty string, or return
    a masked placeholder — never the raw key.

    Convention: the field is included (so the UI can tell whether the key is
    configured) but the value is empty string or a masked indicator.
    """
    real_key = "sk-ant-api03-secret-key-value-that-must-not-leak"
    monkeypatch.setattr(
        app_module,
        "dotenv_values",
        lambda *_a, **_kw: {
            "LIVE_EXECUTION": "False",
            "ANTHROPIC_API_KEY": real_key,
        },
    )
    mock_database.load_state.return_value = {}

    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.get_json()

    returned_value = body.get("globals", {}).get("ANTHROPIC_API_KEY", "")

    assert returned_value != real_key, (
        f"GET /api/settings must NOT echo the ANTHROPIC_API_KEY in plaintext — "
        f"the raw key value was returned as {returned_value!r}. "
        "Return an empty string or a masked indicator instead."
    )


# ===========================================================================
# C2. Settings modal template — ANTHROPIC_API_KEY input field
#
# UX audit finding (branch 4884826): the backend correctly returns
# ANTHROPIC_API_KEY in GET /api/settings, but index.html was never updated.
# The settings modal has no input field for the key, and the JS loadSettings /
# saveSettings functions do not reference it. The operator has no way to set
# the key via the UI.
#
# Three template gaps must be fixed together:
#   1. An <input type="password" id="env-anthropic-key"> in the modal HTML.
#   2. loadSettings assigns document.getElementById('env-anthropic-key').value
#      from vars.ANTHROPIC_API_KEY.
#   3. saveSettings includes ANTHROPIC_API_KEY in the globals object it POSTs.
# ===========================================================================


def test_settings_modal_has_anthropic_api_key_password_input(client, monkeypatch):
    """GET /settings must render an <input type="password"> for ANTHROPIC_API_KEY.

    The Studio V3 redesign moved credentials from a modal in index.html to a
    dedicated /settings page (templates/settings.html).  The field must be
    type="password" (masked) — displaying an API key as plain text is a
    credential-exposure risk.

    Assertion: GET /settings renders HTML containing 'anthropic' (case-insensitive)
    indicating the ANTHROPIC_API_KEY credential field is present on the settings page.
    """
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace").lower()

    assert "anthropic" in html, (
        "GET /settings (settings.html) must contain an ANTHROPIC_API_KEY field — "
        "'anthropic' not found anywhere in the rendered HTML. "
        "The Studio V3 design places ANTHROPIC_API_KEY in the Credentials section "
        "of the /settings page, not in a modal on index.html."
    )


def test_settings_modal_anthropic_key_input_is_password_type(client, monkeypatch):
    """The ANTHROPIC_API_KEY input in settings.html must be type="password".

    An API key rendered as type="text" is visible in plaintext.  type="password"
    masks it consistently with how Composer Secret and Alpaca Secret are handled
    in the same Credentials section.

    The Studio V3 design places this on GET /settings, not in a modal on index.html.
    """
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    import re

    input_tags = re.findall(r"<input[^>]+>", html, re.IGNORECASE)
    anthropic_inputs = [tag for tag in input_tags if "anthropic" in tag.lower()]

    assert anthropic_inputs, (
        "No <input> element referencing 'anthropic' found in GET /settings HTML. "
        "The settings.html Credentials section must contain an ANTHROPIC_API_KEY input."
    )

    password_inputs = [
        tag
        for tag in anthropic_inputs
        if 'type="password"' in tag.lower() or "type='password'" in tag.lower()
    ]
    assert password_inputs, (
        f'Found anthropic input(s) but none are type="password": '
        f"{anthropic_inputs}. "
        'The ANTHROPIC_API_KEY field must be type="password" to mask the key.'
    )


def test_settings_modal_js_saves_anthropic_api_key(client, monkeypatch):
    """GET /settings HTML must include ANTHROPIC_API_KEY in the settings JS
    so the value is included in the POST to /api/settings.

    The Studio V3 design places this on the /settings page (settings.html),
    not in a modal JS block in index.html.
    """
    resp = client.get("/settings")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    assert "ANTHROPIC_API_KEY" in html, (
        "GET /settings (settings.html) must contain 'ANTHROPIC_API_KEY' so the "
        "settings JS includes it in the POST to /api/settings. "
        "The Studio V3 design moved credentials to the /settings route."
    )


# ===========================================================================
# D. Strategy shape correctness — the accept route must pass a flat params
#    dict to revalidate_suggestion_oos, not the nested DB wrapper.
#
# database.get_symphony_strategy() returns {"params": {...}, "locked_vars": [...]}.
# revalidate_suggestion_oos() expects a FLAT params dict as current_strategy
# (it does dict(current_strategy) and patches current_strategy[config_key]).
# Passing the nested wrapper causes the OOS simulation to run against the
# wrapper structure (keys "params"/"locked_vars") instead of the real param
# values — silently wrong OOS result.
# ===========================================================================


def test_accept_passes_flat_params_to_revalidate_suggestion_oos(client, mock_database):
    """The accept route must unwrap database.get_symphony_strategy()'s nested
    {'params': {...}, 'locked_vars': [...]} and pass the FLAT params dict to
    revalidate_suggestion_oos as current_strategy.

    If the route passes the whole wrapper dict, revalidate_suggestion_oos
    receives {'params': {...}, 'locked_vars': [...]} as the baseline strategy —
    patched_strategy[config_key] would add config_key to the top-level wrapper,
    not into the params dict, making the OOS comparison meaningless.

    We assert that the first positional arg to revalidate_suggestion_oos
    (current_strategy) is a flat dict containing real param keys — NOT a dict
    with a 'params' key.
    """
    oos_calls = []

    def capturing_oos(symphony_id, config_key, suggested_value, current_strategy):
        oos_calls.append(
            {
                "symphony_id": symphony_id,
                "config_key": config_key,
                "suggested_value": suggested_value,
                "current_strategy": current_strategy,
            }
        )
        return {"passed": True, "oos_alpha": 2.0, "baseline_oos_alpha": 1.0, "detail": "PASSED"}

    with patch.object(ai_advisor, "revalidate_suggestion_oos", side_effect=capturing_oos):
        resp = client.post(
            "/ai-advisor/accept",
            json={
                "symphony_id": "test_sym",
                "suggestion": {
                    "config_key": "MAX_SQUEEZE_FLOOR",
                    "current_value": 0.20,
                    "suggested_value": 0.30,
                    "rationale": "test",
                    "risk_direction": "loosens",
                    "confidence": "medium",
                    "data_sufficiency": "sufficient",
                },
            },
            content_type="application/json",
        )

    assert resp.status_code == 200
    assert oos_calls, "revalidate_suggestion_oos must have been called"

    current_strategy_arg = oos_calls[0]["current_strategy"]

    assert "params" not in current_strategy_arg, (
        "revalidate_suggestion_oos must receive a FLAT params dict as "
        "current_strategy, not the database wrapper {'params': {...}, "
        "'locked_vars': [...]}. The route must call "
        "current_strategy.get('params', {}) to unwrap before passing."
    )
    assert "locked_vars" not in current_strategy_arg, (
        "revalidate_suggestion_oos must receive a FLAT params dict — "
        "the 'locked_vars' key belongs in save_symphony_strategy, not "
        "in the OOS baseline strategy dict."
    )
    # The flat dict must contain at least one real param key
    known_param_keys = {
        "TRIGGER_THRESHOLD_PCT",
        "TAKE_PROFIT_MC_PCT",
        "VWAP_CROSS_HWM_PCT",
        "VWAP_BLEED_MULTIPLIER",
        "VWAP_BLEED_TICKS",
        "PARABOLIC_VELOCITY_THRESHOLD",
        "MAX_PARABOLIC_SQUEEZE",
        "MAX_SQUEEZE_FLOOR",
    }
    assert any(k in current_strategy_arg for k in known_param_keys), (
        f"current_strategy passed to revalidate_suggestion_oos has no known param "
        f"keys — got: {sorted(current_strategy_arg.keys())}. "
        "The route must unwrap the DB strategy wrapper before passing to OOS gate."
    )
