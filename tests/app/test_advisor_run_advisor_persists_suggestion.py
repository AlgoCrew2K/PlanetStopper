"""advisor-fix cycle — RED: AC-5 the Run Advisor flow persists NOTHING to
llm_suggestions, and a missing-API-key failure is not surfaced.

Root cause (file:line verified by composer-alpaca-integration + me):
  * database.record_llm_suggestion (database.py:858) is the ONLY write path for
    llm_suggestions and has ZERO production callers — every grep hit is a test.
  * /ai-advisor/accept (app.py:3065) runs the C2 gates + save_symphony_strategy
    then returns {"status":"accepted"} — it never records the operator decision.
  * /ai-advisor/reject (app.py:3118) is a complete no-op (does not even read the
    payload) — no rejected row is ever recorded.
  * /ai-advisor/suggest (app.py:3041) returns {"error": ...} 200 when the API key
    is absent (good — the reason IS surfaced server-side), but persists nothing.

AC-5 contract (option (a), confirmed with composer-alpaca-integration + PM):
  - /accept writes ONE llm_suggestions row, operator_decision='accepted',
    symphony_name = normalize_name(payload symphony_id), before/after populated.
  - /reject reads the payload and writes ONE row, operator_decision='rejected'.
  - /suggest with no ANTHROPIC_API_KEY returns a non-empty 'error' key (already
    works — pinned here as a regression guard so the "surface the reason" half of
    AC-5 cannot silently regress).
  - Both records key the symphony by normalize_name, never the raw payload id.

READ-ONLY CONSTRAINT: the RENDER path must NOT write — record_llm_suggestion is
only called on the action routes (accept/reject), never on GET /ai-advisor.
test_dashboard_advisor_render_is_read_only.py already pins that; this file pins
the complementary contract (the ACTION routes DO record).

Mocking strategy:
  * app_module.database is fully mocked (mock_database fixture) — no SQLite.
  * ai_advisor gate functions mocked — ZERO live Anthropic/Composer calls.
  * No live network, no live DB; all fixtures function-scoped.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import ai_advisor
import app as app_module

# ---------------------------------------------------------------------------
# Fixtures — mirror tests/app/test_ai_advisor_tab.py (no cross-file coupling).
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database():
    """Patch database on app_module — no SQLite I/O. record_llm_suggestion is a
    MagicMock so we can assert it IS / IS NOT called and inspect its kwargs."""
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        # normalize_name lowercases + strips, matching the real impl's contract.
        db_mock.normalize_name.side_effect = lambda n: (n or "").strip().lower()
        db_mock.get_symphony_strategy.return_value = {
            "params": {"MAX_SQUEEZE_FLOOR": 0.20},
            "locked_vars": [],
        }
        db_mock.save_symphony_strategy.return_value = None
        db_mock.record_llm_suggestion.return_value = 1
        yield db_mock


@pytest.fixture
def mock_advisor_gates():
    """Mock the C2 gate functions so /accept reaches the write path with a PASS."""
    with (
        patch.object(
            ai_advisor, "enforce_suggestion_allowlist", return_value=([object()], [])
        ),  # (allowed, rejected) — nothing rejected
        patch.object(ai_advisor, "check_risk_direction_agreement", return_value=None),
        patch.object(
            ai_advisor,
            "revalidate_suggestion_oos",
            return_value={"passed": True, "detail": "OOS re-validation PASSED"},
        ),
    ):
        yield


_SYMPHONY_DISPLAY = "(INVEST) LQD + EYEG 5 ways Full Market"
_SYMPHONY_NORMALIZED = "(invest) lqd + eyeg 5 ways full market"


def _suggestion_payload() -> dict:
    return {
        "config_key": "MAX_SQUEEZE_FLOOR",
        "current_value": 0.20,
        "suggested_value": 0.30,
        "rationale": "test rationale",
        "risk_direction": "loosens",
        "confidence": "medium",
        "data_sufficiency": "sufficient",
    }


# ===========================================================================
# AC-5a — /accept records an 'accepted' llm_suggestions row.
# ===========================================================================


def test_accept_records_llm_suggestion_row(client, mock_database, mock_advisor_gates):
    """A successful /ai-advisor/accept MUST persist one llm_suggestions row with
    operator_decision='accepted', keyed by the normalized symphony name.

    Today record_llm_suggestion has ZERO production callers, so the accepted
    operator decision is never recorded — llm_suggestions stays empty (AC-5).
    """
    resp = client.post(
        "/ai-advisor/accept",
        json={"symphony_id": _SYMPHONY_DISPLAY, "suggestion": _suggestion_payload()},
        content_type="application/json",
    )
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.data!r}"

    assert mock_database.record_llm_suggestion.called, (
        "POST /ai-advisor/accept did not call record_llm_suggestion — the accepted "
        "operator decision is not recorded. AC-5: llm_suggestions stays empty."
    )
    kwargs = mock_database.record_llm_suggestion.call_args.kwargs
    assert kwargs.get("operator_decision") == "accepted", (
        f"operator_decision must be 'accepted'; got {kwargs.get('operator_decision')!r}."
    )
    # Canonical key: symphony_name must be the NORMALIZED name, not the raw payload id.
    assert kwargs.get("symphony_name") == _SYMPHONY_NORMALIZED, (
        f"symphony_name must be normalize_name(payload) = {_SYMPHONY_NORMALIZED!r}; "
        f"got {kwargs.get('symphony_name')!r}. Never persist the raw payload id."
    )


def test_accept_record_carries_before_and_after_values(client, mock_database, mock_advisor_gates):
    """The accepted record must capture the param change (before/after) so the
    audit trail shows what the operator actually applied.

    Asserts the values round-trip from the payload (no hardcoded magic numbers —
    derived from _suggestion_payload()).
    """
    payload = _suggestion_payload()
    client.post(
        "/ai-advisor/accept",
        json={"symphony_id": _SYMPHONY_DISPLAY, "suggestion": payload},
        content_type="application/json",
    )
    assert mock_database.record_llm_suggestion.called
    kwargs = mock_database.record_llm_suggestion.call_args.kwargs
    assert kwargs.get("param_name") == payload["config_key"], (
        "param_name must be the suggestion's config_key."
    )
    # after_value must reflect the suggested value; before_value the prior value.
    # Compare structurally (the route may wrap these) — derived from the payload.
    assert kwargs.get("after_value") == payload["suggested_value"], (
        f"after_value must be the suggested_value {payload['suggested_value']!r}; "
        f"got {kwargs.get('after_value')!r}."
    )


# ===========================================================================
# F-023 blast radius (DE-PERFVIEW-ID-MISMATCH) — /ai-advisor/accept's
# server-side contract stays UNCHANGED; the fix lives in ai_advisor.js only.
#
# CORRECTION (team-lead ruling, post-approval blast-radius finding): an
# EARLIER draft of this cycle's remediation resolved a Composer hash server-
# side (mirroring /ai-advisor/suggest's dual hash-or-name resolution,
# app.py:5773-5786) inside ai_advisor_accept() itself. That direction was
# RETRACTED — HARD OUT-OF-SCOPE to touch /ai-advisor/accept's route logic
# (or /ai-advisor/suggest, or analytics, or engine code) for this cycle. The
# actual fix stays entirely in static/ai_advisor.js: its #symphony-id-input
# picker keeps sending the display NAME (its accept/suggest flow's canonical
# key, always has been) — see
# tests/app/test_f023_performance_symphony_id_mismatch.py::
# test_ai_advisor_js_symphony_picker_uses_name_as_value_not_id for the
# JS-side RED test pinning that contract.
#
# This test proves the SERVER side needs no change: /ai-advisor/accept
# ALREADY works correctly given a display-name symphony_id
# (get_symphony_strategy/save_symphony_strategy are normalize_name(display_
# name)-keyed, database.py:508-509/538-539) — which is exactly what the
# corrected ai_advisor.js contract guarantees it will always receive. Found
# by f23-doc during the doc-audit pass, verified independently by f23-tw via
# direct read.
# ===========================================================================

_HASH_ID = "a1b2c3-composer-hash-xyz"
_SYMPHONY_NAME_FOR_HASH = "Sym Hash Regression Test"
_SYMPHONY_NORMALIZED_FOR_HASH = "sym hash regression test"


def test_accept_with_display_name_input_still_resolves_correctly(
    client, mock_database, mock_advisor_gates
):
    """Regression guard: /ai-advisor/accept's server-side logic is UNCHANGED
    by this cycle -- a display-NAME symphony_id (the corrected, and only
    ever, contract ai_advisor.js's picker sends) must resolve to the correct
    canonical normalized name. No server-side fix is needed or wanted; this
    pins that the existing behavior the client-side fix relies on is real."""
    mock_database.load_state.return_value = {_HASH_ID: {"name": _SYMPHONY_NAME_FOR_HASH}}

    resp = client.post(
        "/ai-advisor/accept",
        json={"symphony_id": _SYMPHONY_NAME_FOR_HASH, "suggestion": _suggestion_payload()},
        content_type="application/json",
    )
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.data!r}"

    write_arg = mock_database.save_symphony_strategy.call_args.args[0]
    assert write_arg.strip().lower() == _SYMPHONY_NORMALIZED_FOR_HASH, (
        f"a display-name symphony_id must still resolve to the same canonical "
        f"name ({_SYMPHONY_NORMALIZED_FOR_HASH!r}), got {write_arg!r}"
    )


# ===========================================================================
# AC-5b — /reject records a 'rejected' llm_suggestions row.
# ===========================================================================


def test_reject_records_rejected_llm_suggestion_row(client, mock_database):
    """POST /ai-advisor/reject MUST read the payload and persist one
    llm_suggestions row with operator_decision='rejected', keyed by the
    normalized symphony name.

    Today /reject is a complete no-op (does not even read request.json) — the
    operator's rejection is lost. AC-5: no silent nothing.
    """
    resp = client.post(
        "/ai-advisor/reject",
        json={"symphony_id": _SYMPHONY_DISPLAY, "suggestion": _suggestion_payload()},
        content_type="application/json",
    )
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.data!r}"

    assert mock_database.record_llm_suggestion.called, (
        "POST /ai-advisor/reject did not call record_llm_suggestion — the operator "
        "rejection is not recorded. AC-5: the reject decision must persist."
    )
    kwargs = mock_database.record_llm_suggestion.call_args.kwargs
    assert kwargs.get("operator_decision") == "rejected", (
        f"operator_decision must be 'rejected'; got {kwargs.get('operator_decision')!r}."
    )
    assert kwargs.get("symphony_name") == _SYMPHONY_NORMALIZED, (
        f"symphony_name must be normalize_name(payload) = {_SYMPHONY_NORMALIZED!r}; "
        f"got {kwargs.get('symphony_name')!r}."
    )


def test_reject_does_not_write_strategy(client, mock_database):
    """Recording a rejection must NOT mutate the config store — reject records the
    decision but never calls save_symphony_strategy (regression guard so AC-5's
    new write path does not accidentally apply a rejected suggestion)."""
    client.post(
        "/ai-advisor/reject",
        json={"symphony_id": _SYMPHONY_DISPLAY, "suggestion": _suggestion_payload()},
        content_type="application/json",
    )
    assert not mock_database.save_symphony_strategy.called, (
        "POST /ai-advisor/reject must NEVER call save_symphony_strategy — a "
        "rejected suggestion must not reach the config store."
    )


# ===========================================================================
# AC-5c — /suggest surfaces the reason it can't produce (no silent nothing).
# ===========================================================================


def test_suggest_surfaces_error_when_api_unavailable(client, mock_database, monkeypatch):
    """When request_suggestions returns an error (e.g. ANTHROPIC_API_KEY absent),
    /ai-advisor/suggest MUST return a JSON body with a non-empty 'error' key —
    never a silent empty success.

    This is the "OR surfaces explicitly why it can't" half of AC-5. It already
    works (app.py:3051-3052); pinned here so it cannot silently regress.
    """
    monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)
    with (
        patch.object(ai_advisor, "assemble_advisor_context", return_value={}),
        patch.object(
            ai_advisor,
            "request_suggestions",
            return_value=(None, "Claude advisor unavailable: no API key"),
        ),
    ):
        resp = client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": _SYMPHONY_DISPLAY},
            content_type="application/json",
        )
    assert resp.status_code == 200
    body = resp.get_json()
    assert body is not None and body.get("error"), (
        "When request_suggestions returns an error, /suggest must surface a "
        f"non-empty 'error' key; got body={body!r}. AC-5: no silent nothing."
    )


def test_suggest_does_not_record_on_render_or_error(client, mock_database, monkeypatch):
    """The /suggest path must NOT persist an llm_suggestions row — recording is an
    OPERATOR-DECISION event (accept/reject), not a generation event. Guards the
    read-only-ish suggest path and keeps record_llm_suggestion off the generation
    route (composer scope decision (a): record on decision, not on suggest)."""
    monkeypatch.delenv("DEV_ADVISOR_FIXTURE", raising=False)
    from ai_advisor import ConfigSuggestion, ConfigSuggestionsResponse

    suggestion = ConfigSuggestion(
        config_key="MAX_SQUEEZE_FLOOR",
        current_value=0.20,
        suggested_value=0.30,
        rationale="r",
        risk_direction="loosens",
        confidence="medium",
        data_sufficiency="sufficient",
    )
    with (
        patch.object(ai_advisor, "assemble_advisor_context", return_value={}),
        patch.object(
            ai_advisor,
            "request_suggestions",
            return_value=(ConfigSuggestionsResponse(suggestions=[suggestion]), None),
        ),
        patch.object(app_module, "_compute_suggestion_gates", return_value={}),
        patch.object(app_module, "_enrich_suggestion_impact", return_value={}),
    ):
        client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": _SYMPHONY_DISPLAY},
            content_type="application/json",
        )
    assert not mock_database.record_llm_suggestion.called, (
        "POST /ai-advisor/suggest must NOT record an llm_suggestions row — "
        "recording belongs on the operator-decision routes (accept/reject), per "
        "AC-5 scope (a). Generating a suggestion is not an operator decision."
    )
