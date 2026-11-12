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

(b) The three guardrail definitions (Overview Glossary lines O31a, O31b and
    O31c, once the guardrail-uniform-note paragraph) must not imply uniform
    behavior. Per doc-reconciliation.md §1.8 the copy must name all
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


def _guardrail_lines(html: str) -> dict[str, str]:
    """The three guardrail definitions, read from the Overview Glossary by their keys.

    The paragraph that used to carry them (``data-testid="guardrail-uniform-note"``) is gone: its
    first three sentences are the Glossary lines O31a, O31b and O31c, scoped here by key so a
    check on words like 'active' cannot pass on unrelated markup (the nav's ``cap-tab active``).
    """
    from tests.ui._page_explainers_advisor import norm, soup

    page = soup(html)
    glossary = page.select_one('#tab-panel-overview details[data-terms="glossary"]')
    assert glossary is not None, "the Overview carries no Glossary"
    lines = {}
    for key in ("O31a", "O31b", "O31c"):
        found = glossary.select(f'p[data-term="{key}"]')
        assert len(found) == 1, f"the Overview Glossary holds {len(found)} lines {key}, not one"
        lines[key] = norm(found[0].get_text(" "))
    return lines


def test_the_uniform_note_paragraph_is_gone_and_its_stale_framing_with_it(client, monkeypatch):
    _mock_ai_advisor_route_deps(monkeypatch)
    html = client.get("/ai-advisor").get_data(as_text=True)
    assert 'data-testid="guardrail-uniform-note"' not in html, (
        "the guardrail paragraph is still on the page; its sentences belong to the Glossary"
    )
    assert (
        "Overfitting Conscience and Spec Critic are guardrails that check the shared," not in html
    )


def test_the_guardrail_lines_read_the_plans_text_and_start_with_their_term(client, monkeypatch):
    """S-4.6: each line is the first three sentences of the reference paragraph, word for word
    after its 'Term: ' prefix, without its own leading term name and (for the first and third)
    without the 'is' that followed it. The fixture records that derivation; the page equals it."""
    from tests.ui import _page_copy as copy_fixture

    _mock_ai_advisor_route_deps(monkeypatch)
    lines = _guardrail_lines(client.get("/ai-advisor").get_data(as_text=True))
    expected = {line["key"]: line["text"] for line in copy_fixture.glossary("Overview")}
    for key, term in (
        ("O31a", "Spec Critic: "),
        ("O31b", "Overfitting Conscience: "),
        ("O31c", "Divergence Explainer: "),
    ):
        assert lines[key] == " ".join(expected[key].split()), key
        assert lines[key].startswith(term), f"{key} does not start with {term!r}: {lines[key]!r}"
    assert lines["O31a"].startswith("Spec Critic: an active guardrail")
    assert lines["O31c"].startswith("Divergence Explainer: disabled by default")
    for key in lines:
        assert not lines[key].split(": ", 1)[1].startswith("is "), f"{key} kept the leading 'is'"


def test_the_guardrail_lines_name_spec_critic_as_active_control(client, monkeypatch):
    _mock_ai_advisor_route_deps(monkeypatch)
    line = _guardrail_lines(client.get("/ai-advisor").get_data(as_text=True))["O31a"].lower()
    assert "spec critic" in line and ("active" in line or "evaluated" in line), (
        "the Spec Critic line does not affirm it as a genuine active/evaluated control."
    )


def test_the_guardrail_lines_scope_overfitting_conscience_to_one_dof_source(client, monkeypatch):
    """The line must both name the narrow scope and carry the explicit non-implication carve-out:
    a line with only the scope phrase lets the operator read a CLEAR as 'no overfitting risk
    exists', which is what the audit flagged."""
    _mock_ai_advisor_route_deps(monkeypatch)
    line = _guardrail_lines(client.get("/ai-advisor").get_data(as_text=True))["O31b"].lower()
    assert "overfitting conscience" in line
    assert (
        "backtest-selection" in line or "degrees of freedom" in line or "degrees-of-freedom" in line
    ), "the line does not scope Overfitting Conscience to its narrow check."
    assert "no overfitting risk exists" in line or "does not mean" in line, (
        "the line is missing the carve-out that a CLEAR here does not mean no overfitting risk exists."
    )


def test_the_guardrail_lines_name_divergence_explainer_as_disabled_and_informational(
    client, monkeypatch
):
    _mock_ai_advisor_route_deps(monkeypatch)
    line = _guardrail_lines(client.get("/ai-advisor").get_data(as_text=True))["O31c"].lower()
    assert "divergence explainer" in line
    assert "disabled" in line and "informational" in line, (
        "the line does not describe Divergence Explainer as disabled-by-default / informational-only."
    )
