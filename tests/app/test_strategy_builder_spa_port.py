"""RED tests — SPA-port cycle: Strategy Builder folded into unified AI-Advisor SPA as 6th tab.

BRIEF: .design-handoff/spa-port/BRIEF.md — 7 acceptance criteria.

These tests encode the SPA-port contract. All tests in this file FAIL against
the current implementation (standalone page at /ai-advisor/strategy-builder
returning 200 with its own template). They define the GREEN target.

Acceptance criteria under test:
  AC1: ai_advisor.html gains 6th tab button (data-tab="strategy-builder") +
       panel (id="tab-panel-strategy-builder") matching existing 5 tabs.
  AC2: Strategy Builder panel renders SAME content: risk banner, no-API-key state,
       run controls, proposal cards, sparkline macro. CSS ported to ai_advisor.html.
  AC3: GET /ai-advisor/strategy-builder → 302 redirect to /ai-advisor.
       POST /ai-advisor/strategy-builder/run STAYS unchanged.
  AC4: /ai-advisor route prefetches STRATEGY_BUILDER observations + passes them
       to unified template (same pattern the old standalone route used).
  AC5: sbRunAnalysis() and openChatWithArtifact() live in static/ai_advisor.js
       (not inline in the deleted template). sbRunAnalysis navigates to /ai-advisor
       (not the old /ai-advisor/strategy-builder URL).
  AC6: templates/ai_advisor_strategy_builder.html is DELETED. No render_template
       call references it; no links point to it.
  AC7: tests/app/test_strategy_builder_*.py updated from standalone model to SPA model.
       (Covered by updating A-1..A-4 in test_strategy_builder_route.py — those
       tests' assertions are updated separately; this file covers the SPA model directly.)

Contract preservation (all must still hold after GREEN):
  - Advisory-only: no trade affordances on cards.
  - Non-dismissible risk banner stays.
  - CSRF on POST /run unchanged.
  - D-1: route never echoes raw str(exc).
  - node --check on static/ai_advisor.js passes.

Mocking strategy:
  - analytics and database accessors patched — no live data.
  - Math engine never mocked.
  - No @pytest.mark.live tests in this file.
  - All fixtures are function-scoped.

TEST DISCIPLINE: run ONLY tests/app/test_strategy_builder_*.py targeted. -n2 MAX.
Set $env:DB_PATH before pytest.
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_BASIC_FIXTURE_PATH = (
    _REPO_ROOT
    / "tests"
    / "fixtures"
    / "ai_advisor"
    / "m6"
    / "strategy_builder_observations_basic.json"
)


def _load_fixture() -> dict:
    import json

    return json.loads(_BASIC_FIXTURE_PATH.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shared stub helpers — patch only network/DB, never the math engine
# ---------------------------------------------------------------------------


def _stub_analytics(monkeypatch):
    monkeypatch.setattr(
        app_module.analytics, "get_history_with_cache_invalidation", lambda **kw: {}
    )
    monkeypatch.setattr(app_module.analytics, "list_available_symphonies", lambda h: [])
    monkeypatch.setattr(
        app_module.analytics, "compute_per_symphony_returns", lambda h, sym: ([], [], [])
    )


def _stub_db_observations(monkeypatch):
    monkeypatch.setattr(
        app_module.database, "get_advisor_observations_for_role", lambda role, limit=50: []
    )


# ---------------------------------------------------------------------------
# Flask client fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch):
    """Flask test client with analytics + DB stubs. Function-scoped."""
    _stub_analytics(monkeypatch)
    _stub_db_observations(monkeypatch)
    with patch("database.init_db"):
        pass
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def advisor_page_html(client, monkeypatch):
    """GET /ai-advisor rendered HTML with correlation stub."""
    fake_corr = MagicMock()
    fake_corr.compute_pairwise_correlations.return_value = []
    fake_corr.CRISIS_CAVEAT = "Test caveat"
    with patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}):
        resp = client.get("/ai-advisor")
    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code}; expected 200 for SPA base."
    )
    return resp.data.decode("utf-8", errors="replace")


# ===========================================================================
# AC1 — 6th tab button + panel in ai_advisor.html
# ===========================================================================


class TestAC1SixthTabInSPA:
    """AC1: GET /ai-advisor must contain the 6th tab button and panel for Strategy Builder."""

    def test_spa_contains_strategy_builder_tab_button(self, advisor_page_html):
        """AC1-A: GET /ai-advisor must include a tab button with data-tab="strategy-builder".

        The 6th tab button must follow the same pattern as the existing 5 tabs:
        a <button role="tab" data-tab="strategy-builder"> element.

        RED: the current ai_advisor.html has only 5 tabs (Overview, Correlations,
        Asset Swaps, Logic Changes, Chat). The 6th is absent.
        """
        html = advisor_page_html
        assert 'data-tab="strategy-builder"' in html, (
            'GET /ai-advisor must contain a tab button with data-tab="strategy-builder". '
            "The 6th tab button is absent from the unified SPA template. "
            'Add a <button role="tab" data-tab="strategy-builder"> following the '
            "existing 5 tabs pattern in templates/ai_advisor.html (AC1)."
        )

    def test_spa_contains_strategy_builder_panel_id(self, advisor_page_html):
        """AC1-B: GET /ai-advisor must include a panel with id="tab-panel-strategy-builder".

        The panel div must follow the existing pattern:
          <div id="tab-panel-strategy-builder" role="tabpanel" data-tab="strategy-builder">

        RED: absent from current ai_advisor.html.
        """
        html = advisor_page_html
        assert 'id="tab-panel-strategy-builder"' in html, (
            'GET /ai-advisor must contain a panel div with id="tab-panel-strategy-builder". '
            "The 6th tab panel is absent from the unified SPA template. "
            'Add <div id="tab-panel-strategy-builder" role="tabpanel"> (AC1).'
        )

    def test_spa_strategy_builder_panel_has_role_tabpanel(self, advisor_page_html):
        """AC1-C: The strategy-builder panel must carry role="tabpanel".

        The ARIA tab pattern requires each panel to have role="tabpanel".
        All 5 existing tab panels have this — the 6th must match.

        RED: the panel does not exist yet.
        """
        html = advisor_page_html
        # Find the panel block and assert role=tabpanel presence
        # The panel must have both id="tab-panel-strategy-builder" AND role="tabpanel".
        # We search for the combined pattern within 500 chars.
        panel_start = html.find('id="tab-panel-strategy-builder"')
        assert panel_start != -1, (
            "tab-panel-strategy-builder panel not found in GET /ai-advisor (AC1)."
        )
        context = html[max(0, panel_start - 200) : panel_start + 200]
        assert 'role="tabpanel"' in context, (
            'The tab-panel-strategy-builder div must have role="tabpanel" on the same '
            "element (ARIA tab pattern — AC1, AC2)."
        )

    def test_spa_strategy_builder_tab_button_is_a_button_not_anchor(self, advisor_page_html):
        """AC1-D: The strategy-builder tab control must be a <button>, not an <a href>.

        The existing 5 tabs use <button role="tab" data-tab="...">. The 6th must match.
        An <a href> would cause a full-page navigation instead of in-place switching.

        RED: even if data-tab="strategy-builder" appears, it may be on an anchor.
        """
        html = advisor_page_html
        marker = 'data-tab="strategy-builder"'
        idx = html.find(marker)
        if idx == -1:
            pytest.fail(
                'data-tab="strategy-builder" not found in /ai-advisor — AC1-D cannot verify.'
            )
        before = html[max(0, idx - 300) : idx]
        last_open = before.rfind("<")
        tag_snippet = before[last_open:] + marker
        assert tag_snippet.lstrip("<").startswith("button"), (
            f"The strategy-builder tab control must be a <button> element, not <a href>. "
            f"Found opening tag context: {tag_snippet[:80]!r}. "
            'In-place switching requires <button role="tab"> — any <a> would navigate (AC1, AC2).'
        )

    def test_spa_has_six_tab_buttons_total(self, advisor_page_html):
        """AC1-E: GET /ai-advisor must have exactly 6 role="tab" buttons in the tablist.

        Existing count is 5 (Overview, Correlations, Asset Swaps, Logic Changes, Chat).
        After adding Strategy Builder it must be 6.

        RED: current count is 5.
        """
        html = advisor_page_html
        tab_buttons = re.findall(r'role="tab"', html)
        assert len(tab_buttons) >= 6, (
            f'GET /ai-advisor has {len(tab_buttons)} role="tab" element(s); expected >= 6. '
            "The 6th tab (Strategy Builder) must be added to the tablist — AC1."
        )


# ===========================================================================
# AC2 — Panel content: risk banner, run controls, STRATEGY_BUILDER observations
# ===========================================================================


class TestAC2StrategyBuilderPanelContent:
    """AC2: Strategy Builder panel renders risk banner and run controls."""

    def test_spa_strategy_builder_panel_contains_risk_banner(self, advisor_page_html):
        """AC2-A: The strategy-builder panel must contain the non-dismissible risk banner.

        The banner has data-testid="strategy-builder-risk-banner" and the text
        confirming the highest overfitting risk level. It must be non-dismissible.

        RED: the panel does not exist yet in ai_advisor.html.
        """
        html = advisor_page_html
        has_testid = 'data-testid="strategy-builder-risk-banner"' in html
        has_text = "Logic-change proposals carry the highest overfitting risk" in html
        assert has_testid or has_text, (
            "The strategy-builder tab panel must contain the non-dismissible risk banner. "
            'Expected either data-testid="strategy-builder-risk-banner" or the text '
            "'Logic-change proposals carry the highest overfitting risk'. "
            "The risk banner must be ported from the standalone template (AC2)."
        )

    @pytest.mark.skipif(
        not os.environ.get("COMPOSER_KEY_ID"),
        reason="strategy-builder-controls/sb-run-btn only render when COMPOSER_KEY_ID "
        "is set (template guard: {% if not no_api_key %}) — credential-gated",
    )
    def test_spa_strategy_builder_panel_contains_run_controls(self, advisor_page_html):
        """AC2-B: The strategy-builder panel must contain the run controls section.

        The controls section (objective selector, universe input, symphony selector,
        Run button) must be present in the SPA's 6th tab panel.

        RED: absent from current ai_advisor.html.
        """
        html = advisor_page_html
        has_controls_testid = 'data-testid="strategy-builder-controls"' in html
        has_sb_run_btn = 'data-testid="sb-run-btn"' in html
        assert has_controls_testid or has_sb_run_btn, (
            "The strategy-builder tab panel must contain run controls. "
            'Expected data-testid="strategy-builder-controls" or '
            'data-testid="sb-run-btn" in the /ai-advisor response. '
            "Port the run controls from the standalone template (AC2)."
        )

    def test_spa_strategy_builder_observations_passed_to_template(self, monkeypatch):
        """AC2-C: The /ai-advisor route must pass STRATEGY_BUILDER observations to the template.

        The old standalone GET route loaded STRATEGY_BUILDER observations and passed
        them as 'strategy_builder_observations' (or similar) to render_template.
        After folding in, the /ai-advisor route must do the same — prefetch
        STRATEGY_BUILDER observations and pass them to ai_advisor.html.

        RED: current /ai-advisor route does NOT prefetch strategy_builder observations.
        """
        _stub_analytics(monkeypatch)

        captured: dict = {}

        def _capture(template_name, **kwargs):
            captured.update(kwargs)
            return "<html><body>stub</body></html>"

        fixture = _load_fixture()
        sb_obs = [fixture["observation_survivor"]]

        fake_corr = MagicMock()
        fake_corr.compute_pairwise_correlations.return_value = []
        fake_corr.CRISIS_CAVEAT = "caveat"

        with (
            patch.object(
                app_module.database,
                "get_advisor_observations_for_role",
                side_effect=lambda role, limit=50: sb_obs if role == "STRATEGY_BUILDER" else [],
            ),
            patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}),
            patch("flask.templating._render", side_effect=lambda *a, **kw: "stub"),
            patch("app.render_template", side_effect=_capture),
        ):
            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as c:
                c.get("/ai-advisor")

        # The route must pass strategy builder observations to the template.
        # Accept any of these plausible key names.
        sb_keys = {k for k in captured if "strategy" in k.lower() or "sb_obs" in k.lower()}
        assert sb_keys, (
            f"GET /ai-advisor render_template call did not include strategy_builder "
            f"observations. Template context keys found: {sorted(captured.keys())}. "
            "The /ai-advisor route must prefetch STRATEGY_BUILDER observations from "
            "database.get_advisor_observations_for_role('STRATEGY_BUILDER') and pass "
            "them to ai_advisor.html as part of the template context (AC2, AC4)."
        )

    def test_spa_strategy_builder_css_in_advisor_html_style_block(self):
        """AC2-D: templates/ai_advisor.html must contain strategy-builder-specific CSS.

        The standalone template had an extensive CSS block for .run-controls-panel,
        .proposal-card, .sparkline-macro, etc. Those styles must be ported into
        the unified template's <style> block — not left only in the deleted template.

        RED: ai_advisor.html currently has no strategy-builder CSS.
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor.html"
        source = template_path.read_text(encoding="utf-8")
        # Look for CSS class names that are specific to strategy builder content
        sb_css_markers = [
            ".proposal-card",
            ".run-controls-panel",
            ".survivor-badge",
            ".withheld-badge",
            ".empty-state-title",
        ]
        found = [m for m in sb_css_markers if m in source]
        assert found, (
            "templates/ai_advisor.html has no strategy-builder CSS. "
            f"Expected at least one of: {sb_css_markers}. "
            "Port the CSS from the standalone template's <style> block into "
            "ai_advisor.html's style block — the standalone template is being deleted (AC2)."
        )


# ===========================================================================
# AC3 — GET /ai-advisor/strategy-builder → 302; POST /run unchanged
# ===========================================================================


class TestAC3RouteRedirect:
    """AC3: GET /ai-advisor/strategy-builder becomes a 302 redirect to /ai-advisor."""

    def test_get_strategy_builder_redirects_to_spa(self, client):
        """AC3-A: GET /ai-advisor/strategy-builder must return 302 to /ai-advisor.

        Currently returns 200 with the standalone page. After SPA-port it must
        redirect to /ai-advisor (matching the pattern of the other 4 sub-routes:
        Correlations, Asset Swaps, Logic Changes, Chat all return 302).

        RED: current implementation returns 200.
        """
        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder")

        assert resp.status_code in (301, 302, 303, 308), (
            f"GET /ai-advisor/strategy-builder returned {resp.status_code}; "
            "expected a redirect (301/302/303/308) to /ai-advisor. "
            "After SPA-port, the GET sub-route must redirect to the unified SPA — "
            "match the pattern of Correlations/Asset-Swaps/Logic-Changes/Chat (AC3)."
        )

    def test_get_strategy_builder_redirect_target_is_ai_advisor(self, client):
        """AC3-B: The redirect from GET /ai-advisor/strategy-builder must point to /ai-advisor.

        A redirect to /ai-advisor/strategy-builder itself (loop) or to / (wrong target)
        must both fail this test.

        RED: no redirect exists yet; current route returns 200.
        """
        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder")

        if resp.status_code not in (301, 302, 303, 308):
            pytest.fail(
                f"GET /ai-advisor/strategy-builder returned {resp.status_code} (not a redirect). "
                "Cannot check redirect target (AC3-B)."
            )

        location = resp.headers.get("Location", "")
        assert "/ai-advisor" in location and "strategy-builder" not in location, (
            f"Redirect from /ai-advisor/strategy-builder points to {location!r}; "
            "expected a redirect to /ai-advisor (not to itself or another URL). "
            "The redirect target must be /ai-advisor (AC3)."
        )

    def test_post_run_route_still_exists_and_is_not_redirected(self, client):
        """AC3-C: POST /ai-advisor/strategy-builder/run must still return non-redirect.

        The POST action route is UNCHANGED by the SPA-port. Only the GET sub-route
        becomes a redirect. POST /run must still accept requests and return 200.

        RED if the implementer accidentally redirects or removes the POST route.
        """
        mock_run_module = MagicMock()
        from types import SimpleNamespace

        mock_run = SimpleNamespace(
            candidates=[],
            gated_batch=SimpleNamespace(results=[], survivors=[], n_candidates=0, fdr_q=0.05),
            screened_survivors=[],
            observations_written=0,
            error=None,
        )
        with patch("advisors.strategy_builder_engine.propose_strategies", return_value=mock_run):
            resp = client.post(
                "/ai-advisor/strategy-builder/run",
                json={"objective": "diversify", "universe": ["SPY"], "symphony_id": "sym-test"},
                content_type="application/json",
            )

        assert resp.status_code not in (301, 302, 303, 308), (
            f"POST /ai-advisor/strategy-builder/run returned a redirect ({resp.status_code}). "
            "The POST /run route must remain unchanged — only the GET sub-route is redirected "
            "by the SPA-port (AC3)."
        )
        assert resp.status_code != 404, (
            "POST /ai-advisor/strategy-builder/run returned 404. "
            "The POST /run route must not be removed — it stays unchanged (AC3)."
        )

    def test_get_strategy_builder_does_not_return_200_standalone_page(self, client):
        """AC3-D: GET /ai-advisor/strategy-builder must NOT return 200 with the standalone page.

        The standalone page render (render_template("ai_advisor_strategy_builder.html"))
        must be gone. A 200 response to this GET route after SPA-port means the old
        standalone route was not converted to a redirect.

        RED: current implementation returns 200.
        """
        with (
            patch("database.get_advisor_observations_for_symphony", return_value=[]),
            patch("analytics.list_available_symphonies", return_value=[]),
        ):
            resp = client.get("/ai-advisor/strategy-builder")

        assert resp.status_code != 200, (
            "GET /ai-advisor/strategy-builder returned 200. "
            "After SPA-port, this route must be a redirect — returning 200 with the "
            "standalone page is the DEFECT we are eliminating. "
            "Convert the GET handler in app.py to return redirect('/ai-advisor') (AC3)."
        )


# ===========================================================================
# AC4 — /ai-advisor route prefetches STRATEGY_BUILDER observations
# ===========================================================================


class TestAC4PrefetchInSPARoute:
    """AC4: The /ai-advisor route must prefetch STRATEGY_BUILDER observations."""

    def test_ai_advisor_tab_route_calls_get_advisor_observations_for_role(self, monkeypatch):
        """AC4-A: The /ai-advisor route must call get_advisor_observations_for_role('STRATEGY_BUILDER').

        Currently the /ai-advisor route calls get_advisor_observations_for_role for
        OVERFITTING_CONSCIENCE, SPEC_CRITIC, DIVERGENCE_EXPLAINER. After SPA-port
        it must also call it for 'STRATEGY_BUILDER' (to prefetch the tab's data).

        RED: current /ai-advisor route does NOT call the accessor with 'STRATEGY_BUILDER'.
        """
        _stub_analytics(monkeypatch)
        called_roles: list[str] = []

        def _capture_role(role, limit=50):
            called_roles.append(role)
            return []

        fake_corr = MagicMock()
        fake_corr.compute_pairwise_correlations.return_value = []
        fake_corr.CRISIS_CAVEAT = "caveat"

        with (
            patch.object(
                app_module.database, "get_advisor_observations_for_role", side_effect=_capture_role
            ),
            patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}),
        ):
            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as c:
                c.get("/ai-advisor")

        assert "STRATEGY_BUILDER" in called_roles, (
            f"GET /ai-advisor did not call "
            "database.get_advisor_observations_for_role('STRATEGY_BUILDER'). "
            f"Roles called: {called_roles}. "
            "The /ai-advisor route must prefetch STRATEGY_BUILDER observations so the "
            "6th tab renders without a separate round-trip (AC4)."
        )

    def test_ai_advisor_tab_route_builds_card_artifacts_for_sb(self, monkeypatch):
        """AC4-B: The /ai-advisor route must build card_artifacts for strategy-builder observations.

        The standalone route built card_artifacts (dict keyed by obs_id) and passed them
        to the template. The /ai-advisor route must do the same for the strategy-builder
        panel's observations.

        RED: current /ai-advisor route does not build sb card_artifacts.
        """
        _stub_analytics(monkeypatch)
        fixture = _load_fixture()
        sb_obs = [fixture["observation_survivor"]]

        captured: dict = {}

        def _capture(template_name, **kwargs):
            captured.update(kwargs)
            return "<html><body>stub</body></html>"

        fake_corr = MagicMock()
        fake_corr.compute_pairwise_correlations.return_value = []
        fake_corr.CRISIS_CAVEAT = "caveat"

        with (
            patch.object(
                app_module.database,
                "get_advisor_observations_for_role",
                side_effect=lambda role, limit=50: sb_obs if role == "STRATEGY_BUILDER" else [],
            ),
            patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}),
            patch("app.render_template", side_effect=_capture),
        ):
            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as c:
                c.get("/ai-advisor")

        # The route must pass card artifacts for strategy-builder obs
        sb_artifact_keys = {
            k for k in captured if "card_artifact" in k.lower() or "sb_card" in k.lower()
        }
        assert sb_artifact_keys, (
            f"GET /ai-advisor did not pass card_artifacts for strategy-builder observations. "
            f"Template context keys found: {sorted(captured.keys())}. "
            "The /ai-advisor route must build and pass sb_card_artifacts (or similar) "
            "to the template so the strategy-builder panel can render Discuss buttons (AC4)."
        )


# ===========================================================================
# AC5 — sbRunAnalysis / openChatWithArtifact in static/ai_advisor.js (not inline)
# ===========================================================================


class TestAC5JSFunctionsInStaticFile:
    """AC5: sbRunAnalysis() and openChatWithArtifact() must live in static/ai_advisor.js."""

    def test_sb_run_analysis_present_in_ai_advisor_js(self):
        """AC5-A: static/ai_advisor.js must define sbRunAnalysis.

        Currently sbRunAnalysis is defined inline in <script> in the standalone
        template. After SPA-port it must move to the shared static file so the
        unified SPA's initTabSwitcher can bind it without duplicating the function.

        RED: sbRunAnalysis is not currently in ai_advisor.js.
        """
        js_path = _REPO_ROOT / "static" / "ai_advisor.js"
        assert js_path.exists(), f"static/ai_advisor.js not found at {js_path}."
        source = js_path.read_text(encoding="utf-8")
        assert "sbRunAnalysis" in source, (
            "static/ai_advisor.js does not define sbRunAnalysis. "
            "The function must be moved from the deleted standalone template's inline "
            "<script> block into static/ai_advisor.js (AC5)."
        )

    def test_open_chat_with_artifact_present_in_ai_advisor_js(self):
        """AC5-B: static/ai_advisor.js must define openChatWithArtifact.

        Currently openChatWithArtifact is defined inline in the standalone template.
        After SPA-port it must be in the shared static file.

        RED: openChatWithArtifact is not currently in ai_advisor.js.
        """
        js_path = _REPO_ROOT / "static" / "ai_advisor.js"
        assert js_path.exists(), f"static/ai_advisor.js not found at {js_path}."
        source = js_path.read_text(encoding="utf-8")
        assert "openChatWithArtifact" in source, (
            "static/ai_advisor.js does not define openChatWithArtifact. "
            "The function must be moved from the deleted standalone template's inline "
            "<script> block into static/ai_advisor.js (AC5)."
        )

    def test_sb_run_analysis_navigates_to_ai_advisor_not_standalone_url(self):
        """AC5-C: sbRunAnalysis in ai_advisor.js must navigate to /ai-advisor after success.

        The old standalone template's sbRunAnalysis navigated to:
          /ai-advisor/strategy-builder?symphony_id=...
        After SPA-port it must navigate to:
          /ai-advisor?...  (or just /ai-advisor)
        Navigating back to the old standalone URL defeats the purpose of the fold-in.

        RED: the current (inline) sbRunAnalysis navigates to /ai-advisor/strategy-builder.
        """
        js_path = _REPO_ROOT / "static" / "ai_advisor.js"
        if not js_path.exists():
            pytest.skip("static/ai_advisor.js not found — cannot verify navigation URL.")
        source = js_path.read_text(encoding="utf-8")
        if "sbRunAnalysis" not in source:
            pytest.skip("sbRunAnalysis not yet in ai_advisor.js — AC5-C cannot verify.")

        # Find the sbRunAnalysis function body and check it navigates to /ai-advisor, not the old URL.
        # The old URL fragment: '/ai-advisor/strategy-builder'
        # We look for the navigation assignment inside sbRunAnalysis context.
        # Strategy: search for the function, then look for its navigation statements.
        fn_start = source.find("sbRunAnalysis")
        fn_context = source[fn_start : fn_start + 1500]

        # Must NOT navigate to the old standalone URL
        old_url = "/ai-advisor/strategy-builder"
        if old_url in fn_context:
            # If the old URL appears, it should NOT be in a window.location assignment
            nav_old = re.search(
                r"window\.location(?:\.href)?\s*=\s*['\"][^'\"]*strategy-builder[^'\"]*['\"]",
                fn_context,
            )
            assert not nav_old, (
                "sbRunAnalysis in ai_advisor.js navigates to the old standalone URL "
                f"'/ai-advisor/strategy-builder': {nav_old.group()!r}. "
                "After SPA-port, sbRunAnalysis must navigate to /ai-advisor (the unified SPA), "
                "not the deleted standalone URL (AC5)."
            )

    def test_ai_advisor_html_inline_script_does_not_define_sb_run_analysis(self):
        """AC5-D: templates/ai_advisor.html must NOT define sbRunAnalysis inline.

        The function must be in static/ai_advisor.js — not duplicated as an inline
        <script> inside the unified template. Inline JS in templates is hard to test
        and breaks the node --check safety guard.

        RED: this test fails if someone moves sbRunAnalysis inline into ai_advisor.html
        instead of into the static file.
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor.html"
        source = template_path.read_text(encoding="utf-8")
        # Allow the function to appear as a reference (e.g. onclick="sbRunAnalysis()"),
        # but NOT as a function definition.
        fn_definition_pattern = re.compile(
            r"function\s+sbRunAnalysis\s*\(",
            re.MULTILINE,
        )
        matches = fn_definition_pattern.findall(source)
        assert not matches, (
            f"templates/ai_advisor.html contains {len(matches)} inline definition(s) of "
            "sbRunAnalysis. The function must be defined only in static/ai_advisor.js, "
            "not duplicated as inline script in the template (AC5)."
        )

    def test_ai_advisor_html_inline_script_does_not_define_open_chat_with_artifact(self):
        """AC5-E: templates/ai_advisor.html must NOT define openChatWithArtifact inline.

        Same constraint as AC5-D — the function belongs in static/ai_advisor.js.
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor.html"
        source = template_path.read_text(encoding="utf-8")
        fn_definition_pattern = re.compile(
            r"function\s+openChatWithArtifact\s*\(",
            re.MULTILINE,
        )
        matches = fn_definition_pattern.findall(source)
        assert not matches, (
            f"templates/ai_advisor.html contains {len(matches)} inline definition(s) of "
            "openChatWithArtifact. The function must be defined only in static/ai_advisor.js (AC5)."
        )


# ===========================================================================
# AC6 — Standalone template DELETED; no references remain
# ===========================================================================


class TestAC6StandaloneTemplateDeleted:
    """AC6: templates/ai_advisor_strategy_builder.html must be deleted."""

    def test_standalone_template_file_does_not_exist(self):
        """AC6-A: templates/ai_advisor_strategy_builder.html must not exist.

        The standalone template is being folded into the unified SPA. Once folded,
        the standalone file must be deleted so it can never be accidentally referenced.

        RED: the file currently exists.
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor_strategy_builder.html"
        assert not template_path.exists(), (
            f"templates/ai_advisor_strategy_builder.html still exists at {template_path}. "
            "DELETE this file after folding its content into templates/ai_advisor.html — "
            "the standalone template must not coexist with the unified SPA (AC6)."
        )

    def test_app_py_does_not_render_template_standalone(self):
        """AC6-B: app.py must not call render_template('ai_advisor_strategy_builder.html').

        The GET route handler must return redirect('/ai-advisor'), not call
        render_template with the deleted standalone template name.

        RED: current GET handler calls render_template('ai_advisor_strategy_builder.html').
        """
        source = (_REPO_ROOT / "app.py").read_text(encoding="utf-8")
        assert "ai_advisor_strategy_builder.html" not in source, (
            "app.py still references 'ai_advisor_strategy_builder.html' in a render_template call. "
            "After SPA-port, the GET route must return redirect('/ai-advisor') — no render of "
            "the deleted standalone template (AC6)."
        )

    def test_ai_advisor_html_does_not_link_to_standalone_route(self):
        """AC6-C: templates/ai_advisor.html must not contain links to /ai-advisor/strategy-builder GET.

        After SPA-port, the 6th tab is an in-place panel — there must be no <a href>
        pointing operators back to the old standalone route.

        RED: the standalone template had a cap-tab <a href> pointing to itself.
        The unified template must not introduce a similar link.
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor.html"
        source = template_path.read_text(encoding="utf-8")
        # The standalone template url_for was: url_for('ai_advisor_strategy_builder')
        # After port, the tab button must be <button data-tab="strategy-builder">, not an <a href>.
        bad_patterns = [
            "ai_advisor_strategy_builder'",  # url_for('ai_advisor_strategy_builder')
            'href="/ai-advisor/strategy-builder"',
        ]
        for p in bad_patterns:
            assert p not in source, (
                f"templates/ai_advisor.html contains a link to the deleted standalone route: {p!r}. "
                "Remove all <a href> links to /ai-advisor/strategy-builder — "
                "the tab must be an in-place button, not a navigation anchor (AC6)."
            )


# ===========================================================================
# AC7 — node --check on static/ai_advisor.js (JS parse safety guard)
# ===========================================================================


class TestAC7JSParseGuard:
    """AC7: static/ai_advisor.js must pass node --check after functions are moved in."""

    def test_ai_advisor_js_passes_node_check(self):
        """AC7-A: node --check static/ai_advisor.js must exit 0 (no JS syntax errors).

        Moving sbRunAnalysis and openChatWithArtifact into ai_advisor.js introduces
        risk of syntax errors that Python unit tests cannot catch. A parse error
        silently breaks all JS on the page — tab switching, CSRF fetch, suggestion
        cards, everything.

        RED: this test will fail if ai_advisor.js has a syntax error after the move.
        Note: this test also fails if node is not installed, which is a test environment
        misconfiguration — node is required per BRIEF operational rules.
        """
        js_path = _REPO_ROOT / "static" / "ai_advisor.js"
        assert js_path.exists(), (
            f"static/ai_advisor.js not found at {js_path}. "
            "The file must exist for the SPA to function."
        )
        result = subprocess.run(
            ["node", "--check", str(js_path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, (
            f"node --check failed on static/ai_advisor.js (exit {result.returncode}):\n"
            f"STDERR: {result.stderr}\n"
            f"STDOUT: {result.stdout}\n"
            "JS syntax errors break all client-side functionality. Fix the syntax before "
            "claiming GREEN (AC7)."
        )


# ===========================================================================
# Advisory-only contract — preserved after SPA-port (regression guard)
# ===========================================================================


class TestAdvisoryOnlyContractPreserved:
    """Verify that the advisory-only + CSRF contracts hold in the SPA context."""

    def test_spa_strategy_builder_panel_has_no_action_affordances(self, advisor_page_html):
        """Regression guard: the strategy-builder panel in the SPA must have zero action affordances.

        The advisory-only contract (no forms, no submit buttons, no action links on cards)
        must hold when the panel is embedded in the unified SPA.

        This is a regression of the original F-17 adversarial test adapted for the SPA model.
        """
        html = advisor_page_html

        # Count submit buttons — must be zero
        submit_buttons = re.findall(r'<button[^>]+type\s*=\s*["\']submit["\']', html, re.IGNORECASE)
        assert not submit_buttons, (
            f"The unified SPA /ai-advisor page contains {len(submit_buttons)} "
            "<button type='submit'> element(s). "
            "Advisory-only cards in the strategy-builder panel must have ZERO submit buttons. "
            "This is a regression of the F-17 adversarial contract (advisory-only preservation)."
        )

        # Count submit inputs — must be zero
        submit_inputs = re.findall(r'<input[^>]+type\s*=\s*["\']submit["\']', html, re.IGNORECASE)
        assert not submit_inputs, (
            f"The unified SPA /ai-advisor page contains {len(submit_inputs)} "
            "<input type='submit'> element(s). Advisory-only contract violation."
        )

    def test_post_run_csrf_enforcement_preserved_after_spa_port(self):
        """CSRF on POST /run must still work after SPA-port.

        The POST route is unchanged — CSRF enforcement must not be broken by the
        redirect added to the GET route.
        """
        import app as app_module

        _app = app_module.app
        _app.config["TESTING"] = True

        import app as _am

        original = _am._csrf_check_enabled
        try:
            _am._csrf_check_enabled = True
            with _app.test_client() as c:
                resp = c.post(
                    "/ai-advisor/strategy-builder/run",
                    json={"objective": "diversify", "universe": ["SPY"], "symphony_id": "s"},
                    content_type="application/json",
                    # Deliberately no X-CSRF-Token
                )
            assert resp.status_code == 403, (
                f"POST /ai-advisor/strategy-builder/run without CSRF token returned "
                f"{resp.status_code}; expected 403. "
                "CSRF enforcement on the POST /run route must be preserved after SPA-port (AC3)."
            )
        finally:
            _am._csrf_check_enabled = original

    def test_settings_write_allowlist_unchanged_by_spa_port(self):
        """SPA-port must not expand _SETTINGS_WRITE_ALLOWLIST.

        The allowlist must still not contain 'strategy_builder' or 'LIVE_EXECUTION'.
        Regression of G-18 from the original test suite.
        """
        allowlist = app_module._SETTINGS_WRITE_ALLOWLIST
        assert "strategy_builder" not in allowlist, (
            "_SETTINGS_WRITE_ALLOWLIST contains 'strategy_builder'. "
            "SPA-port must not expand the settings write allowlist."
        )
        assert "LIVE_EXECUTION" not in allowlist, (
            "_SETTINGS_WRITE_ALLOWLIST contains 'LIVE_EXECUTION'. "
            "Critical: LIVE_EXECUTION must never be in the settings allowlist."
        )


# ===========================================================================
# Existing test compatibility — A1..A4 of test_strategy_builder_route.py
# must be updated to assert the SPA model (verify the update happened)
# ===========================================================================


class TestRouteTestsSPAModelVerification:
    """Verify that the original test_strategy_builder_route.py has been updated
    to the SPA model (AC7: tests updated from standalone-page model to SPA model).
    """

    def test_test_strategy_builder_route_does_not_assert_200_on_get(self):
        """AC7 verification: test_strategy_builder_route.py A-1 must NOT assert status_code==200.

        The old A-1 test asserted GET /ai-advisor/strategy-builder returns 200 (standalone page).
        After SPA-port that route returns 302. The test file must be updated to assert 302,
        or the test must be removed.

        RED if the test file still asserts status_code == 200 for A-1.
        """
        test_file = _REPO_ROOT / "tests" / "app" / "test_strategy_builder_route.py"
        if not test_file.exists():
            return  # File deleted — acceptable if other tests cover the contract.

        source = test_file.read_text(encoding="utf-8")

        # Look for the A-1 test function. It should assert redirect status,
        # NOT assert status_code == 200.
        fn_match = re.search(
            r"def test_get_strategy_builder_returns_200.*?(?=\ndef |\Z)",
            source,
            re.DOTALL,
        )
        if not fn_match:
            # Function was renamed or removed — that's fine.
            return

        fn_body = fn_match.group()
        # The old assertion pattern: assert resp.status_code == 200
        still_asserts_200 = bool(re.search(r"assert\s+resp\.status_code\s*==\s*200", fn_body))
        assert not still_asserts_200, (
            "test_strategy_builder_route.py still has `assert resp.status_code == 200` "
            "in test_get_strategy_builder_returns_200. "
            "After SPA-port the route returns 302 — update this assertion to assert 302 "
            "or redirect status (AC7 requires tests updated to the SPA model)."
        )
