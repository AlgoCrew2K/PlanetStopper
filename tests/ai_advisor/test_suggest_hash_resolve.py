"""
test_suggest_hash_resolve.py — RED→GREEN test for hash→name resolution in
POST /ai-advisor/suggest.

Bug: the route received a Composer hash ID (e.g. "n2ooAZTvBRN6ZzpMmWmU") but
passed it directly to assemble_advisor_context / get_latest_autotune_run, which
keys by normalized name.  Fix: resolve hash→name via bot_state before calling
assemble_advisor_context, mirroring app.py:2497-2507.

These tests are RED on the original code (hash passed as-is → autotune_run=None
→ assemble_advisor_context called with hash, not name) and GREEN after the fix.
"""

from __future__ import annotations

from unittest.mock import patch

import ai_advisor

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

HASH_ID = "n2ooAZTvBRN6ZzpMmWmU"
SYMPHONY_NAME = "(invest:crypto) we do a little trolling planet's mix v1.4 | 10.19.2022"

# Minimal bot_state blob: one real symphony (hash → name) plus noise keys.
FAKE_BOT_STATE = {
    "last_execution_mode": "dry_run",
    "date": "2026-06-09",
    HASH_ID: {"name": SYMPHONY_NAME, "armed": False},
}

# A non-empty autotune run dict (shape only — values irrelevant to this test).
FAKE_AUTOTUNE_RUN = {
    "run_timestamp": "2026-06-05T20:11:41Z",
    "train_alpha": 0.12,
    "oos_alpha": 0.09,
    "baseline_decision": "adopt",
    "fallback_oos_alpha": 0.07,
    "default_oos_alpha": 0.05,
}

# A minimal non-empty suggestions response.
FAKE_SUGGESTIONS_RESPONSE = ai_advisor.ConfigSuggestionsResponse(
    suggestions=[
        ai_advisor.ConfigSuggestion(
            config_key="VWAP_BLEED_MULTIPLIER",
            current_value=1.8,
            suggested_value=2.1,
            rationale="Test rationale.",
            risk_direction="loosens",
            confidence="medium",
            data_sufficiency="sufficient",
        )
    ]
)


# ---------------------------------------------------------------------------
# Helper — build a Flask test client with the route wired up.
# ---------------------------------------------------------------------------


def _make_client():
    """Return a Flask test client for app.py with CSRF disabled."""
    import app as flask_app  # noqa: PLC0415 — import inside helper intentional

    flask_app.app.config["TESTING"] = True
    flask_app._csrf_check_enabled = False  # disable CSRF for test POSTs
    return flask_app.app.test_client()


# ---------------------------------------------------------------------------
# Test 1 (core RED→GREEN): hash is resolved to normalized name before context
# assembly, so assemble_advisor_context sees the name, not the hash.
# ---------------------------------------------------------------------------


def test_suggest_resolves_hash_to_name_before_context_assembly():
    """
    GIVEN  a bot_state that maps HASH_ID → SYMPHONY_NAME
    AND    get_latest_autotune_run returns a run for SYMPHONY_NAME (normalized)
    WHEN   POST /ai-advisor/suggest is called with symphony_id=HASH_ID
    THEN   assemble_advisor_context is called with the resolved normalized name,
           NOT with the original hash.
    """
    client = _make_client()

    # Stub assemble_advisor_context to return a minimal valid context dict.
    # We capture the call args to assert the resolved name was passed.
    fake_context = {
        "scope": "symphony",
        "symphony_id": SYMPHONY_NAME.lower(),
        "role_framing": "",
        "suggestible_surface": [],
        "locked_vars": [],
        "optuna_evidence": {"available": True, "train_alpha": 0.12, "oos_alpha": 0.09},
        "volatility_regime": {"available": True},
        "data_window": {},
        "risk_invariants": [],
        "symphony_logic": None,
    }

    with (
        patch("database.load_state", return_value=FAKE_BOT_STATE),
        patch("ai_advisor.assemble_advisor_context", return_value=fake_context) as mock_ctx,
        patch("ai_advisor.request_suggestions", return_value=(FAKE_SUGGESTIONS_RESPONSE, None)),
        patch("database.get_latest_autotune_run", return_value=FAKE_AUTOTUNE_RUN),
    ):
        resp = client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": HASH_ID},
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "suggestions" in data, f"Unexpected response: {data}"
    assert len(data["suggestions"]) == 1, (
        "Expected 1 suggestion — if 0, the resolved name is not being passed and "
        "the autotune run is invisible (hash→name resolution not applied)."
    )

    # Verify assemble_advisor_context was called with the resolved normalized name.
    mock_ctx.assert_called_once()
    call_args = mock_ctx.call_args
    # call_args is (args, kwargs); symphony_id may be positional or keyword.
    called_symphony_id = (
        call_args.kwargs.get("symphony_id")
        if call_args.kwargs.get("symphony_id") is not None
        else (call_args.args[1] if len(call_args.args) > 1 else None)
    )
    assert called_symphony_id == SYMPHONY_NAME.lower(), (
        f"assemble_advisor_context called with symphony_id={called_symphony_id!r}; "
        f"expected resolved name {SYMPHONY_NAME.lower()!r}. "
        "Hash→name resolution is missing or broken."
    )


# ---------------------------------------------------------------------------
# Test 2: unknown hash falls back gracefully — no exception, route returns
# a valid (possibly empty) suggestions JSON, not a 500.
# ---------------------------------------------------------------------------


def test_suggest_unknown_hash_falls_back_gracefully():
    """
    GIVEN  a bot_state that does NOT contain the supplied hash
    WHEN   POST /ai-advisor/suggest is called with that unknown hash
    THEN   the route returns HTTP 200 with a "suggestions" key (may be empty),
           and does NOT raise / return an error key due to the fallback.
    """
    client = _make_client()

    unknown_hash = "ZZZunknownhashZZZ"

    with (
        patch("database.load_state", return_value=FAKE_BOT_STATE),
        patch(
            "ai_advisor.assemble_advisor_context",
            return_value={
                "scope": "symphony",
                "symphony_id": unknown_hash,
                "role_framing": "",
                "suggestible_surface": [],
                "locked_vars": [],
                "optuna_evidence": {"available": False},
                "volatility_regime": {"available": False},
                "data_window": {},
                "risk_invariants": [],
                "symphony_logic": None,
            },
        ),
        patch(
            "ai_advisor.request_suggestions",
            return_value=(ai_advisor.ConfigSuggestionsResponse(suggestions=[]), None),
        ),
    ):
        resp = client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": unknown_hash},
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "suggestions" in data, f"Expected 'suggestions' key, got: {data}"
    assert "error" not in data, f"Unexpected error key in response: {data}"


# ---------------------------------------------------------------------------
# Test 3: empty symphony_id string falls back gracefully (no KeyError/crash).
# ---------------------------------------------------------------------------


def test_suggest_empty_symphony_id_does_not_crash():
    """Empty string symphony_id → graceful degradation, no 500."""
    client = _make_client()

    with (
        patch("database.load_state", return_value={}),
        patch(
            "ai_advisor.assemble_advisor_context",
            return_value={
                "scope": "symphony",
                "symphony_id": "",
                "role_framing": "",
                "suggestible_surface": [],
                "locked_vars": [],
                "optuna_evidence": {"available": False},
                "volatility_regime": {"available": False},
                "data_window": {},
                "risk_invariants": [],
                "symphony_logic": None,
            },
        ),
        patch(
            "ai_advisor.request_suggestions",
            return_value=(ai_advisor.ConfigSuggestionsResponse(suggestions=[]), None),
        ),
    ):
        resp = client.post(
            "/ai-advisor/suggest",
            json={"symphony_id": ""},
            content_type="application/json",
        )

    assert resp.status_code == 200
    data = resp.get_json()
    assert "suggestions" in data
