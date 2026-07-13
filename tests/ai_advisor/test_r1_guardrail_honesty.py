"""RED tests — AC-14 (F8 revision, B.4): guardrail honesty.

Two independent sub-requirements, targeting two DIFFERENT surfaces (confirmed
by reading both before writing these tests — see the module-level notes below
each test class, this is not guesswork):

(a) Divergence Explainer stops polluting the OBSERVATIONS FEED. NOTE: the
    Overview panel's own aggregation (app.py:ai_advisor_tab, ~3907-3927)
    ALREADY excludes DIVERGENCE_EXPLAINER entirely (it is not a member of
    _ADVISOR_ROLES) and additionally filters any NOT_APPLICABLE +
    feature_flag=="off" row — so that specific surface is not a RED target;
    testing it would pass vacuously today (see the passing self-guard test
    below, which pins that this is already correct and must stay so).
    The REAL leak point is GET /api/advisor-observations?symphony_id=... ,
    which calls database.get_advisor_observations_for_symphony(...) directly
    with NO role filter and NO feature-flag suppression — a DIVERGENCE_EXPLAINER
    NOT_APPLICABLE row with raw_response.feature_flag=="off" is returned
    verbatim and unlabeled today. That is the RED target.

(b) The guardrail-uniform-note paragraph (ai_advisor.html:2204-2209,
    data-testid="guardrail-uniform-note") currently names only TWO of the
    THREE guardrail producers ("Overfitting Conscience and Spec Critic are
    guardrails that check the shared, frozen THEORY spec") and implies uniform
    behavior. Per doc-reconciliation.md §1.8 the corrected copy must name all
    three by their ACTUAL, non-uniform behavior: Spec Critic as a genuine
    active/evaluated control, Overfitting Conscience scoped to ONE narrow
    DoF source (with the "does not mean no overfitting risk exists" carve-out
    -- not general overfitting protection), and Divergence Explainer named
    explicitly as disabled-by-default / informational-only.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture()
def client():
    """Flask test client with TESTING mode — no port bound.  Mirrors the
    established pattern in tests/ai_advisor/test_rf1_prose_render.py."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _mock_ai_advisor_route_deps(monkeypatch) -> None:
    """Patch non-guardrail route dependencies so GET /ai-advisor renders
    without I/O — mirrors test_rf1_prose_render.py's _mock_route_deps."""
    import app as app_module
    import database

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: None)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)


# ===========================================================================
# (a) Divergence Explainer polluting /api/advisor-observations.
# ===========================================================================


_DE_FEATURE_OFF_ROW = {
    "id": 501,
    "advisor_role": "DIVERGENCE_EXPLAINER",
    "subject_type": "autotune_run",
    "subject_id": "42",
    "symphony_id": "test-symphony",
    "verdict": "NOT_APPLICABLE",
    "raw_response": {"feature_flag": "off"},
    "is_advisory_only": 1,
    "created_at": "2026-07-13 00:00:00",
}


def test_ac14_api_advisor_observations_suppresses_or_labels_feature_off_de_row(client, monkeypatch):
    """MUST FAIL pre-fix: GET /api/advisor-observations?symphony_id=... returns
    the DIVERGENCE_EXPLAINER feature-off row verbatim today (database.
    get_advisor_observations_for_symphony has no role filter and no
    feature-flag suppression, unlike the Overview panel's own aggregation).
    Either/or RED per AC-14 wording: the row is absent, OR some string field
    on it contains 'feature disabled' (case-insensitive)."""
    import database

    monkeypatch.setattr(
        database,
        "get_advisor_observations_for_symphony",
        lambda symphony_id: [dict(_DE_FEATURE_OFF_ROW)],
    )

    resp = client.get("/api/advisor-observations?symphony_id=test-symphony")
    assert resp.status_code == 200
    rows = resp.get_json()
    assert isinstance(rows, list)

    de_rows = [r for r in rows if r.get("advisor_role") == "DIVERGENCE_EXPLAINER"]
    if not de_rows:
        # Suppressed entirely — satisfies AC-14's first disjunct.
        return

    labeled = any(
        "feature disabled" in str(v).lower()
        for r in de_rows
        for v in r.values()
        if isinstance(v, str)
    )
    assert labeled, (
        "AC-14 GAP: /api/advisor-observations returned a DIVERGENCE_EXPLAINER "
        "feature-off row that is neither suppressed nor labeled 'feature "
        f"disabled' in any string field. Got: {de_rows}"
    )


def test_ac14_e2e_flag_off_symphony_observations_feed_has_no_divergence_explainer_rows(client):
    """NEW regression test (PM-adjudicated 2026-07-13, explicitly requested in
    addition to the mocked test above): end-to-end through the REAL database
    module (no mocking of get_advisor_observations_for_symphony or
    insert_advisor_observation — the test-isolated temp DB_PATH from
    conftest.py's autouse fixture is used) — the user-visible surface the fix
    exists for. Calls the REAL run_divergence_explainer with §B off (the
    approved fix: write nothing), then hits the REAL route and confirms zero
    DIVERGENCE_EXPLAINER rows for that symphony — proving the full stack, not
    just an isolated unit."""
    import advisors.divergence_explainer as divex

    symphony_id = "test-symphony-de-e2e"
    autotune_run = {"id": 9001, "symphony_id": symphony_id, "spec_bundle_id": "bundle-e2e-9001"}

    result = divex.run_divergence_explainer(autotune_run, None, second_window_enabled=False)
    assert result is None, f"expected no row written (§B off), got a row id: {result!r}"

    resp = client.get(f"/api/advisor-observations?symphony_id={symphony_id}")
    assert resp.status_code == 200
    rows = resp.get_json()
    de_rows = [r for r in rows if r.get("advisor_role") == "DIVERGENCE_EXPLAINER"]
    assert de_rows == [], (
        f"AC-14 REGRESSION: /api/advisor-observations for a symphony that ran "
        f"autotune with §B off must contain NO DIVERGENCE_EXPLAINER rows "
        f"end-to-end; got: {de_rows}"
    )


def test_self_guard_overview_panel_already_excludes_divergence_explainer_role(client, monkeypatch):
    """PINS existing-correct behavior (not a RED test): the Overview panel's
    own aggregation must continue to exclude DIVERGENCE_EXPLAINER from
    _ADVISOR_ROLES and continue to filter feature-off NOT_APPLICABLE rows.
    This guards against a well-intentioned AC-14 fix that adds
    DIVERGENCE_EXPLAINER to _ADVISOR_ROLES without ALSO preserving the
    feature-off suppression, which would regress the Overview panel from
    'already correct' to 'polluted'."""
    import app as app_module

    assert "DIVERGENCE_EXPLAINER" not in app_module._ADVISOR_ROLES, (
        "DIVERGENCE_EXPLAINER was added to _ADVISOR_ROLES — if this is the "
        "chosen AC-14 fix, the feature-off suppression block in "
        "ai_advisor_tab() (the NOT_APPLICABLE + feature_flag=='off' filter) "
        "MUST be preserved so feature-off rows still never render on Overview."
    )


# ===========================================================================
# (b) guardrail-uniform-note copy — all three producers named by actual behavior.
# ===========================================================================


def _extract_guardrail_note(html: str) -> str:
    """Isolate the guardrail-uniform-note paragraph's own text, not the whole
    page. Scoping matters: the page contains generic words like 'active' (e.g.
    the nav's `class="cap-tab active"`) elsewhere, so an unscoped substring
    check on 'active'/'evaluated' etc. can pass vacuously without the
    guardrail-note copy itself ever changing. Extraction is marker-based
    (testid to next closing </p>), not full HTML parsing — sufficient for this
    single well-known element and avoids a new parser dependency."""
    marker = 'data-testid="guardrail-uniform-note"'
    start = html.find(marker)
    assert start != -1, "guardrail-uniform-note element not found in rendered HTML at all"
    end = html.find("</p>", start)
    assert end != -1, "guardrail-uniform-note element has no closing </p> in rendered HTML"
    return html[start:end]


def test_ac14_guardrail_note_no_longer_implies_uniform_two_producer_behavior(client, monkeypatch):
    """MUST FAIL pre-fix: the current copy names only OC and SC and calls a
    'uniform CLEAR verdict... the healthy expected result' — the exact framing
    doc-reconciliation §1.8 corrects. Byte-absence check on the stale phrase,
    not merely presence of new text (a lazy fix could append new text without
    removing the misleading claim)."""
    _mock_ai_advisor_route_deps(monkeypatch)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert (
        "Overfitting Conscience and Spec Critic are guardrails that check the shared,"
        in html
    ) is False, (
        "AC-14 GAP: the stale two-producer 'uniform CLEAR' framing is still present "
        "in the guardrail-uniform-note copy verbatim."
    )


def test_ac14_guardrail_note_names_spec_critic_as_active_control(client, monkeypatch):
    """Scoped to the guardrail-uniform-note element itself (see
    _extract_guardrail_note) — an unscoped page-wide check on 'active' would
    pass vacuously via unrelated markup (e.g. the nav's `cap-tab active`
    class), which is exactly the trap this scoping avoids."""
    _mock_ai_advisor_route_deps(monkeypatch)
    html = client.get("/ai-advisor").get_data(as_text=True)
    note = _extract_guardrail_note(html).lower()
    assert "spec critic" in note and ("active" in note or "evaluated" in note), (
        "AC-14 GAP: guardrail-uniform-note does not affirm Spec Critic as a "
        "genuine active/evaluated control."
    )


def test_ac14_guardrail_note_scopes_overfitting_conscience_to_one_dof_source(client, monkeypatch):
    """The corrected copy must both (1) name the narrow scope and (2) carry the
    explicit non-implication carve-out — a fix that adds only the scope phrase
    without the carve-out still leaves the operator able to over-read a CLEAR
    as 'no overfitting risk exists', which is exactly what F8 flags."""
    _mock_ai_advisor_route_deps(monkeypatch)
    html = client.get("/ai-advisor").get_data(as_text=True)
    note = _extract_guardrail_note(html).lower()
    assert "overfitting conscience" in note
    assert "backtest-selection" in note or "degrees of freedom" in note or "degrees-of-freedom" in note, (
        "AC-14 GAP: guardrail-uniform-note does not scope Overfitting Conscience "
        "to its actual narrow check (BACKTEST_SELECTION degrees-of-freedom only)."
    )
    assert "no overfitting risk exists" in note or "does not mean" in note, (
        "AC-14 GAP: guardrail-uniform-note is missing the explicit carve-out that "
        "a CLEAR here does not mean no overfitting risk exists anywhere."
    )


def test_ac14_guardrail_note_names_divergence_explainer_as_disabled_and_informational(client, monkeypatch):
    """MUST FAIL pre-fix: the current copy does not mention Divergence Explainer
    at all — an operator seeing a stray DE row (via the /api/advisor-observations
    leak this file's other test targets) has zero context for it."""
    _mock_ai_advisor_route_deps(monkeypatch)
    html = client.get("/ai-advisor").get_data(as_text=True)
    note = _extract_guardrail_note(html).lower()
    assert "divergence explainer" in note, (
        "AC-14 GAP: guardrail-uniform-note does not name Divergence Explainer at all."
    )
    assert "disabled" in note and "informational" in note, (
        "AC-14 GAP: guardrail-uniform-note does not describe Divergence Explainer "
        "as disabled-by-default / informational-only."
    )
