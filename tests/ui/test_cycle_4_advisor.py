"""
Cycle 4 — AI Advisor parity RED tests.

Source: feature-plans/studio-design-handoff.md  AC-S.6
Design: .design-handoff/project/advisor.jsx + templates/ai_advisor.html
Target: templates/ai_advisor.html  vs  /ai-advisor  +  /ai-advisor/suggest
        /ai-advisor/accept  +  /ai-advisor/reject  +  /api/autotune-runs

All tests written against REQUIRED behaviour per the design spec.
Tests are intentionally RED until impl delivers the Advisor template rewrite.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

_SUGGESTIONS_STUB = [
    {
        "config_key": "VOLATILITY_FLOOR",
        "current_value": 0.02,
        "suggested_value": 0.025,
        "rationale": "Recent 20-day vol regime elevated; tightening the floor avoids premature exits.",
        "risk_direction": "tightens",
        "confidence": "high",
        "data_sufficiency": "sufficient",
        "oos_status": "passed",
        "oos_reason": None,
        "impact": {"metric": "sharpe", "delta": 0.18},
    },
    {
        "config_key": "SQUEEZE_THRESHOLD",
        "current_value": 0.08,
        "suggested_value": 0.07,
        "rationale": "Lower threshold captures momentum earlier in the swing.",
        "risk_direction": "loosens",
        "confidence": "medium",
        "data_sufficiency": "sufficient",
        "oos_status": "rejected",
        "oos_reason": "OOS alpha -0.04 below floor",
        "impact": {"metric": "sharpe", "delta": -0.05},
    },
    {
        "config_key": "PARABOLIC_RATCHET",
        "current_value": 1.5,
        "suggested_value": 1.6,
        "rationale": "Ratchet too conservative given trailing 125d vol.",
        "risk_direction": "loosens",
        "confidence": "low",
        "data_sufficiency": "insufficient",
        "oos_status": "pending",
        "oos_reason": None,
        "impact": {"metric": "sortino", "delta": 0.11},
    },
]

_AUTOTUNE_RUNS_STUB = [
    {
        "run_timestamp": "2025-05-01T10:00:00",
        "symphony_id": "my_symphony",
        "oos_alpha": 0.12,
        "train_alpha": 0.20,
        "baseline_decision": "apply",
        "deflated_sharpe": 1.23,
        "naive_sharpe": 1.45,
        "validation_sharpe": 1.30,
        "frozen_eval_sharpe": 1.10,
        "math_mode": "per_symphony",
        "account_id": "acc-abc",
        "sortino_sentinel_pct": None,
    },
    {
        "run_timestamp": "2025-04-15T09:00:00",
        "symphony_id": "alpha_sym",
        "oos_alpha": -0.05,
        "train_alpha": 0.18,
        "baseline_decision": "reject",
        "deflated_sharpe": 0.80,
        "naive_sharpe": 0.95,
        "validation_sharpe": 0.85,
        "frozen_eval_sharpe": 0.75,
        "math_mode": "per_symphony",
        "account_id": "acc-abc",
        "sortino_sentinel_pct": None,
    },
]


@contextmanager
def _patch_advisor():
    """Stack all advisor-module patches for clean test isolation."""
    # DEV_ADVISOR_FIXTURE in .env bypasses the mocked route path; clear it for tests.
    _prev_dev_fixture = os.environ.pop("DEV_ADVISOR_FIXTURE", None)
    try:
        with patch("ai_advisor.request_suggestions") as mock_suggest, \
             patch("ai_advisor.assemble_advisor_context") as mock_ctx, \
             patch("ai_advisor.enforce_suggestion_allowlist") as mock_allow, \
             patch("ai_advisor.check_risk_direction_agreement") as mock_risk, \
             patch("ai_advisor.revalidate_suggestion_oos") as mock_oos, \
             patch("database.get_all_autotune_runs", return_value=_AUTOTUNE_RUNS_STUB), \
             patch("database.get_symphony_strategy", return_value={"params": {}, "locked_vars": []}), \
             patch("database.save_symphony_strategy"):
            # Default: request_suggestions returns a stub response
            import ai_advisor as _ai
            fake_response = MagicMock()
            fake_response.suggestions = [
                _ai.ConfigSuggestion(**{k: v for k, v in s.items()
                                        if k in {"config_key", "current_value", "suggested_value",
                                                 "rationale", "risk_direction", "confidence",
                                                 "data_sufficiency"}})
                for s in _SUGGESTIONS_STUB
            ]
            mock_suggest.return_value = (fake_response, None)
            mock_ctx.return_value = {}
            mock_allow.return_value = ([MagicMock()], [])
            mock_risk.return_value = {"agreement": True}
            mock_oos.return_value = {"passed": True, "detail": ""}
            yield {
                "mock_suggest": mock_suggest,
                "mock_ctx": mock_ctx,
                "mock_allow": mock_allow,
                "mock_risk": mock_risk,
                "mock_oos": mock_oos,
            }
    finally:
        if _prev_dev_fixture is not None:
            os.environ["DEV_ADVISOR_FIXTURE"] = _prev_dev_fixture


@pytest.fixture
def adv_client():
    """Flask test client for the advisor surface."""
    import app as app_module
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ===========================================================================
# AC-C4.1 — Route basics
# ===========================================================================


def test_advisor_route_200(adv_client):
    resp = adv_client.get("/ai-advisor")
    assert resp.status_code == 200


def test_advisor_route_returns_html(adv_client):
    resp = adv_client.get("/ai-advisor")
    assert b"text/html" in resp.content_type.encode() or b"<html" in resp.data.lower()


def test_advisor_suggest_post_200(adv_client):
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "my_symphony"}),
            content_type="application/json",
        )
    assert resp.status_code == 200


def test_advisor_suggest_returns_suggestions_key(adv_client):
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "my_symphony"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    assert "suggestions" in body


def test_advisor_accept_post_200(adv_client):
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/accept",
            data=json.dumps({
                "symphony_id": "my_symphony",
                "suggestion": _SUGGESTIONS_STUB[0],
            }),
            content_type="application/json",
        )
    assert resp.status_code == 200


def test_advisor_reject_post_200(adv_client):
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/reject",
            data=json.dumps({
                "symphony_id": "my_symphony",
                "suggestion": _SUGGESTIONS_STUB[0],
            }),
            content_type="application/json",
        )
    assert resp.status_code == 200


def test_autotune_runs_route_200(adv_client):
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    assert resp.status_code == 200


def test_autotune_runs_returns_list(adv_client):
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    body = json.loads(resp.data)
    assert isinstance(body, list)


# ===========================================================================
# AC-C4.2 — Page landmark
# ===========================================================================


def test_advisor_page_landmark(adv_client):
    """Root element must carry data-testid='advisor-page' for test anchoring."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="advisor-page"' in resp.data


def test_advisor_page_has_chrome_include(adv_client):
    """Chrome top-bar must be included (data-testid='top-bar' present)."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="top-bar"' in resp.data


def test_advisor_page_has_page_header(adv_client):
    """Page header section must be present."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="advisor-header"' in resp.data


def test_advisor_page_header_contains_title(adv_client):
    """Header must contain the 'AI advisor' title text."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "ai advisor" in html


def test_advisor_page_header_contains_subtitle(adv_client):
    """Header subtitle must mention operator approval and safety gates."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "operator" in html and ("gate" in html or "approval" in html)


# ===========================================================================
# AC-C4.3 — Controls bar
# ===========================================================================


def test_advisor_controls_bar_present(adv_client):
    """Controls bar must carry data-testid='advisor-controls'."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="advisor-controls"' in resp.data


def test_advisor_symphony_select_present(adv_client):
    """Symphony selector must be a <select> with data-testid='symphony-select'."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="symphony-select"' in resp.data


def test_advisor_run_button_present(adv_client):
    """Run Claude advisor button must be present with data-testid='run-advisor-btn'."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="run-advisor-btn"' in resp.data


def test_advisor_run_button_has_label(adv_client):
    """Run button must have descriptive text mentioning advisor or Claude."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "run" in html and ("advisor" in html or "claude" in html)


# ===========================================================================
# AC-C4.4 — Summary chips strip
# ===========================================================================


def test_advisor_summary_strip_present(adv_client):
    """Summary chip strip must carry data-testid='advisor-summary-strip'."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="advisor-summary-strip"' in resp.data


def test_advisor_summary_chip_total(adv_client):
    """Summary strip must contain a chip for total suggestion count."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="chip-total-suggestions"' in resp.data


def test_advisor_summary_chip_oos_passed(adv_client):
    """Summary strip must contain a chip for OOS-passed count."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="chip-oos-passed"' in resp.data


def test_advisor_summary_chip_oos_rejected(adv_client):
    """Summary strip must contain a chip for OOS-rejected count."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="chip-oos-rejected"' in resp.data


# ===========================================================================
# AC-C4.5 — Two-column layout
# ===========================================================================


def test_advisor_two_column_layout_present(adv_client):
    """Main content area must carry data-testid='advisor-grid' for the 2-col grid."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="advisor-grid"' in resp.data


def test_advisor_suggestions_column_present(adv_client):
    """Left column (suggestions list) must carry data-testid='suggestions-column'."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="suggestions-column"' in resp.data


def test_advisor_autotune_panel_present(adv_client):
    """Right column (autotune runs panel) must carry data-testid='autotune-panel'."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="autotune-panel"' in resp.data


# ===========================================================================
# AC-C4.6 — Suggestion card anatomy (server-rendered empty state on GET)
# ===========================================================================


def test_advisor_empty_suggestions_placeholder(adv_client):
    """On initial page load (no suggestions yet), a placeholder must be rendered
    OR the suggestions-column is empty — but NOT a broken layout."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace")
    # Either a placeholder element or the column container is empty
    has_placeholder = 'data-testid="suggestions-placeholder"' in html
    has_column = 'data-testid="suggestions-column"' in html
    assert has_placeholder or has_column, (
        "Expected either a suggestions placeholder or suggestions-column container on initial load"
    )


# ---------------------------------------------------------------------------
# AC-C4.6 — Suggestion card anatomy — tested via the /ai-advisor/suggest API
# response shape (since cards are client-rendered)
# ---------------------------------------------------------------------------


def test_suggest_response_config_key(adv_client):
    """Each suggestion in the API response must include config_key."""
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "config_key" in s, f"Missing config_key in suggestion: {s}"


def test_suggest_response_current_value(adv_client):
    """Each suggestion must include current_value."""
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "current_value" in s


def test_suggest_response_suggested_value(adv_client):
    """Each suggestion must include suggested_value."""
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "suggested_value" in s


def test_suggest_response_rationale(adv_client):
    """Each suggestion must include rationale."""
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "rationale" in s


def test_suggest_response_confidence(adv_client):
    """Each suggestion must include confidence (high/medium/low)."""
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "confidence" in s
        assert s["confidence"] in ("high", "medium", "low"), (
            f"confidence must be high/medium/low, got: {s['confidence']!r}"
        )


def test_suggest_response_risk_direction(adv_client):
    """Each suggestion must include risk_direction (tightens/loosens/neutral)."""
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "risk_direction" in s
        assert s["risk_direction"] in ("tightens", "loosens", "neutral"), (
            f"risk_direction must be tightens/loosens/neutral, got: {s['risk_direction']!r}"
        )


def test_suggest_response_data_sufficiency(adv_client):
    """Each suggestion must include data_sufficiency."""
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "data_sufficiency" in s


def test_suggest_response_oos_status(adv_client):
    """Each suggestion must include oos_status (passed/rejected/pending).

    This field is NOT in the current ConfigSuggestion pydantic model — impl
    must extend the model or add it in the route serialiser.
    """
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "oos_status" in s, (
            f"oos_status missing from suggestion JSON. Current ConfigSuggestion model "
            f"does not include oos_status — impl must extend it."
        )
        assert s["oos_status"] in ("passed", "rejected", "pending"), (
            f"oos_status must be passed/rejected/pending, got: {s['oos_status']!r}"
        )


def test_suggest_response_impact(adv_client):
    """Each suggestion must include an impact object with metric and delta.

    This field is NOT in the current ConfigSuggestion pydantic model — impl
    must extend it.
    """
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "impact" in s, (
            "impact missing from suggestion JSON. Impl must add impact.metric + impact.delta."
        )
        assert "metric" in s["impact"], "impact.metric missing"
        assert "delta" in s["impact"], "impact.delta missing"
        assert isinstance(s["impact"]["delta"], (int, float)), (
            f"impact.delta must be numeric, got {type(s['impact']['delta'])}"
        )


def test_suggest_response_oos_reason_field(adv_client):
    """Each suggestion must include oos_reason (null OK for passed/pending)."""
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    for s in body.get("suggestions", []):
        assert "oos_reason" in s, "oos_reason key missing from suggestion"


# ===========================================================================
# AC-C4.7 — Autotune runs API contract (/api/autotune-runs)
# ===========================================================================


def test_autotune_runs_each_has_symphony_id(adv_client):
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    rows = json.loads(resp.data)
    for row in rows:
        assert "symphony_id" in row


def test_autotune_runs_each_has_run_timestamp(adv_client):
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    rows = json.loads(resp.data)
    for row in rows:
        assert "run_timestamp" in row


def test_autotune_runs_each_has_baseline_decision(adv_client):
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    rows = json.loads(resp.data)
    for row in rows:
        assert "baseline_decision" in row


def test_autotune_runs_each_has_naive_sharpe(adv_client):
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    rows = json.loads(resp.data)
    for row in rows:
        assert "naive_sharpe" in row


def test_autotune_runs_each_has_deflated_sharpe(adv_client):
    """deflated_sharpe (DSR) must be present in each row."""
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    rows = json.loads(resp.data)
    for row in rows:
        assert "deflated_sharpe" in row


def test_autotune_runs_each_has_frozen_eval_sharpe(adv_client):
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    rows = json.loads(resp.data)
    for row in rows:
        assert "frozen_eval_sharpe" in row


def test_autotune_runs_decision_badge_values(adv_client):
    """baseline_decision values must be apply/reject/None — not arbitrary strings."""
    with _patch_advisor():
        resp = adv_client.get("/api/autotune-runs")
    rows = json.loads(resp.data)
    valid = {"apply", "reject", None}
    for row in rows:
        assert row["baseline_decision"] in valid or row["baseline_decision"] is None, (
            f"baseline_decision must be apply/reject/null, got: {row['baseline_decision']!r}"
        )


# ===========================================================================
# AC-C4.8 — Accept / Reject API contract
# ===========================================================================


def test_accept_returns_accepted_status(adv_client):
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/accept",
            data=json.dumps({
                "symphony_id": "s1",
                "suggestion": {
                    "config_key": "VOLATILITY_FLOOR",
                    "current_value": 0.02,
                    "suggested_value": 0.025,
                    "rationale": "test",
                    "risk_direction": "tightens",
                    "confidence": "high",
                    "data_sufficiency": "sufficient",
                },
            }),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    assert body.get("status") == "accepted"


def test_reject_returns_rejected_status(adv_client):
    with _patch_advisor():
        resp = adv_client.post(
            "/ai-advisor/reject",
            data=json.dumps({"symphony_id": "s1", "suggestion": {}}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    assert body.get("status") == "rejected"


def test_accept_blocked_when_oos_fails(adv_client):
    """When OOS revalidation fails, accept must return status=rejected."""
    with _patch_advisor() as mocks:
        mocks["mock_oos"].return_value = {"passed": False, "detail": "OOS alpha -0.1 below floor"}
        resp = adv_client.post(
            "/ai-advisor/accept",
            data=json.dumps({
                "symphony_id": "s1",
                "suggestion": {
                    "config_key": "VOLATILITY_FLOOR",
                    "current_value": 0.02,
                    "suggested_value": 0.025,
                    "rationale": "test",
                    "risk_direction": "tightens",
                    "confidence": "high",
                    "data_sufficiency": "sufficient",
                },
            }),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    assert body.get("status") == "rejected"


def test_accept_blocked_when_not_in_allowlist(adv_client):
    """When allowlist rejects the key, accept must return status=rejected."""
    with _patch_advisor() as mocks:
        mocks["mock_allow"].return_value = ([], [MagicMock()])
        resp = adv_client.post(
            "/ai-advisor/accept",
            data=json.dumps({
                "symphony_id": "s1",
                "suggestion": {
                    "config_key": "FORBIDDEN_KEY",
                    "current_value": 1,
                    "suggested_value": 2,
                    "rationale": "test",
                    "risk_direction": "neutral",
                    "confidence": "low",
                    "data_sufficiency": "sufficient",
                },
            }),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    assert body.get("status") == "rejected"


def test_suggest_error_path_returns_error_key(adv_client):
    """When the advisor module returns an error message, the API must include 'error'."""
    with _patch_advisor() as mocks:
        mocks["mock_suggest"].return_value = (None, "Claude API unavailable")
        resp = adv_client.post(
            "/ai-advisor/suggest",
            data=json.dumps({"symphony_id": "s1"}),
            content_type="application/json",
        )
    body = json.loads(resp.data)
    assert "error" in body


# ===========================================================================
# AC-C4.9 — Token hygiene (HTML template)
# ===========================================================================


def test_advisor_no_tailwind_cdn(adv_client):
    """Tailwind CDN must be removed from ai_advisor.html."""
    resp = adv_client.get("/ai-advisor")
    assert b"cdn.tailwindcss.com" not in resp.data, (
        "Tailwind CDN found in advisor page — must use tokens.css instead"
    )


def test_advisor_tokens_css_linked(adv_client):
    """tokens.css must be linked in the advisor page."""
    resp = adv_client.get("/ai-advisor")
    assert b"tokens.css" in resp.data


def test_advisor_tweaks_js_linked(adv_client):
    """tweaks.js must be loaded in the advisor page."""
    resp = adv_client.get("/ai-advisor")
    assert b"tweaks.js" in resp.data


def test_advisor_ai_advisor_js_linked(adv_client):
    """ai_advisor.js must be loaded in the advisor page."""
    resp = adv_client.get("/ai-advisor")
    assert b"ai_advisor.js" in resp.data


def test_advisor_html_no_bare_hex(adv_client):
    """ai_advisor.html must contain no bare hex color literals outside CSS-var declarations.

    Acceptable: --studio-foo: #hexvalue (CSS variable declaration).
    Not acceptable: color: #cbd5e1, background: #1e293b, etc.
    """
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace")
    # Strip CSS var declaration lines (--name: #hex)
    stripped = re.sub(r"--[\w-]+\s*:\s*#[0-9a-fA-F]{3,8}\b", "", html)
    # Strip var() references
    stripped = re.sub(r"var\([^)]*\)", "", stripped)
    # Strip HTML comments
    stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.DOTALL)
    leaks = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", stripped)
    assert leaks == [], (
        f"Bare hex color literals found in ai_advisor.html: {leaks[:10]}"
    )


# ===========================================================================
# AC-C4.10 — JS token hygiene (ai_advisor.js)
# ===========================================================================


def test_ai_advisor_js_no_bare_hex():
    """ai_advisor.js must contain no bare hex color literals outside comments."""
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    content = js_path.read_text(encoding="utf-8")
    # Strip single-line comments
    stripped = re.sub(r"//[^\n]*", "", content)
    # Strip block comments
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    leaks = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", stripped)
    assert leaks == [], (
        f"Bare hex color literals found in ai_advisor.js: {leaks}"
    )


def test_ai_advisor_js_no_tailwind_classes():
    """ai_advisor.js must not inject Tailwind class names via innerHTML."""
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    content = js_path.read_text(encoding="utf-8")
    # Tailwind class patterns: bg-slate-XXX, text-slate-XXX, rounded-XXX, etc.
    tailwind_patterns = re.findall(
        r'class\s*=\s*["\'][^"\']*(?:bg-|text-|rounded-|border-|flex|grid|p-\d|px-|py-|gap-|font-)[^"\']*["\']',
        content,
    )
    assert tailwind_patterns == [], (
        f"Tailwind class strings found in ai_advisor.js: {tailwind_patterns[:5]}"
    )


# ===========================================================================
# AC-C4.11 — Structural invariants
# ===========================================================================


def test_advisor_html_has_data_theme_attribute(adv_client):
    """<html> element must carry data-theme attribute (set by tweaks.js)."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace")
    # The html tag should have data-theme (may be empty string initially, set by JS)
    assert "data-theme" in html


def test_advisor_html_no_bg_slate_body(adv_client):
    """<body> must not use Tailwind bg-slate-* class — must use var(--studio-bg)."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace")
    body_match = re.search(r"<body[^>]*>", html)
    if body_match:
        assert "bg-slate" not in body_match.group(0), (
            "body tag uses Tailwind bg-slate-* class instead of var(--studio-bg)"
        )


def test_advisor_autotune_panel_has_table(adv_client):
    """Autotune panel must contain a card-list container for runs.
    The panel migrated from <table id="autotune-runs-tbody"> to
    <div id="autotune-runs-list"> (C-16 contract update).
    """
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="autotune-panel"' in resp.data
    # new card-list contract: JS populates autotune-run-card items into this container
    assert b'id="autotune-runs-list"' in resp.data


def test_advisor_autotune_runs_tbody_present(adv_client):
    """autotune-runs-list container must be present for JS population.
    Renamed from autotune-runs-tbody when panel migrated to card layout (C-16).
    """
    resp = adv_client.get("/ai-advisor")
    assert b'id="autotune-runs-list"' in resp.data


def test_advisor_error_element_present(adv_client):
    """Advisor error display element must be present for error state rendering."""
    resp = adv_client.get("/ai-advisor")
    assert b'id="advisor-error"' in resp.data


# ===========================================================================
# AC-C4.12 — Autotune panel visual structure
# ===========================================================================


def test_advisor_autotune_panel_has_header(adv_client):
    """Autotune panel must have a visible header label."""
    resp = adv_client.get("/ai-advisor")
    assert b'data-testid="autotune-panel-header"' in resp.data


def test_advisor_autotune_panel_header_text(adv_client):
    """Autotune panel header must contain 'autotune' text."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "autotune" in html


def test_advisor_autotune_table_has_symphony_col(adv_client):
    """Autotune table header must include Symphony column."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "symphony" in html


def test_advisor_autotune_table_has_decision_col(adv_client):
    """Autotune table header must include Decision column."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "decision" in html


def test_advisor_autotune_table_has_sharpe_col(adv_client):
    """Autotune table header must include Sharpe or DSR column."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "sharpe" in html or "dsr" in html


def test_advisor_autotune_table_has_frozen_eval_col(adv_client):
    """Autotune table header must include Frozen-eval column."""
    resp = adv_client.get("/ai-advisor")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "frozen" in html or "frozen-eval" in html


# ===========================================================================
# AC-C4.13 — UX BLOCKs: card action labels, OOS-rejected treatment, data_sufficiency badge
# ===========================================================================


def test_advisor_js_apply_button_label():
    """renderSuggestions must render 'Apply suggestion' (not 'Accept') on non-rejected cards.

    BLOCK-1: The design specifies 'Apply suggestion' for the affirmative action.
    Current impl uses 'Accept' — wrong label.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    content = js_path.read_text(encoding="utf-8")
    # Strip comments
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    assert "Apply suggestion" in stripped, (
        "renderSuggestions must use 'Apply suggestion' label on the affirmative action button"
    )


def test_advisor_js_dismiss_button_label():
    """renderSuggestions must render 'Dismiss' (not 'Reject') on the ghost button.

    BLOCK-1: The design specifies 'Dismiss' for the dismissal action.
    Current impl uses 'Reject'.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    assert "Dismiss" in stripped, (
        "renderSuggestions must use 'Dismiss' label on the ghost dismissal button"
    )


def test_advisor_js_oos_rejected_blocked_label():
    """OOS-rejected cards must render 'Blocked by OOS gate' instead of 'Apply suggestion'.

    BLOCK-1: Design shows disabled apply button reading 'Blocked by OOS gate' on rejected cards.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    assert "Blocked by OOS gate" in stripped, (
        "renderSuggestions must render 'Blocked by OOS gate' label for oos_status='rejected' cards"
    )


def _extract_render_suggestions_body(js_path):
    """Extract just the renderSuggestions function body via brace counting."""
    content = js_path.read_text(encoding="utf-8")
    # Strip comments first
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    # Find the start of renderSuggestions
    start_match = re.search(r'function\s+renderSuggestions\s*\(', stripped)
    if start_match is None:
        return None
    # Find the opening brace
    brace_start = stripped.index('{', start_match.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == '{':
            depth += 1
        elif stripped[i] == '}':
            depth -= 1
            if depth == 0:
                return stripped[brace_start + 1:i]
        i += 1
    return None


def test_advisor_js_oos_rejected_apply_button_disabled():
    """Apply button on OOS-rejected cards must carry disabled attribute within renderSuggestions.

    BLOCK-1: OOS-rejected apply button must be disabled (not just visually muted).
    The disabled attribute must appear within the renderSuggestions function body,
    conditional on oos_status — not just in accept/reject handlers or getSuggestions.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    render_body = _extract_render_suggestions_body(js_path)
    assert render_body is not None, "renderSuggestions function not found in ai_advisor.js"
    assert "oos_status" in render_body and "disabled" in render_body, (
        "renderSuggestions must set disabled attribute conditionally based on oos_status "
        "— the current impl never adds disabled to the apply button inside renderSuggestions"
    )


def test_advisor_js_oos_rejected_card_opacity():
    """OOS-rejected cards must have reduced opacity set at render time inside renderSuggestions.

    BLOCK-2: Design sets opacity: 0.7 on rejected cards.
    Current impl only sets opacity transiently in accept/reject click handlers, not at render time.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    render_body = _extract_render_suggestions_body(js_path)
    assert render_body is not None, "renderSuggestions function not found in ai_advisor.js"
    assert "opacity" in render_body and "oos_status" in render_body, (
        "renderSuggestions must apply opacity conditionally based on oos_status at card-render time "
        "(not just transiently in accept/reject click handlers)"
    )


def test_advisor_js_oos_rejected_card_neg_border():
    """OOS-rejected card border must reference --studio-neg inside renderSuggestions.

    BLOCK-2: Design uses a red-tinted border for rejected cards.
    The only --studio-neg reference in the current impl is in the autotune error row (loadRecentRuns),
    not inside renderSuggestions card rendering.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    render_body = _extract_render_suggestions_body(js_path)
    assert render_body is not None, "renderSuggestions function not found"
    assert "--studio-neg" in render_body, (
        "renderSuggestions must reference --studio-neg in card border styling for rejected cards "
        "— the current --studio-neg reference is only in the autotune error row, not in card render"
    )


def test_advisor_js_data_sufficiency_badge():
    """renderSuggestions must render a data_sufficiency badge when value is not 'sufficient'.

    BLOCK-3: Design shows a warn-colored badge reading '{data_sufficiency} data' when
    data_sufficiency != 'sufficient'. Current impl has no reference to s.data_sufficiency.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    assert "data_sufficiency" in stripped, (
        "renderSuggestions must reference s.data_sufficiency to render the insufficient-data badge"
    )


def test_advisor_js_data_sufficiency_badge_text():
    """The data_sufficiency badge must include 'data' suffix text (e.g. 'marginal data').

    BLOCK-3: Badge reads '{value} data' per design spec.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    # The badge text construction must append ' data' to the sufficiency value
    assert "data_sufficiency" in stripped and " data" in stripped, (
        "renderSuggestions must render '{data_sufficiency} data' badge text for non-sufficient cases"
    )


def test_advisor_js_run_button_label():
    """Run button label must be 'Run Claude advisor', not 'Run Advisor'.

    NIT-1 (treated as test): design copy specifies 'Run Claude advisor'.
    """
    js_path = PROJECT_ROOT / "static" / "ai_advisor.js"
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    # The finally block restores the button text — must say 'Run Claude advisor'
    assert "Run Claude advisor" in stripped, (
        "getSuggestions finally block must restore button text to 'Run Claude advisor'"
    )
