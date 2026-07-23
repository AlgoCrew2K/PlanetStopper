"""RED tests — Frontrunner Builder Advisor-tab UI (feature-plans/frontrunner-builder.md
AC-8/AC-9-route/AC-10-UX, template/JS layer).

Team-lead-ruled scope (2026-07-11): tab button present in the SPA render,
pending-approval card anatomy (Calmar/CAGR/MDD/node-count deltas + source
badge + approve/reject), the INVERTED no-GET-triggerable-<a href> adversarial
contract (this tab HAS write-actions, unlike every other advisory-only tab —
action must be POST-only via JS fetch + CSRF header, never a navigable link),
no `| safe` on untrusted fields (candidate/symphony ids, error messages), the
collapsed <details> raw-preview (NOT open by default — "no debug-dump JSON
wall" guardrail), empty-state, and source-branching (frontrunner_builder rows
render incumbent-vs-candidate metrics; strategy_builder_retrofit rows render
candidate-only metrics — verified directly: strategy_builder_engine.py's
_persist_survivor call to insert_frontrunner_proposal at line 702 supplies
metrics_json={"cagr", "sharpe", "calmar", "max_drawdown"} with NO incumbent_*
keys, vs frontrunner_builder.py's own metrics_json at lines 1310-1318 which
has both incumbent_* and candidate_* keys).

Mirrors the established SPA-tab test pattern in
tests/app/test_strategy_builder_spa_port.py (AC1 tab-structure tests,
advisor_page_html fixture) and the existing rendered-card conventions already
in templates/ai_advisor.html (Strategy Builder's rejected-candidates
<details class="rules-collapsible"> block, stats-table, per-cell
data-testid="baseline-*" pattern for conditionally-rendered columns).

data-testid contract (frdash, locked 2026-07-11, msg afa4be87):
  Tab button:       data-testid="frontrunner-builder-tab"
  Panel:             data-testid="tab-panel-frontrunner-builder"
  Empty state:       data-testid="frontrunner-builder-empty-state"
  Cards container:   data-testid="frontrunner-proposal-cards"
  Each card:         data-testid="frontrunner-proposal-card" id="fr-card-{{ p.id }}"
  Source badge:      data-testid="proposal-source-badge"
  Approve button:    data-testid="fr-approve-btn" (onclick calls frApprove(...))
  Reject button:     data-testid="fr-reject-btn" (onclick calls frReject(...))

Per-metric-cell testids (this file's own RED contract — not yet in the repo;
authored here for frdash's GREEN to satisfy, mirroring the existing
baseline-sharpe/baseline-sortino/baseline-max-drawdown/baseline-cagr
per-cell-testid convention already used by Strategy Builder's own
rejected-candidate stats table):
  data-testid="fr-incumbent-calmar" / "fr-candidate-calmar"
  data-testid="fr-incumbent-cagr"   / "fr-candidate-cagr"
  data-testid="fr-incumbent-mdd"    / "fr-candidate-mdd"
  data-testid="fr-node-count-delta"
  data-testid="fr-raw-preview" (the collapsed <details> wrapping the raw
    candidate_tree/metrics_json preview)

Mocking strategy: only database.get_pending_frontrunner_proposals is
controlled per-test (the new accessor this route must call); analytics +
get_advisor_observations_for_role + the correlation module are stubbed
identically to test_strategy_builder_spa_port.py's own fixtures so the rest
of the (unrelated) /ai-advisor render succeeds.
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Shared stub helpers — mirrors test_strategy_builder_spa_port.py exactly
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
# Fixture-row factories — synthetic test INPUT (not producer-computed
# output), mirroring the codebase's _make_proposal_run / _load_fixture
# factory pattern (feedback_no_hardcoded_test_values applies to asserting
# hardcoded PRODUCER values, not to authoring our own controlled test rows).
# ---------------------------------------------------------------------------

_FR_METRICS = {
    "incumbent_cagr": 0.08,
    "incumbent_max_drawdown": -0.22,
    "incumbent_calmar": 0.36,
    "candidate_cagr": 0.15,
    "candidate_max_drawdown": -0.11,
    "candidate_calmar": 1.36,
    "candidate_sharpe": 1.42,
    "candidate_volatility": 0.14,
    "node_count_delta": -6,
}

_RETROFIT_METRICS = {
    "cagr": 0.19,
    "sharpe": 1.55,
    "calmar": 1.9,
    "max_drawdown": -0.10,
}


def _make_pending_row(
    *,
    row_id: int = 1,
    symphony_id: str = "sym-abc123",
    proposal_source: str = "frontrunner_builder",
    candidate_tree: dict | None = None,
    metrics_json: dict | None = None,
    error_message: str | None = None,
) -> dict:
    """A synthetic frontrunner_proposals row shaped exactly per
    database._FRONTRUNNER_PROPOSAL_COLUMNS (verified directly, database.py:3296).
    """
    return {
        "id": row_id,
        "created_at": "2026-07-10T12:00:00Z",
        "updated_at": "2026-07-10T12:00:00Z",
        "symphony_id": symphony_id,
        "proposal_source": proposal_source,
        "approval_status": "pending",
        "candidate_tree": candidate_tree if candidate_tree is not None else {"step": "root"},
        "metrics_json": metrics_json
        if metrics_json is not None
        else dict(_FR_METRICS if proposal_source == "frontrunner_builder" else _RETROFIT_METRICS),
        "created_symphony_id": None,
        "error_message": error_message,
    }


# ---------------------------------------------------------------------------
# Flask client + advisor-page-render fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _render_advisor_page(client, monkeypatch, *, pending_rows: list[dict] | None = None) -> str:
    """GET /ai-advisor with the frontrunner accessor controlled; returns decoded HTML.

    pending_rows=None means database.get_pending_frontrunner_proposals is not
    yet called by the route at all (current, pre-GREEN state) — the mock
    still returns [] in that case since an unpatched-but-uncalled mock has no
    bearing on the render either way.
    """
    _stub_analytics(monkeypatch)
    _stub_db_observations(monkeypatch)

    fake_corr = MagicMock()
    fake_corr.compute_pairwise_correlations.return_value = []
    fake_corr.CRISIS_CAVEAT = "Test caveat"

    with (
        patch.object(
            app_module.database,
            "get_pending_frontrunner_proposals",
            return_value=pending_rows if pending_rows is not None else [],
        ),
        patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code}; expected 200 for SPA base."
    )
    return resp.data.decode("utf-8", errors="replace")


def _panel_source(html: str) -> str:
    """Extract the frontrunner-builder tab panel's HTML slice for scoped assertions.

    Falls back to the full page when the panel marker is absent (pre-GREEN —
    callers assert on absence in that case, so an empty/whole-page fallback
    is safe: 'not found in the whole page' implies 'not found in the panel').
    """
    start = html.find('id="tab-panel-frontrunner-builder"')
    if start == -1:
        return ""
    # Panels are large siblings; bound the slice at the next top-level
    # '</div>{# /tab-panel-' closing comment used consistently in this
    # template (verified: templates/ai_advisor.html:2022 for strategy-builder).
    end = html.find("{# /tab-panel-", start)
    return html[start : end if end != -1 else start + 20000]


# ===========================================================================
# Group A: tab button + panel structure
# ===========================================================================


class TestTabStructure:
    def test_spa_contains_frontrunner_builder_tab_button(self, client, monkeypatch):
        html = _render_advisor_page(client, monkeypatch)
        assert 'data-testid="frontrunner-builder-tab"' in html, (
            "GET /ai-advisor must contain a tab button with "
            'data-testid="frontrunner-builder-tab" — the Frontrunner Builder tab is '
            "absent from the unified SPA template (AC-8)."
        )

    def test_spa_contains_frontrunner_builder_panel_id(self, client, monkeypatch):
        html = _render_advisor_page(client, monkeypatch)
        assert 'id="tab-panel-frontrunner-builder"' in html, (
            'GET /ai-advisor must contain a panel with id="tab-panel-frontrunner-builder" (AC-8).'
        )

    def test_frontrunner_builder_panel_has_role_tabpanel(self, client, monkeypatch):
        html = _render_advisor_page(client, monkeypatch)
        start = html.find('id="tab-panel-frontrunner-builder"')
        assert start != -1, "tab-panel-frontrunner-builder not found (AC-8)."
        context = html[max(0, start - 200) : start + 200]
        assert 'role="tabpanel"' in context, (
            'The tab-panel-frontrunner-builder div must have role="tabpanel" on the same '
            "element (ARIA tab pattern, AC-8)."
        )

    def test_frontrunner_builder_tab_button_is_a_button_not_anchor(self, client, monkeypatch):
        html = _render_advisor_page(client, monkeypatch)
        marker = 'data-testid="frontrunner-builder-tab"'
        idx = html.find(marker)
        if idx == -1:
            pytest.fail("frontrunner-builder-tab not found in /ai-advisor — cannot verify.")
        before = html[max(0, idx - 300) : idx]
        last_open = before.rfind("<")
        tag_snippet = before[last_open:] + marker
        assert tag_snippet.lstrip("<").startswith("button"), (
            f"The frontrunner-builder tab control must be a <button> element, not <a href>. "
            f"Found opening tag context: {tag_snippet[:80]!r}. In-place tab switching "
            'requires <button role="tab"> (AC-8).'
        )

    def test_spa_has_at_least_seven_tab_buttons_total(self, client, monkeypatch):
        """Existing count is 6 (Overview/Correlations/Asset Swaps/Logic Changes/Chat/
        Strategy Builder — verified: templates/ai_advisor.html has 6 data-tab= entries).
        Adding Frontrunner Builder must bring it to 7."""
        html = _render_advisor_page(client, monkeypatch)
        tab_buttons = re.findall(r'role="tab"', html)
        assert len(tab_buttons) >= 7, (
            f'GET /ai-advisor has {len(tab_buttons)} role="tab" element(s); expected >= 7 '
            "after adding the Frontrunner Builder tab (AC-8)."
        )

    def test_frontrunner_builder_tab_badge_tooltip_does_not_say_advisory_gated(
        self, client, monkeypatch
    ):
        """RULING (team-lead, 2026-07-11, both frreview Nit 1 and frux Item 7
        independently flagged this): the tab badge tooltip previously read
        "Composer write on approval — advisory-gated" — "advisory-gated"
        softens the ONE tab in this SPA whose approve action performs a REAL
        (undeployed) Composer write, unlike every sibling tab which is
        advisory-only end to end. The word must not appear on this badge.
        """
        html = _render_advisor_page(client, monkeypatch)
        idx = html.find('data-testid="frontrunner-builder-tab"')
        assert idx != -1, "frontrunner-builder-tab not found — cannot verify badge copy."
        # The badge <span> is a sibling inside the same <button>; bound the
        # search to the button's own markup (next </button> close).
        button_end = html.find("</button>", idx)
        button_html = html[idx : button_end if button_end != -1 else idx + 500]
        assert "advisory-gated" not in button_html, (
            "The frontrunner-builder tab badge tooltip still contains "
            "'advisory-gated' — this tab's approve action performs a REAL "
            "Composer write (undeployed symphony), unlike every other tab in "
            "the SPA. The tooltip must not soften that into an advisory-only "
            "claim (team-lead ruling, frreview Nit 1 / frux Item 7)."
        )

    def test_frontrunner_builder_tab_badge_tooltip_names_real_write_and_approval(
        self, client, monkeypatch
    ):
        """The replacement copy must convey BOTH facts the old copy obscured:
        this is a real Composer write, and it only happens with operator
        approval. Locked wording (coordinated with frdash, UI-copy owner,
        matching the terse em-dash voice of the 5 sibling badges — e.g.
        "Highest overfitting risk — building from scratch"):
        "Real Composer write — approval required"
        """
        html = _render_advisor_page(client, monkeypatch)
        idx = html.find('data-testid="frontrunner-builder-tab"')
        assert idx != -1, "frontrunner-builder-tab not found — cannot verify badge copy."
        button_end = html.find("</button>", idx)
        button_html = html[idx : button_end if button_end != -1 else idx + 500]
        assert "Real Composer write" in button_html, (
            "The frontrunner-builder tab badge tooltip must name the real "
            "Composer write explicitly — locked wording: "
            "'Real Composer write — approval required'. "
            f"Found button markup: {button_html!r}"
        )
        assert "approval required" in button_html, (
            "The frontrunner-builder tab badge tooltip must state that "
            "approval is required before the write happens — locked wording: "
            "'Real Composer write — approval required'. "
            f"Found button markup: {button_html!r}"
        )


# ===========================================================================
# Group B: route prefetch — database.get_pending_frontrunner_proposals wired
# ===========================================================================


class TestRoutePrefetch:
    def test_ai_advisor_route_calls_get_pending_frontrunner_proposals(self, client, monkeypatch):
        """AC-9-route: the /ai-advisor route must call
        database.get_pending_frontrunner_proposals() to prefetch the tab's cards —
        mirrors the sb_observations prefetch pattern (app.py:4083)."""
        _stub_analytics(monkeypatch)
        _stub_db_observations(monkeypatch)

        called = {"count": 0}

        def _capture(limit=50):
            called["count"] += 1
            return []

        fake_corr = MagicMock()
        fake_corr.compute_pairwise_correlations.return_value = []
        fake_corr.CRISIS_CAVEAT = "caveat"

        with (
            patch.object(
                app_module.database,
                "get_pending_frontrunner_proposals",
                side_effect=_capture,
            ),
            patch.dict("sys.modules", {"advisors.correlation_diagnostic": fake_corr}),
        ):
            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as c:
                c.get("/ai-advisor")

        assert called["count"] >= 1, (
            "GET /ai-advisor did not call database.get_pending_frontrunner_proposals(). "
            "The route must prefetch pending proposals so the tab renders without a "
            "separate round-trip (AC-9-route)."
        )

    def test_pending_proposal_symphony_id_appears_in_rendered_cards(self, client, monkeypatch):
        """A controlled pending row's symphony_id must round-trip into the rendered
        HTML — proves the fetched rows actually reach the template, not just that
        the accessor was called."""
        row = _make_pending_row(symphony_id="sym-round-trip-marker-999")
        html = _render_advisor_page(client, monkeypatch, pending_rows=[row])
        assert "sym-round-trip-marker-999" in html, (
            "A pending frontrunner_proposals row's symphony_id did not appear anywhere "
            "in the rendered /ai-advisor HTML. The route must pass "
            "get_pending_frontrunner_proposals()'s rows to the template, and the "
            "template must render each row as a proposal card (AC-9-route, AC-10-UX)."
        )


# ===========================================================================
# Group C: empty state
# ===========================================================================


class TestEmptyState:
    def test_empty_state_renders_when_no_pending_proposals(self, client, monkeypatch):
        html = _render_advisor_page(client, monkeypatch, pending_rows=[])
        assert 'data-testid="frontrunner-builder-empty-state"' in html, (
            "With zero pending frontrunner_proposals rows, the panel must render "
            'data-testid="frontrunner-builder-empty-state" (mirrors the existing '
            "strategy-builder-empty-state pattern, templates/ai_advisor.html:1783) — "
            "an empty queue is a valid, non-error outcome (AC-10-UX)."
        )

    def test_empty_state_absent_when_pending_proposals_exist(self, client, monkeypatch):
        row = _make_pending_row()
        html = _render_advisor_page(client, monkeypatch, pending_rows=[row])
        assert 'data-testid="frontrunner-builder-empty-state"' not in html, (
            "The empty-state marker must NOT render when pending proposals exist — "
            "showing both an empty-state AND real cards simultaneously is a defect."
        )


# ===========================================================================
# Group D: card anatomy — frontrunner_builder source (incumbent vs candidate)
# ===========================================================================


class TestCardAnatomyFrontrunnerBuilderSource:
    @pytest.fixture
    def html(self, client, monkeypatch):
        row = _make_pending_row(row_id=42, proposal_source="frontrunner_builder")
        return _render_advisor_page(client, monkeypatch, pending_rows=[row])

    def test_cards_container_testid_present(self, html):
        assert 'data-testid="frontrunner-proposal-cards"' in html, (
            'Cards container must carry data-testid="frontrunner-proposal-cards" '
            "(locked contract, AC-10-UX)."
        )

    def test_card_testid_and_id_present(self, html):
        assert 'data-testid="frontrunner-proposal-card"' in html, (
            'Each card must carry data-testid="frontrunner-proposal-card" (AC-10-UX).'
        )
        assert 'id="fr-card-42"' in html, (
            'Each card must carry id="fr-card-{{ p.id }}" — expected id="fr-card-42" '
            "for row_id=42 (locked contract, AC-10-UX)."
        )

    def test_source_badge_reads_frontrunner_builder(self, html):
        idx = html.find('data-testid="proposal-source-badge"')
        assert idx != -1, 'data-testid="proposal-source-badge" not found (AC-10-UX).'
        nearby = html[idx : idx + 300]
        assert "Frontrunner Builder" in nearby, (
            'The source badge for a proposal_source="frontrunner_builder" row must '
            'read "Frontrunner Builder" nearby its testid. Found: '
            f"{nearby[:150]!r} (AC-10-UX)."
        )

    def test_incumbent_and_candidate_calmar_cells_present(self, html):
        assert 'data-testid="fr-incumbent-calmar"' in html, (
            "A frontrunner_builder-source card must render an incumbent Calmar cell "
            '(data-testid="fr-incumbent-calmar") — this file\'s RED contract for the '
            "delta table."
        )
        assert 'data-testid="fr-candidate-calmar"' in html, (
            "A frontrunner_builder-source card must render a candidate Calmar cell "
            '(data-testid="fr-candidate-calmar").'
        )

    def test_incumbent_and_candidate_cagr_cells_present(self, html):
        assert 'data-testid="fr-incumbent-cagr"' in html
        assert 'data-testid="fr-candidate-cagr"' in html

    def test_incumbent_and_candidate_mdd_cells_present(self, html):
        assert 'data-testid="fr-incumbent-mdd"' in html
        assert 'data-testid="fr-candidate-mdd"' in html

    def test_node_count_delta_cell_present_with_value(self, html):
        idx = html.find('data-testid="fr-node-count-delta"')
        assert idx != -1, (
            "A frontrunner_builder-source card must render "
            'data-testid="fr-node-count-delta" (metrics_json["node_count_delta"] = -6 '
            "in this test's fixture)."
        )
        nearby = html[idx : idx + 200]
        assert "-6" in nearby or "6" in nearby, (
            f"The node-count-delta cell must render the fixture's node_count_delta "
            f"value (-6) in some form. Found nearby: {nearby[:150]!r}."
        )

    def test_approve_and_reject_buttons_present(self, html):
        assert 'data-testid="fr-approve-btn"' in html, (
            'A pending card must render an Approve button (data-testid="fr-approve-btn") '
            "(AC-9-route, AC-10-UX)."
        )
        assert 'data-testid="fr-reject-btn"' in html, (
            'A pending card must render a Reject button (data-testid="fr-reject-btn").'
        )


# ===========================================================================
# Group E: card anatomy — strategy_builder_retrofit source (candidate-only)
# ===========================================================================


class TestCardAnatomyRetrofitSource:
    @pytest.fixture
    def html(self, client, monkeypatch):
        row = _make_pending_row(row_id=7, proposal_source="strategy_builder_retrofit")
        return _render_advisor_page(client, monkeypatch, pending_rows=[row])

    def test_source_badge_reads_strategy_builder_retrofit(self, html):
        idx = html.find('data-testid="proposal-source-badge"')
        assert idx != -1, 'data-testid="proposal-source-badge" not found (AC-10-UX).'
        nearby = html[idx : idx + 300]
        assert "Strategy Builder" in nearby, (
            'The source badge for a proposal_source="strategy_builder_retrofit" row must '
            "distinguish itself from the plain frontrunner_builder badge text — expected "
            f'"Strategy Builder" (e.g. "Strategy Builder (retrofit)") nearby. Found: '
            f"{nearby[:150]!r} (AC-10-UX)."
        )

    def test_incumbent_cells_absent_for_retrofit_source(self, html):
        """strategy_builder_engine.py's _persist_survivor call to
        insert_frontrunner_proposal (line 702-711) supplies
        metrics_json={"cagr","sharpe","calmar","max_drawdown"} — verified directly,
        NO incumbent_* keys. The card must not render an incumbent column/row for
        these rows (source-branching, not just blank cells)."""
        card_start = html.find('id="fr-card-7"')
        assert card_start != -1, 'id="fr-card-7" not found — cannot scope the check.'
        card_html = html[card_start : card_start + 4000]
        assert 'data-testid="fr-incumbent-calmar"' not in card_html, (
            "A strategy_builder_retrofit-source card rendered an incumbent Calmar cell "
            "despite having no incumbent_calmar key in its metrics_json — the card must "
            "branch on proposal_source and omit the incumbent column entirely, not "
            "render a blank/None cell (AC-10-UX source-branching)."
        )
        assert 'data-testid="fr-incumbent-cagr"' not in card_html
        assert 'data-testid="fr-incumbent-mdd"' not in card_html

    def test_candidate_calmar_still_present_for_retrofit_source(self, html):
        """The candidate-only metrics (calmar/cagr/max_drawdown) ARE present in the
        retrofit row's metrics_json — the card must still surface them even without
        an incumbent to compare against."""
        card_start = html.find('id="fr-card-7"')
        assert card_start != -1, 'id="fr-card-7" not found — cannot scope the check.'
        card_html = html[card_start : card_start + 4000]
        assert 'data-testid="fr-candidate-calmar"' in card_html, (
            "A strategy_builder_retrofit-source card must still render its own Calmar "
            "value (metrics_json['calmar'] = 1.9 in this test's fixture) even without "
            "an incumbent to compare against (AC-10-UX)."
        )


# ===========================================================================
# Group F: inverted no-GET-<a href> adversarial contract
# ===========================================================================


class TestApproveRejectAdversarialContract:
    """Unlike every other advisory-only tab in this SPA (AC-4.1 HARD: 'NO apply/
    accept/deploy/trade buttons' comment, templates/ai_advisor.html:2029), the
    Frontrunner Builder tab HAS real write-actions (approve creates an undeployed
    Composer symphony). This is the deliberate INVERSION of the usual
    advisory-only contract for THIS tab only — but the actions must still never
    be GET-triggerable (no <a href> to the write routes, no auto-firing forms).
    All 3 tests in this class exist to catch a regression toward a plain link,
    which would let a crawler/prefetcher/accidental click silently trigger a
    Composer create."""

    @pytest.fixture
    def html(self, client, monkeypatch):
        row = _make_pending_row(row_id=1)
        return _render_advisor_page(client, monkeypatch, pending_rows=[row])

    def test_no_anchor_href_points_at_the_approve_or_reject_routes(self, html):
        anchors_to_proposal_routes = re.findall(
            r'<a\b[^>]*href\s*=\s*["\'][^"\']*proposal/(?:approve|reject)[^"\']*["\']',
            html,
            re.IGNORECASE,
        )
        assert not anchors_to_proposal_routes, (
            f"Found {len(anchors_to_proposal_routes)} <a href> element(s) pointing at "
            "/ai-advisor/proposal/approve or /reject — these routes create/mutate real "
            "state and MUST NOT be GET-triggerable via a navigable link. Use "
            '<button type="button" onclick="frApprove(...)"> / frReject(...) instead, '
            f"matching the POST+CSRF dispatch pattern. Found: {anchors_to_proposal_routes}"
        )

    def test_approve_and_reject_are_button_type_button_not_submit(self, html):
        for testid in ("fr-approve-btn", "fr-reject-btn"):
            idx = html.find(f'data-testid="{testid}"')
            if idx == -1:
                pytest.fail(f'data-testid="{testid}" not found — cannot verify tag type.')
            before = html[max(0, idx - 300) : idx]
            last_open = before.rfind("<")
            tag_snippet = before[last_open:]
            assert tag_snippet.lstrip("<").startswith("button"), (
                f"The {testid} element must be a <button>, not an <a> or <input>. "
                f"Found opening tag context: {tag_snippet[:80]!r}."
            )
            # Must not be type="submit" (no bare HTML <form> action affordance —
            # the dispatch is JS fetch + X-CSRF-Token, matching every other
            # write-capable control already in this template, e.g. sb-run-btn).
            forward = html[idx : idx + 300]
            assert 'type="submit"' not in forward, (
                f'{testid} must not be type="submit" — dispatch via JS fetch + CSRF '
                "header, not a native form submission."
            )

    def test_approve_and_reject_onclick_call_the_named_js_functions(self, html):
        """frdash's own message (0214e094) names the static/ai_advisor.js functions
        as frRunBuild/frApprove/frReject — assert the buttons actually wire to them."""
        approve_idx = html.find('data-testid="fr-approve-btn"')
        reject_idx = html.find('data-testid="fr-reject-btn"')
        assert approve_idx != -1 and reject_idx != -1, (
            "fr-approve-btn / fr-reject-btn not found — cannot verify onclick wiring."
        )
        approve_ctx = html[max(0, approve_idx - 200) : approve_idx + 200]
        reject_ctx = html[max(0, reject_idx - 200) : reject_idx + 200]
        assert "frApprove(" in approve_ctx, (
            "fr-approve-btn must call frApprove(...) via onclick — not found nearby: "
            f"{approve_ctx!r}"
        )
        assert "frReject(" in reject_ctx, (
            f"fr-reject-btn must call frReject(...) via onclick — not found nearby: {reject_ctx!r}"
        )


# ===========================================================================
# Group G: no `| safe` on untrusted proposal-derived fields
# ===========================================================================


class TestNoSafeFilterOnUntrustedFields:
    def test_symphony_id_xss_payload_is_escaped_not_rendered_raw(self, client, monkeypatch):
        payload_symphony_id = "sym-<script>alert(1)</script>-marker"
        row = _make_pending_row(symphony_id=payload_symphony_id)
        html = _render_advisor_page(client, monkeypatch, pending_rows=[row])
        assert "<script>alert(1)</script>" not in html, (
            "An unescaped <script> tag from a proposal's symphony_id reached the "
            "rendered page — this is a stored-XSS vector. Jinja's default autoescape "
            "must not be bypassed with | safe on proposal-derived fields (AC-10-UX "
            "security)."
        )
        assert "&lt;script&gt;" in html, (
            "The symphony_id XSS payload was not found HTML-escaped anywhere in the "
            "page either — expected Jinja's default autoescape to render "
            "'&lt;script&gt;'. If the raw marker text is entirely absent, the "
            "fixture's symphony_id never reached the template at all (a route-wiring "
            "gap, not an escaping pass)."
        )

    def test_error_message_xss_payload_is_escaped(self, client, monkeypatch):
        payload = "<img src=x onerror=alert(1)>"
        row = _make_pending_row(row_id=99, error_message=payload)
        html = _render_advisor_page(client, monkeypatch, pending_rows=[row])
        assert "<img src=x onerror=alert(1)>" not in html, (
            "An unescaped <img onerror=...> payload from a proposal's error_message "
            "reached the rendered page — stored-XSS vector via a rejected/errored "
            "proposal's error text (AC-10-UX security)."
        )

    def test_frontrunner_panel_source_contains_no_safe_filter(self):
        """Static source-scan of the frontrunner-builder panel block in
        templates/ai_advisor.html: zero `| safe` occurrences anywhere in that
        block. Mirrors the R2 guardrail already documented for .obs-raw-preview
        (project CLAUDE.md: 'no | safe, no raw JSON exposure') — extended here
        to the Frontrunner Builder panel specifically, since it is a NEW panel
        with no historical | safe usage to inherit.
        """
        template_path = _REPO_ROOT / "templates" / "ai_advisor.html"
        source = template_path.read_text(encoding="utf-8")
        start = source.find('id="tab-panel-frontrunner-builder"')
        if start == -1:
            pytest.fail(
                "tab-panel-frontrunner-builder not found in templates/ai_advisor.html — "
                "cannot scope the | safe scan (the panel does not exist yet)."
            )
        end = source.find("{# /tab-panel-", start)
        panel_source = source[start : end if end != -1 else start + 20000]
        assert "| safe" not in panel_source and "|safe" not in panel_source, (
            "The frontrunner-builder tab panel block contains a `| safe` filter. "
            "No field derived from a frontrunner_proposals row (symphony_id, "
            "error_message, candidate_tree/metrics_json preview text) may bypass "
            "Jinja's default autoescape — all such fields must render through the "
            "default escaping path (AC-10-UX security, mirrors the .obs-raw-preview "
            "R2 guardrail)."
        )


# ===========================================================================
# Group H: collapsed <details> raw-preview — NOT open by default
# ===========================================================================


class TestCollapsedRawPreview:
    @pytest.fixture
    def html(self, client, monkeypatch):
        row = _make_pending_row(row_id=1)
        return _render_advisor_page(client, monkeypatch, pending_rows=[row])

    def test_raw_preview_details_element_present(self, html):
        assert 'data-testid="fr-raw-preview"' in html, (
            "A pending card must render a collapsed raw-preview <details "
            'data-testid="fr-raw-preview"> (candidate_tree / metrics_json preview) — '
            "mirrors the existing rules-collapsible <details> pattern "
            "(templates/ai_advisor.html:1998) so the card never dumps a raw JSON wall "
            'onto the page by default (team-lead\'s "no debug-dump JSON wall" '
            "guardrail, AC-10-UX)."
        )

    def test_raw_preview_details_is_not_open_by_default(self, html):
        idx = html.find('data-testid="fr-raw-preview"')
        assert idx != -1, 'data-testid="fr-raw-preview" not found — cannot verify collapsed state.'
        # Locate the enclosing <details ...> opening tag (search backward for '<details').
        before = html[max(0, idx - 300) : idx]
        details_open = before.rfind("<details")
        assert details_open != -1, (
            'data-testid="fr-raw-preview" was not found inside a <details> element — '
            "the raw preview must be wrapped in <details> so it is collapsible."
        )
        opening_tag_end = html.find(">", idx)
        opening_tag = html[max(0, idx - 300) + details_open : opening_tag_end + 1]
        assert not re.search(r"\bopen\b", opening_tag), (
            f"The fr-raw-preview <details> element carries an 'open' attribute: "
            f"{opening_tag!r}. It must be collapsed by default — omit the 'open' "
            "attribute entirely (AC-10-UX, no debug-dump JSON wall on page load)."
        )
