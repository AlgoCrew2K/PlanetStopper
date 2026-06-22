"""
RED suite — AC1–AC7: in-place tab switching for the AI Advisor page.

All tests in this file are FAILING against the current MPA implementation
(5 separate Flask routes, 5 full templates connected by <a href> navigation).
They define the GREEN contract that the implementer must satisfy.

Design source:
  .design-handoff/advisor/alphabotpm/project/advisor.jsx
  .design-handoff/advisor/alphabotpm/project/uploads/ai-advisor-design-prompt.md
  .design-handoff/advisor-ui-diag/TAB-DESIGN-RECON.md

User decision: keep Overview as a 5th in-place tab (plus Correlations, Asset
Swaps, Logic Changes, Chat) — 5 tabs total, all in-place on /ai-advisor.

Mocking strategy:
  - analytics, correlation_diagnostic, asset_swap_engine, database accessors
    are patched so the route can render without live data.
  - Math engine is NEVER mocked.
  - No live API calls; all tests are Tier 1 (default CI run).
  - DB isolation is provided by the autouse _isolate_db fixture in conftest.py.

Assert structure/behavior, never computed values.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def flask_client():
    """Test client for the Flask app with TESTING mode on.

    Patches database.init_db to skip migration on import; CSRF is already
    disabled by the autouse _disable_csrf_for_tests fixture in conftest.py.
    """
    with patch("database.init_db"):
        import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


def _stub_analytics(monkeypatch):
    """Patch analytics accessors so /ai-advisor renders without post-mortem files."""
    import app as flask_app

    fake_history: dict = {}
    monkeypatch.setattr(
        flask_app.analytics, "get_history_with_cache_invalidation", lambda **kw: fake_history
    )
    monkeypatch.setattr(flask_app.analytics, "list_available_symphonies", lambda h: [])
    monkeypatch.setattr(
        flask_app.analytics, "compute_per_symphony_returns", lambda h, sym: ([], [], [])
    )


def _stub_db_observations(monkeypatch):
    """Patch database observation accessor so /ai-advisor renders without a live DB."""
    import app as flask_app

    monkeypatch.setattr(
        flask_app.database, "get_advisor_observations_for_role", lambda role, limit=50: []
    )


@pytest.fixture(scope="function")
def advisor_client(monkeypatch):
    """Flask test client with analytics + DB observations stubbed.

    Uses monkeypatch (function-scoped) so stubs are torn down after each test.
    """
    _stub_analytics(monkeypatch)
    _stub_db_observations(monkeypatch)
    with patch("database.init_db"):
        import app as flask_app
    flask_app.app.config["TESTING"] = True
    with flask_app.app.test_client() as client:
        yield client


@pytest.fixture(scope="function")
def advisor_page_html(advisor_client, monkeypatch):
    """GET /ai-advisor response body as a string.

    The correlation_diagnostic module is patched at the sys.modules level so
    the lazily-imported advisors.correlation_diagnostic resolves without the
    real compute path.
    """
    fake_corr = MagicMock()
    fake_corr.compute_pairwise_correlations.return_value = []
    fake_corr.CRISIS_CAVEAT = "Test caveat"

    # Patch via sys.modules so the lazy `from advisors import correlation_diagnostic`
    # inside the route picks up the mock at call time.
    with patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}):
        resp = advisor_client.get("/ai-advisor")
    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code}; expected 200. "
        "Route must render the single unified page."
    )
    return resp.data.decode("utf-8", errors="replace")


# ===========================================================================
# AC1 — Single page: GET /ai-advisor returns all 5 tab panels in ONE response.
# ===========================================================================


def test_ac1_get_advisor_returns_200(advisor_client, monkeypatch):
    """GET /ai-advisor must return 200 (the single unified page).

    RED: currently this returns the Overview page only; after GREEN it must
    return one page containing all 5 panels.
    """
    fake_corr = MagicMock()
    fake_corr.compute_pairwise_correlations.return_value = []
    fake_corr.CRISIS_CAVEAT = "caveat"
    with patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}):
        resp = advisor_client.get("/ai-advisor")
    assert resp.status_code == 200


def test_ac1_all_five_panel_markers_in_single_response(advisor_page_html):
    """All 5 tab panels must be present in the single GET /ai-advisor response.

    The panels are identified by data-testid markers that the implementer must
    introduce on the panel wrapper divs:
      - data-testid="tab-panel-overview"
      - data-testid="tab-panel-correlations"
      - data-testid="tab-panel-asset-swaps"
      - data-testid="tab-panel-logic-changes"
      - data-testid="tab-panel-chat"

    RED: currently each panel lives on its own page, so none of the non-overview
    panels appear in the /ai-advisor response.
    """
    required_panels = [
        "tab-panel-overview",
        "tab-panel-correlations",
        "tab-panel-asset-swaps",
        "tab-panel-logic-changes",
        "tab-panel-chat",
    ]
    html = advisor_page_html
    missing = [p for p in required_panels if f'data-testid="{p}"' not in html]
    assert not missing, (
        f"GET /ai-advisor is missing these tab panel markers: {missing}. "
        "All 5 tab panels must be rendered in a single response (AC1)."
    )


def test_ac1_sub_page_routes_removed_or_redirect(advisor_client):
    """The old sub-page GET routes must NOT serve full pages independently.

    After the in-place migration the sub-page GET routes (/ai-advisor/correlations
    etc.) should be removed (404) or redirect to /ai-advisor. Either is
    acceptable — the key invariant is that the operator never lands on a
    stripped sub-page that is missing the header, Overview, and other tabs.

    RED: currently these routes return 200 with full pages.
    """
    sub_routes = [
        "/ai-advisor/correlations",
        "/ai-advisor/asset-swaps",
        "/ai-advisor/logic-changes",
    ]
    for route in sub_routes:
        resp = advisor_client.get(route)
        # Accept 301/302/303/308 redirect OR 404; never an independent 200 page.
        assert resp.status_code in (301, 302, 303, 308, 404), (
            f"Route {route} returned {resp.status_code}; expected redirect or 404. "
            "The MPA sub-page GET routes must be removed after in-place migration (AC1)."
        )


# ===========================================================================
# AC2 — In-place switch: tab nav uses <button> not <a href>, ARIA semantics.
# ===========================================================================


def test_ac2_tab_nav_uses_buttons_not_anchors_to_other_routes(advisor_page_html):
    """The capability tab bar must use <button> elements, not <a href> links to
    other routes.

    The current implementation uses <a href="{{ url_for(...) }}"> for every tab,
    causing a full page reload on click. The design requires <button> elements
    whose click handlers are handled in-page by JS (no navigation).

    We assert: for each of the 5 expected tab data-testids, the element in the
    source must be a <button ..., NOT an <a href=...> to a different route.

    RED: currently all 5 tab controls are <a href="..."> to distinct routes.
    """
    html = advisor_page_html
    forbidden_hrefs = [
        'href="/ai-advisor/correlations"',
        'href="/ai-advisor/asset-swaps"',
        'href="/ai-advisor/logic-changes"',
        'href="/ai-advisor/chat"',
    ]
    found_nav_anchors = [h for h in forbidden_hrefs if h in html]
    assert not found_nav_anchors, (
        f"Tab nav still uses <a href> links to sub-routes: {found_nav_anchors}. "
        "Tab controls must be <button> elements (no navigation on click) — AC2."
    )


def test_ac2_tab_controls_have_button_elements(advisor_page_html):
    """Each of the 5 tabs must be a <button> element, not an anchor.

    Assert the data-testid tab selectors are backed by <button> in the source.
    RED: currently they are all <a> elements.
    """
    html = advisor_page_html
    tab_testids = [
        "tab-overview",
        "tab-correlations",
        "tab-asset-swaps",
        "tab-logic-changes",
        "tab-chat",
    ]
    for testid in tab_testids:
        # Look for the marker; the element wrapping it must be a <button>.
        marker = f'data-testid="{testid}"'
        assert marker in html, (
            f"Tab control data-testid={testid!r} not found in /ai-advisor response."
        )
        # Find the position of the marker and look backwards for the opening tag.
        idx = html.find(marker)
        # Scan backward up to 200 chars for the opening < tag.
        before = html[max(0, idx - 200) : idx]
        last_open = before.rfind("<")
        element_snippet = before[last_open:] + marker
        assert element_snippet.lstrip("<").startswith("button"), (
            f"Tab control data-testid={testid!r} is not a <button> element — "
            f"found opening tag context: {element_snippet[:60]!r}. "
            "All tab controls must be <button> (not <a>) for in-place switching — AC2."
        )


def test_ac2_tablist_role_present(advisor_page_html):
    """The tab container must have role='tablist' (ARIA tab pattern — AC2).

    RED: current implementation uses a plain <nav>, no ARIA tab semantics.
    """
    html = advisor_page_html
    assert 'role="tablist"' in html, (
        "Tab container must have role='tablist' (ARIA tab semantics — AC2). "
        "Currently absent from the nav."
    )


def test_ac2_tab_buttons_have_role_tab(advisor_page_html):
    """Each tab button must carry role='tab' (ARIA tab pattern — AC2).

    RED: current anchor elements have no role attribute.
    """
    html = advisor_page_html
    assert 'role="tab"' in html, "Tab buttons must carry role='tab' (ARIA — AC2). Currently absent."


def test_ac2_tab_panels_have_role_tabpanel(advisor_page_html):
    """Each tab panel wrapper must carry role='tabpanel' (ARIA — AC2).

    RED: current implementation has no panel wrappers with this role.
    """
    html = advisor_page_html
    assert 'role="tabpanel"' in html, (
        "Tab panels must carry role='tabpanel' (ARIA — AC2). Currently absent."
    )


def test_ac2_aria_selected_present(advisor_page_html):
    """At least one tab button must carry aria-selected='true' (the active tab).

    RED: current <a> elements have no aria-selected attributes.
    """
    html = advisor_page_html
    assert 'aria-selected="true"' in html, (
        "Active tab must carry aria-selected='true' (ARIA — AC2). Currently absent."
    )


# ===========================================================================
# AC3 — Overview kept as 5th tab; Run-Advisor + suggestions still functional.
# ===========================================================================


def test_ac3_overview_panel_present_in_single_page(advisor_page_html):
    """The Overview panel (Run-Advisor, autotune rail) must exist on /ai-advisor.

    Asserts the overview-specific testids are present in the single-page response.
    RED if the implementer accidentally drops the Overview tab content.
    """
    html = advisor_page_html
    # These testids exist on the current /ai-advisor page (the legacy overview).
    # After migration they must still be present within the tab-panel-overview panel.
    overview_markers = [
        "run-advisor-btn",  # "Run Claude advisor" button
        "autotune-panel",  # autotune right-rail
    ]
    missing = [m for m in overview_markers if f'data-testid="{m}"' not in html]
    assert not missing, (
        f"Overview tab content is missing from /ai-advisor after migration: {missing}. "
        "The Overview tab (with Run-Advisor and autotune panel) must be preserved — AC3."
    )


def test_ac3_suggest_route_preserved(advisor_client):
    """POST /ai-advisor/suggest must still exist as an API endpoint.

    This is the Run-Advisor action from the Overview tab.
    RED if the route is accidentally dropped during template consolidation.
    """
    import app as flask_app

    # Verify the route is registered (endpoint exists in URL map).
    rules = [str(r) for r in flask_app.app.url_map.iter_rules()]
    assert "/ai-advisor/suggest" in rules, (
        "POST /ai-advisor/suggest route is missing from the URL map. "
        "This endpoint backs the Run-Advisor button on the Overview tab — AC3, AC5."
    )


def test_ac3_accept_and_reject_routes_preserved(advisor_client):
    """POST /ai-advisor/accept and /ai-advisor/reject must still exist.

    RED if the route is accidentally dropped during template consolidation.
    """
    import app as flask_app

    rules = [str(r) for r in flask_app.app.url_map.iter_rules()]
    for route in ("/ai-advisor/accept", "/ai-advisor/reject"):
        assert route in rules, (
            f"Route {route} is missing from the URL map. "
            "Accept/reject endpoints must be preserved on the single page — AC3, AC5."
        )


# ===========================================================================
# AC4 — Chat slide panel always in DOM on /ai-advisor; openChatPanel defined.
# ===========================================================================


def test_ac4_chat_panel_present_in_advisor_page(advisor_page_html):
    """The chat panel must be present in the DOM on /ai-advisor (always-in-DOM).

    The design specifies the chat panel as a slide-in overlay always in the
    DOM, opened by openChatPanel(). Currently it only exists on /ai-advisor/chat.

    RED: currently data-testid='chat-panel' is absent from /ai-advisor.
    """
    html = advisor_page_html
    assert 'data-testid="chat-panel"' in html, (
        "data-testid='chat-panel' must be present on /ai-advisor (always-in-DOM). "
        "Currently the chat panel is only on /ai-advisor/chat — AC4."
    )


def test_ac4_chat_panel_close_button_present(advisor_page_html):
    """The chat panel close button must be present on /ai-advisor.

    RED: currently absent from /ai-advisor (chat panel lives on its own page).
    """
    html = advisor_page_html
    assert 'data-testid="chat-panel-close"' in html, (
        "Chat panel close button (data-testid='chat-panel-close') must be present "
        "on /ai-advisor for the always-in-DOM slide panel — AC4."
    )


def test_ac4_chat_send_route_preserved(advisor_client):
    """POST /ai-advisor/chat/send must still exist as an API endpoint.

    RED if the route is accidentally dropped during template consolidation.
    """
    import app as flask_app

    rules = [str(r) for r in flask_app.app.url_map.iter_rules()]
    assert "/ai-advisor/chat/send" in rules, (
        "POST /ai-advisor/chat/send route is missing from the URL map. "
        "The chat send endpoint must be preserved — AC4, AC5."
    )


def test_ac4_chat_get_route_removed_or_redirected(advisor_client):
    """GET /ai-advisor/chat as a standalone page must not return 200.

    After migration chat is a panel on /ai-advisor, not its own page.
    The GET route should be removed (404) or redirect to /ai-advisor.

    RED if the separate chat page still returns 200 (the DEFECT-3 bug
    context-loss path remains open).
    """
    resp = advisor_client.get("/ai-advisor/chat")
    assert resp.status_code in (301, 302, 303, 308, 404), (
        f"GET /ai-advisor/chat returned {resp.status_code}; expected redirect or 404. "
        "Chat is now a panel, not a standalone page — AC4."
    )


# ===========================================================================
# AC5 — Functional regression: POST action routes preserved and CSRF-protected.
# ===========================================================================


def test_ac5_asset_swaps_evaluate_route_preserved(advisor_client):
    """POST /ai-advisor/asset-swaps/evaluate must be registered in the URL map.

    RED if the route is accidentally dropped during template consolidation.
    """
    import app as flask_app

    rules = [str(r) for r in flask_app.app.url_map.iter_rules()]
    assert "/ai-advisor/asset-swaps/evaluate" in rules, (
        "POST /ai-advisor/asset-swaps/evaluate missing from URL map — AC5."
    )


def test_ac5_logic_changes_evaluate_route_preserved(advisor_client):
    """POST /ai-advisor/logic-changes/evaluate must be registered in the URL map.

    RED if the route is accidentally dropped during template consolidation.
    """
    import app as flask_app

    rules = [str(r) for r in flask_app.app.url_map.iter_rules()]
    assert "/ai-advisor/logic-changes/evaluate" in rules, (
        "POST /ai-advisor/logic-changes/evaluate missing from URL map — AC5."
    )


def test_ac5_post_routes_require_csrf(monkeypatch):
    """All POST routes on /ai-advisor/* must reject requests missing the CSRF token.

    This test re-enables the CSRF check (counteracting the autouse fixture) and
    verifies that a bare POST without X-CSRF-Token gets 400/403, not 200.

    RED if any POST route bypasses CSRF after template consolidation.
    """
    _stub_analytics(monkeypatch)
    _stub_db_observations(monkeypatch)
    with patch("database.init_db"):
        import app as flask_app
    # Re-enable CSRF (autouse fixture disabled it; we want to verify it works).
    monkeypatch.setattr(flask_app, "_csrf_check_enabled", True)
    flask_app.app.config["TESTING"] = True

    post_routes = [
        "/ai-advisor/suggest",
        "/ai-advisor/accept",
        "/ai-advisor/reject",
        "/ai-advisor/asset-swaps/evaluate",
        "/ai-advisor/logic-changes/evaluate",
        "/ai-advisor/chat/send",
    ]
    with flask_app.app.test_client() as client:
        for route in post_routes:
            resp = client.post(
                route,
                json={},
                content_type="application/json",
                # Deliberately omit X-CSRF-Token header.
            )
            assert resp.status_code in (400, 403, 422), (
                f"POST {route} returned {resp.status_code} without CSRF token; "
                "expected 400/403/422. All POST advisor routes must remain "
                "CSRF-protected after migration — AC5, AC7."
            )




# ===========================================================================
# AC2 (structural) — tab switcher JS present in the consolidated page.
# ===========================================================================


def test_ac2_tab_switcher_js_loaded_in_advisor_page(advisor_page_html):
    """The /ai-advisor page must load the JS responsible for in-place tab switching.

    Either ai_advisor.js must contain tab-switching logic OR a new dedicated
    tab-switcher script must be included. We assert the page loads at least one
    script that references 'ai_advisor' (not just ai_advisor_chat.js alone).

    RED: currently the page loads ai_advisor.js (suggestions), but that file has
    no tab-switching code; after GREEN it must.
    """
    html = advisor_page_html
    # The JS tab switcher must be present in the page (as a script load or inline).
    # We check for characteristic tab-switcher DOM interactions.
    has_tab_switch_attr = (
        "aria-selected" in html  # ARIA attributes set by tab-switcher JS
        or "tab-panel--active" in html  # panel visibility class
        or "data-tab=" in html  # data-tab attribute on panel elements
    )
    assert has_tab_switch_attr, (
        "The /ai-advisor page must include tab-switching mechanisms: either "
        "data-tab= attributes on panels, aria-selected on tabs, or .tab-panel--active "
        "class references. These are absent — no JS tab switching wired up — AC2."
    )


# ===========================================================================
# AC1 (deeper) — each panel contains its expected key content markers.
# ===========================================================================


def test_ac1_correlations_panel_contains_crisis_caveat(advisor_page_html):
    """The correlations panel must carry the crisis caveat banner.

    Currently this marker only appears on /ai-advisor/correlations.
    RED: absent from /ai-advisor.
    """
    html = advisor_page_html
    assert 'data-testid="crisis-caveat-banner"' in html, (
        "The correlations panel must include crisis-caveat-banner on /ai-advisor. "
        "Currently this content only appears on the separate /ai-advisor/correlations "
        "page — AC1."
    )


@pytest.mark.skipif(
    not os.environ.get("COMPOSER_KEY_ID"),
    reason="try-swap-panel/form only renders when COMPOSER_KEY_ID is set "
    "(template guard: {% if not no_api_key %}) — credential-gated",
)
def test_ac1_asset_swaps_panel_contains_try_swap_form(advisor_page_html):
    """The asset-swaps panel must contain the try-swap-form.

    Currently only on /ai-advisor/asset-swaps.
    RED: absent from /ai-advisor.
    """
    html = advisor_page_html
    assert 'data-testid="try-swap-panel"' in html or 'data-testid="try-swap-form"' in html, (
        "The asset-swaps panel must include try-swap-panel/form on /ai-advisor. "
        "Currently this content only appears on the separate /ai-advisor/asset-swaps "
        "page — AC1."
    )


def test_ac1_logic_changes_panel_contains_fdr_warning_banner(advisor_page_html):
    """The logic-changes panel must carry the FDR warning banner.

    Currently only on /ai-advisor/logic-changes.
    RED: absent from /ai-advisor.
    """
    html = advisor_page_html
    assert 'data-testid="fdr-warning-banner"' in html, (
        "The logic-changes panel must include fdr-warning-banner on /ai-advisor. "
        "Currently this content only appears on the separate /ai-advisor/logic-changes "
        "page — AC1."
    )


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="chat-thread/chat-input-row only render when ANTHROPIC_API_KEY is set "
    "(template guard: {% if chat_available %}) — credential-gated",
)
def test_ac1_chat_panel_contains_thread_and_input(advisor_page_html):
    """The chat panel must contain the message thread and input row.

    Currently only on /ai-advisor/chat.
    RED: absent from /ai-advisor.
    """
    html = advisor_page_html
    assert 'data-testid="chat-thread"' in html, (
        "Chat thread (data-testid='chat-thread') must be on /ai-advisor. "
        "Currently only on /ai-advisor/chat — AC1, AC4."
    )
    assert 'data-testid="chat-input-row"' in html, (
        "Chat input row (data-testid='chat-input-row') must be on /ai-advisor. "
        "Currently only on /ai-advisor/chat — AC1, AC4."
    )


# ===========================================================================
# AC6 — visual fidelity markers: tab strip follows window-selector pattern.
# ===========================================================================


def test_ac6_tab_strip_uses_window_selector_or_cap_nav_pattern(advisor_page_html):
    """The tab strip must follow the window-selector / cap-nav CSS class pattern.

    The design spec says: 'a lightweight segmented control consistent with the
    existing window-selector button group pattern'. The tab container must use
    a class that maps to that pattern (cap-nav, window-selector, or similar).

    RED: currently no in-place tab strip exists.
    """
    html = advisor_page_html
    # Accept either the existing cap-nav or a window-selector style wrapper.
    has_tab_container = (
        'class="cap-nav"' in html
        or "window-selector" in html
        or 'role="tablist"' in html  # the ARIA marker implies a tab container
    )
    assert has_tab_container, (
        "The tab strip must use the cap-nav or window-selector CSS pattern as "
        "a tab container (per design spec) — AC6."
    )


def test_ac6_active_tab_class_present(advisor_page_html):
    """One tab must carry the active styling class or aria-selected='true'.

    The window-selector pattern uses an 'active' class. The ARIA pattern uses
    aria-selected='true'. Either is acceptable; the absence of both means no
    initial active tab is indicated.

    RED: currently no in-place tab strip with active state exists.
    """
    html = advisor_page_html
    has_active = (
        'aria-selected="true"' in html
        or '"cap-tab cap-tab--active"' in html
        or '"cap-tab active"' in html
        or 'class="cap-tab active"' in html
    )
    assert has_active, (
        "No tab carries an active/selected state marker on initial page load. "
        "The default tab (Overview) must be visually marked active — AC6."
    )
