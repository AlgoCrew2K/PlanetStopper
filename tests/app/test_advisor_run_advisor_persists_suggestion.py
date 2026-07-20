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
# F-023 blast radius (DE-PERFVIEW-ID-MISMATCH) — /ai-advisor/accept must
# resolve a Composer-hash symphony_id server-side. FINAL direction (this
# reversed twice -- see tests/app/test_f023_performance_symphony_id_mismatch.py
# "AC-5" section comment + .claude/tdd-handoff.md "BLOCKING finding" for the
# full trail; this is team-lead's final ruling):
#
# static/ai_advisor.js's #symphony-id-input picker sends the Composer HASH
# (matching performance.js and /ai-advisor/suggest, which already dual-
# resolves hash-or-name at app.py:5773-5786). /ai-advisor/accept must mirror
# that SAME resolution logic before touching symphony_strategies --
# get_symphony_strategy/save_symphony_strategy are normalize_name(display_
# name)-keyed ONLY (database.py:508-509/538-539) and have no hash awareness
# of their own. team-lead's hard guardrails for the fix:
#   1. Faithful mirror only -- reuse /ai-advisor/suggest's existing hash-or-
#      name resolution logic, no new/parallel resolution scheme.
#   2. DB stays keyed by the canonical normalized name after resolution --
#      the persisted symphony_strategies row must be the REAL name-keyed
#      row, NEVER a phantom (see test #2 below -- this is stricter than a
#      pure faithful-mirror would give you, see that test's docstring).
#   3. No trade/engine/LIVE_EXECUTION behavior change; accept stays
#      advisory config-write only; no analytics-query change.
#   4. Both directions tested: hash-valued accept resolves + persists
#      correctly (test #1); name-valued accept still works (test #3,
#      unaffected either way since it was never broken).
#   5. f23-rev re-reviews this specific server change before it clears.
#
# Found by f23-doc during the doc-audit pass, verified independently by
# f23-tw via direct read.
# ===========================================================================

_HASH_ID = "a1b2c3-composer-hash-xyz"
_SYMPHONY_NAME_FOR_HASH = "Sym Hash Regression Test"
_SYMPHONY_NORMALIZED_FOR_HASH = "sym hash regression test"


def test_accept_resolves_composer_hash_to_canonical_name_before_strategy_write(
    client, mock_database, mock_advisor_gates
):
    """Test #1 (guardrails 1, 2, 4-hash-direction): a hash-valued symphony_id
    that DOES match a known bot_state entry must resolve to the canonical
    normalized name BEFORE reading/writing symphony_strategies -- never reach
    get_symphony_strategy/save_symphony_strategy as the raw hash. This also
    indirectly proves Gate 3's OOS-revalidation baseline (flat_params/
    locked_vars, read from the SAME get_symphony_strategy call) is sourced
    from the resolved name too -- f23-rev flagged this as worth confirming;
    a wrong read argument here would mean Gate 3 validates against the wrong
    (phantom-default) baseline."""
    mock_database.load_state.return_value = {_HASH_ID: {"name": _SYMPHONY_NAME_FOR_HASH}}

    resp = client.post(
        "/ai-advisor/accept",
        json={"symphony_id": _HASH_ID, "suggestion": _suggestion_payload()},
        content_type="application/json",
    )
    assert resp.status_code == 200, f"got {resp.status_code}: {resp.data!r}"
    assert resp.get_json().get("status") == "accepted", (
        "a resolvable hash must be a real accept, not a rejection"
    )

    assert mock_database.get_symphony_strategy.called, "accept must read the current strategy row"
    read_arg = mock_database.get_symphony_strategy.call_args.args[0]
    assert read_arg.strip().lower() == _SYMPHONY_NORMALIZED_FOR_HASH, (
        f"get_symphony_strategy must be called with a value that resolves to the "
        f"CANONICAL symphony name ({_SYMPHONY_NORMALIZED_FOR_HASH!r}), not the raw "
        f"Composer hash ({read_arg!r}) — otherwise it silently reads the wrong "
        "(empty-default) strategy row, AND Gate 3's OOS baseline is wrong too."
    )

    assert mock_database.save_symphony_strategy.called, "accept must persist the config write"
    write_arg = mock_database.save_symphony_strategy.call_args.args[0]
    assert write_arg.strip().lower() == _SYMPHONY_NORMALIZED_FOR_HASH, (
        f"save_symphony_strategy must be called with a value that resolves to the "
        f"CANONICAL symphony name ({_SYMPHONY_NORMALIZED_FOR_HASH!r}), not the raw "
        f"Composer hash ({write_arg!r}) — otherwise it writes a PHANTOM row the "
        "live exec engine never reads, and the accepted change silently never "
        "takes effect."
    )


def test_accept_with_unresolvable_symphony_id_does_not_write_phantom_row_or_report_success(
    client, mock_database, mock_advisor_gates
):
    """Test #2 (guardrail 2 -- 'never a phantom', the misleading-success
    concern team-lead asked to be confirmed): /ai-advisor/suggest's OWN
    resolution loop falls through to the RAW (unresolved) symphony_id when
    no bot_state match exists (app.py:5777, 'fallback: pass as-is if no
    match found') -- a faithful, literal mirror of JUST that loop would
    inherit the same silent fallback here. For a READ-only route (suggest)
    that's low-risk; for a WRITE route (accept) it would write a phantom
    symphony_strategies row under the still-unresolved raw value AND still
    report {"status": "accepted"} -- the exact misleading-success failure
    mode guardrail 2 ('never a phantom') forbids. This is the one guardrail
    that overrides 'faithful mirror only' (guardrail 1) for this specific
    branch: the resolution LOOP itself must be a faithful mirror, but an
    UNRESOLVED result must reject (mirroring this route's OWN existing
    gate-rejection pattern, e.g. Gate 1/3/4 -> {"status": "rejected", ...}),
    never fall through to a phantom write."""
    mock_database.load_state.return_value = {_HASH_ID: {"name": _SYMPHONY_NAME_FOR_HASH}}

    resp = client.post(
        "/ai-advisor/accept",
        json={"symphony_id": "totally-unresolvable-id", "suggestion": _suggestion_payload()},
        content_type="application/json",
    )
    body = resp.get_json()
    assert body.get("status") != "accepted", (
        f"an unresolvable symphony_id must NOT report success — got {body!r}. "
        "A silent fallback to the raw (unresolved) value would write a phantom "
        "symphony_strategies row while still claiming success."
    )
    assert not mock_database.save_symphony_strategy.called, (
        "an unresolvable symphony_id must never reach save_symphony_strategy — "
        "no phantom row, ever."
    )


def test_accept_with_display_name_input_still_resolves_correctly(
    client, mock_database, mock_advisor_gates
):
    """Test #3 (guardrail 4 -- name direction): a display-NAME symphony_id
    (a valid input regardless of this fix — e.g. a stale bookmark, or any
    other future consumer) must continue to resolve to the same canonical
    normalized name it always has. This path was never broken; the
    hash-resolution fix must not break it either."""
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
