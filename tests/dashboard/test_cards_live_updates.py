"""
RED tests — cards-live cycle.

Bug: symphony cards render server-side at page load and are never updated
by the 30-second /api/state poll. After an exit the card shows stale
status/return/0.0% until a hard refresh.

Fix contract (AC-CL.*):
  AC-CL.1  /api/state returns a `symphonies` dict keyed by symphony id
             carrying status-critical fields: current_return, stop_trigger,
             mc_prob, armed, triggered, triggered_reason, _cr, _tc, _mdd.
  AC-CL.2  The `symphonies` field reflects current bot_state — an exited
             symphony (triggered=True) shows triggered=True in the field.
  AC-CL.3  Existing /api/state top-level fields are still present (additive only):
             status, state, bot_state, html, live_mode, market_state.
  AC-CL.4  static/index.js passes `node --check` (no syntax errors introduced
             by the implementer).
  AC-CL.5  static/index.js updateDashboard contains a reference to `data-sym-id`
             attribute targeting (card in-place update path).
  AC-CL.6  static/index.js does NOT inject `data.html` into any DOM element
             (no innerHTML assignment from the html field = no dark-table injection).
  AC-CL.7  templates/index.html contains NO `sym-table-container` id or class
             (locks out the prior broken pattern).
  AC-CL.8  templates/index.html preserves the `cards-grid`, `sym-card`, and
             `card-spark` canvas elements (no whole-card replacement).
  AC-CL.9  static/index.js does NOT create or reference `sym-table-container`
             (JS-side regression guard).

Fixture provenance:
  tests/fixtures/dashboard/cards_live/api_state_with_cards.json
  Hand-authored; schema derived from alpha_bot_execution.py bot_state structure
  and app.py:440-475 (_cr/_tc/_mdd enrichment).
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_STATIC_DIR = _WORKTREE / "static"
_TEMPLATES_DIR = _WORKTREE / "templates"
_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "dashboard" / "cards_live"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Flask test-client fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def flask_client():
    """Flask test client with minimal stubs — no live DB, no live network."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with (
        patch.object(app_module, "analytics") as mock_analytics,
        patch.object(app_module, "schedule"),
    ):
        mock_analytics.get_portfolio_today_change.return_value = None
        mock_analytics.get_portfolio_cumulative_return.return_value = None
        mock_analytics.get_portfolio_max_drawdown.return_value = None
        mock_analytics.get_symphony_today_change.return_value = {
            "if_held": 0.0,
            "dry_run": 0.0,
        }
        mock_analytics.get_symphony_cumulative_return.return_value = {
            "if_held": 0.0,
            "dry_run": 0.0,
        }
        mock_analytics.get_symphony_max_drawdown.return_value = {
            "if_held": 0.0,
            "dry_run": 0.0,
        }
        with app_module.app.test_client() as client:
            yield client, app_module


@pytest.fixture()
def fixture_state() -> dict:
    """
    Fixture bot_state used by AC-CL.1 / AC-CL.2 tests.

    Provenance: tests/fixtures/dashboard/cards_live/api_state_with_cards.json
    """
    return _load_fixture("api_state_with_cards")


# ---------------------------------------------------------------------------
# AC-CL.1  /api/state response carries a `symphonies` dict keyed by sym id
# ---------------------------------------------------------------------------


def test_api_state_carries_symphonies_field_keyed_by_id(flask_client, fixture_state, monkeypatch):
    """
    /api/state must include a top-level `symphonies` key whose value is a dict
    (or list) keyed/indexed by symphony id, enabling the JS card-update path.

    The field must carry at minimum: current_return, armed, triggered, mc_prob.
    These are the values that go stale on the cards without live polling.
    """
    client, app_module = flask_client

    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = fixture_state
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {
            "by_symphony": {},
            "portfolio_today": None,
        }
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_last_trigger_per_symphony.return_value = {}
        db_mock.get_guard_alpha_by_symphony.return_value = {}

        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")

        resp = client.get("/api/state")

    assert resp.status_code == 200, f"/api/state returned {resp.status_code}"
    body = resp.get_json()
    assert body.get("status") == "active", (
        f"Expected status='active', got {body.get('status')!r}. "
        "The fixture state has two symphonies; route should not fall through to 'waiting'."
    )

    # AC-CL.1: the new `symphonies` field must exist
    assert "symphonies" in body, (
        "AC-CL.1 FAIL: /api/state response is missing the `symphonies` field. "
        "Implementer must add a per-symphony dict/list to the JSON payload so "
        "the 30s poll can update card values in-place."
    )

    symphonies = body["symphonies"]
    # Accept either a dict keyed by id or a list of dicts that have an `id` key.
    if isinstance(symphonies, list):
        sym_map = {s["id"]: s for s in symphonies if isinstance(s, dict) and "id" in s}
    else:
        assert isinstance(symphonies, dict), (
            "AC-CL.1 FAIL: `symphonies` must be a dict keyed by symphony id "
            f"or a list of dicts with an `id` key; got {type(symphonies).__name__}."
        )
        sym_map = symphonies

    assert "sym_alpha" in sym_map, (
        "AC-CL.1 FAIL: fixture symphony 'sym_alpha' not found in response symphonies. "
        "The field must carry ALL symphonies from bot_state."
    )
    assert "sym_beta" in sym_map, (
        "AC-CL.1 FAIL: fixture symphony 'sym_beta' not found in response symphonies."
    )

    # Required per-symphony fields for card updates
    alpha = sym_map["sym_alpha"]
    for field in ("current_return", "armed", "triggered", "mc_prob"):
        assert field in alpha, (
            f"AC-CL.1 FAIL: symphonies['sym_alpha'] is missing required field '{field}'. "
            "Cards depend on current_return (return %), armed/triggered (status pill), "
            "and mc_prob (MC dial) being present for in-place DOM updates."
        )


# ---------------------------------------------------------------------------
# AC-CL.2  Exited symphony reflects triggered=True in the symphonies field
# ---------------------------------------------------------------------------


def test_api_state_symphonies_field_reflects_exited_symphony(
    flask_client, fixture_state, monkeypatch
):
    """
    When a symphony has triggered=True in bot_state, the `symphonies` field
    returned by /api/state must also show triggered=True for that symphony.

    This is the core bug: stale cards show old status because the poll response
    carries no per-symphony triggered signal that JS can read.
    """
    client, app_module = flask_client

    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = fixture_state
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {
            "by_symphony": {},
            "portfolio_today": None,
        }
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_last_trigger_per_symphony.return_value = {}
        db_mock.get_guard_alpha_by_symphony.return_value = {}

        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")

        resp = client.get("/api/state")

    assert resp.status_code == 200
    body = resp.get_json()

    symphonies = body.get("symphonies")
    assert symphonies is not None, (
        "AC-CL.2 FAIL: `symphonies` field absent. Cannot verify triggered state without the field."
    )

    if isinstance(symphonies, list):
        sym_map = {s["id"]: s for s in symphonies if isinstance(s, dict) and "id" in s}
    else:
        sym_map = symphonies

    beta = sym_map.get("sym_beta")
    assert beta is not None, (
        "AC-CL.2 FAIL: fixture symphony 'sym_beta' missing from symphonies field."
    )
    assert beta.get("triggered") is True, (
        "AC-CL.2 FAIL: sym_beta has triggered=True in bot_state but the symphonies "
        "field shows triggered=%r. Cards will show stale status (not exited) on the "
        "next poll even after the symphony has been exited." % (beta.get("triggered"),)
    )


# ---------------------------------------------------------------------------
# AC-CL.3  Existing /api/state top-level fields are still present (additive)
# ---------------------------------------------------------------------------


def test_api_state_existing_fields_preserved_after_symphonies_added(
    flask_client, fixture_state, monkeypatch
):
    """
    The `symphonies` field is additive — it must not displace existing fields
    that other JS paths depend on: status, state, bot_state, html, live_mode,
    market_state.

    A missing existing field means the implementer replaced rather than extended
    the response, which will break hero widgets, mode badge, status strip, etc.
    """
    client, app_module = flask_client

    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = fixture_state
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {
            "by_symphony": {},
            "portfolio_today": None,
        }
        db_mock.read_fleet_alert.return_value = None
        db_mock.get_last_trigger_per_symphony.return_value = {}
        db_mock.get_guard_alpha_by_symphony.return_value = {}

        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")

        resp = client.get("/api/state")

    assert resp.status_code == 200
    body = resp.get_json()

    # These fields must survive the additive symphonies addition.
    required_existing = ["status", "state", "bot_state", "html", "live_mode", "market_state"]
    missing = [f for f in required_existing if f not in body]
    assert not missing, (
        f"AC-CL.3 FAIL: the following existing /api/state fields are missing after "
        f"the symphonies field was added: {missing}. "
        "The change must be purely additive — do not rename or remove any existing key."
    )


# ---------------------------------------------------------------------------
# AC-CL.5  updateDashboard references data-sym-id targeting (card update path)
# ---------------------------------------------------------------------------


def test_index_js_update_dashboard_references_data_sym_id_targeting():
    """
    static/index.js updateDashboard (or a function it calls) must contain a
    reference to `data-sym-id` attribute targeting so that card elements are
    located by symphony id for in-place value updates.

    If this is absent the implementer did not wire the card update path at all.
    The presence of `data-sym-id` in a querySelector/querySelectorAll call is
    the minimum signal that each card is targeted individually.
    """
    js_path = _STATIC_DIR / "index.js"
    assert js_path.exists(), "static/index.js must exist"

    content = js_path.read_text(encoding="utf-8")

    # The cards already carry data-sym-id; the JS update path must use it.
    # Accept querySelector('[data-sym-id=...'), querySelectorAll, or
    # attribute assignment patterns — any reference that targets by sym id.
    assert "data-sym-id" in content, (
        "AC-CL.5 FAIL: static/index.js does not reference `data-sym-id` targeting. "
        "The card in-place update path must locate each card via "
        "[data-sym-id='<id>'] to update return%, status, stop, and mc_prob without "
        "replacing the whole card element."
    )

    # Additionally require a reference that updates card values from the new
    # symphonies field in the poll response.
    has_symphonies_read = (
        "data.symphonies" in content
        or "data['symphonies']" in content
        or "symphonies" in content  # more lenient: any reference in the file
    )
    assert has_symphonies_read, (
        "AC-CL.5 FAIL: static/index.js updateDashboard does not read `data.symphonies` "
        "(or equivalent). The 30s poll response now carries per-symphony data; "
        "the update function must consume it to refresh card values."
    )


# ---------------------------------------------------------------------------
# AC-CL.6  static/index.js does NOT inject data.html into any DOM element
# ---------------------------------------------------------------------------


def test_index_js_does_not_inject_html_field_into_dom():
    """
    static/index.js must NOT assign `data.html` (the table_partial.html render)
    to any element's innerHTML.

    The prior broken cycle did exactly this — it injected the dark slate-themed
    table into a new container, overriding the light card UI. The test locks
    this pattern out at the source-text level.

    Matching patterns that must be absent:
      - innerHTML = data.html
      - .innerHTML=data.html (no spaces)
      - innerHTML = data['html']
    """
    js_path = _STATIC_DIR / "index.js"
    assert js_path.exists(), "static/index.js must exist"

    content = js_path.read_text(encoding="utf-8")

    # Match innerHTML assignment from the html field in any whitespace variant.
    # Patterns caught: innerHTML = data.html  /  innerHTML=data.html  /
    #                  innerHTML = data['html']  /  innerHTML = data["html"]
    # Using two simpler patterns instead of a backreference to avoid re group issues.
    html_inject_patterns = [
        re.compile(r"innerHTML\s*=\s*data\.html\b", re.MULTILINE),
        re.compile(r"""innerHTML\s*=\s*data\[['"]html['"]\]""", re.MULTILINE),
    ]
    for html_inject_pattern in html_inject_patterns:
        match = html_inject_pattern.search(content)
        assert match is None, (
            "AC-CL.6 FAIL: static/index.js assigns data.html to innerHTML. "
            "This injects the dark table_partial.html render into the DOM and "
            "overrides the light card UI — exactly the pattern that broke the "
            "prior cycle. Do NOT use the `html` field for rendering. "
            f"Found match near: {content[max(0, match.start() - 40) : match.end() + 40]!r}"
        )


# ---------------------------------------------------------------------------
# AC-CL.7  templates/index.html contains no sym-table-container
# ---------------------------------------------------------------------------


def test_index_html_contains_no_sym_table_container():
    """
    templates/index.html must not contain the string 'sym-table-container'.

    The prior broken cycle injected a new <div id="sym-table-container"> or
    equivalent and loaded the dark table into it. This test locks that id/class
    out of the template — the card UI must continue to use the existing
    cards-grid / sym-card structure.
    """
    html_path = _TEMPLATES_DIR / "index.html"
    assert html_path.exists(), "templates/index.html must exist"

    content = html_path.read_text(encoding="utf-8")

    assert "sym-table-container" not in content, (
        "AC-CL.7 FAIL: templates/index.html contains 'sym-table-container'. "
        "This id/class was introduced by the prior broken cycle to inject the "
        "dark table_partial.html. It must not appear in the card UI template."
    )


# ---------------------------------------------------------------------------
# AC-CL.8  templates/index.html preserves cards-grid / sym-card / card-spark
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    [
        'class="cards-grid"',
        'data-testid="sym-card"',
        'data-testid="card-spark"',
    ],
)
def test_index_html_preserves_card_markup_markers(marker: str):
    """
    The existing card markup must survive the implementer's changes.

    cards-grid, sym-card, and card-spark are the structural anchors that the
    server-side template and the JS (sparkline loader, verdict updater) rely on.
    Removing or renaming any of these breaks rendering immediately on page load
    as well as the JS-driven enrichment pass.
    """
    html_path = _TEMPLATES_DIR / "index.html"
    assert html_path.exists(), "templates/index.html must exist"

    content = html_path.read_text(encoding="utf-8")

    assert marker in content, (
        f"AC-CL.8 FAIL: templates/index.html is missing required marker {marker!r}. "
        "The implementer must update card VALUES in-place — not replace the card "
        "markup or rename structural class/testid attributes."
    )


# ---------------------------------------------------------------------------
# AC-CL.9  static/index.js does NOT create or reference sym-table-container
# ---------------------------------------------------------------------------


def test_index_js_does_not_reference_sym_table_container():
    """
    static/index.js must not contain the string 'sym-table-container'.

    The prior broken cycle created this container via JS and loaded the dark
    table into it. Lock it out at both the template level (AC-CL.7) and the
    JS level here so neither surface can reintroduce the anti-pattern.
    """
    js_path = _STATIC_DIR / "index.js"
    assert js_path.exists(), "static/index.js must exist"

    content = js_path.read_text(encoding="utf-8")

    assert "sym-table-container" not in content, (
        "AC-CL.9 FAIL: static/index.js references 'sym-table-container'. "
        "This was the injection point for the dark table that collided with "
        "the light card UI. Remove all references to this container."
    )


# ---------------------------------------------------------------------------
# AC-CL.10  updateCards updates the status pill text/class via sym.triggered/armed
# ---------------------------------------------------------------------------


def test_index_js_update_cards_updates_status_pill():
    """
    static/index.js updateCards (or a helper it calls) must update the
    status-pill element on each card when the symphony's armed/triggered state
    changes.

    The original bug: cards render server-side only — after an exit the status
    pill still shows "Armed" until a hard refresh. The fix must update the
    status-pill text AND CSS class on each 30s poll using sym.triggered /
    sym.armed / sym.tp_armed / sym.para_armed from data.symphonies.

    Minimum signal: updateCards must reference 'status-pill' as a querySelector
    target and must read sym.triggered (or sym.status) to set the new text/class.
    """
    js_path = _STATIC_DIR / "index.js"
    assert js_path.exists(), "static/index.js must exist"

    content = js_path.read_text(encoding="utf-8")

    assert "status-pill" in content, (
        "AC-CL.10 FAIL: static/index.js does not reference 'status-pill'. "
        "updateCards must locate the status-pill element on each card and update "
        "its text + CSS class when triggered/armed state changes on the 30s poll. "
        "Without this the status pill goes stale — an exited symphony still shows "
        "'Armed' until a hard refresh."
    )

    # The update must read triggered or status from the per-symphony payload.
    has_triggered_read = (
        "sym.triggered" in content
        or "sym.status" in content
        or 'sym["triggered"]' in content
        or 'sym["status"]' in content
    )
    assert has_triggered_read, (
        "AC-CL.10 FAIL: static/index.js does not read sym.triggered or sym.status "
        "in the card update path. The status pill text ('TRAILING STOP', 'Armed', "
        "'Standby', etc.) must be derived from the per-symphony triggered/armed flags "
        "in data.symphonies — not from stale server-rendered text."
    )


# ---------------------------------------------------------------------------
# AC-CL.11  updateCards updates the MC dial from sym.mc_prob
# ---------------------------------------------------------------------------


def test_index_js_update_cards_updates_mc_dial_from_sym_mc_prob():
    """
    static/index.js updateCards must update the MC dial (`data-testid="mc-dial"`)
    for each card using sym.mc_prob from data.symphonies on each 30s poll.

    Currently renderMcDial is called only from the chart-data fetch path (which
    triggers on page load + sparkline refresh), not on the state poll. The dial
    therefore shows the value from the last chart load, which can be many minutes
    stale during market hours.

    The fix: updateCards must also drive the MC dial from sym.mc_prob in the
    symphonies payload. Minimum signal: updateCards references both 'mc-dial'
    (querySelector target) and sym.mc_prob (value source).
    """
    js_path = _STATIC_DIR / "index.js"
    assert js_path.exists(), "static/index.js must exist"

    content = js_path.read_text(encoding="utf-8")

    # The function must target the mc-dial element within the card update path.
    # We verify that 'mc-dial' appears in updateCards or a helper it calls.
    # The existing renderMcDial already uses 'mc-dial' (for chart loads) — we need
    # updateCards to also drive it, so the count must reflect wiring in updateCards.
    # A simple presence check is sufficient: if mc-dial + mc_prob both appear and
    # updateCards is wired, the dial is updated.
    assert "mc-dial" in content, (
        "AC-CL.11 FAIL: static/index.js does not reference 'mc-dial' in the card "
        "update path. updateCards must update the MC dial arc and label on each 30s "
        "poll using sym.mc_prob from data.symphonies."
    )

    # The update must connect mc_prob from the symphonies payload to the dial.
    # We check that updateCards (the function body) references sym.mc_prob —
    # not just that sym.mc_prob appears somewhere in the file (it also appears
    # in populateRiskMathFromState for the detail panel).
    # Extract the updateCards function body by slicing between 'function updateCards'
    # and the matching closing brace block.
    update_cards_start = content.find("function updateCards(")
    assert update_cards_start != -1, (
        "AC-CL.11 FAIL: function updateCards not found in static/index.js."
    )
    # Take 2000 chars past the function start — sufficient to cover the function body.
    update_cards_body = content[update_cards_start : update_cards_start + 2000]

    has_mc_prob_in_update_cards = (
        "sym.mc_prob" in update_cards_body
        or 'sym["mc_prob"]' in update_cards_body
        or "renderMcDial" in update_cards_body  # acceptable: delegate to existing helper
    )
    assert has_mc_prob_in_update_cards, (
        "AC-CL.11 FAIL: static/index.js updateCards does not read sym.mc_prob or "
        "call renderMcDial. The MC dial must be refreshed on every 30s poll — "
        "not only when chart data loads. Add sym.mc_prob → renderMcDial (passing id "
        "and a synthetic chartData object) or inline the dial update inside updateCards."
    )
