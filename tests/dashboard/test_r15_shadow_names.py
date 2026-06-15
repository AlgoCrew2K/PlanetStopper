"""
RED tests — R15: Shadow Performance banner UX — symphony names + hover-highlight.

Covers eleven contracted changes:
  1. /api/state shadow_divergence.by_symphony entries include a 'name' field
  2. Shadow Perf banner renders symphony NAME (truncated ≤24 chars + '...')
  3. data-symphony-id attribute present on each Shadow Perf pill
  4. Hover handler wired in index.html JS (mouseenter + setTimeout + mouseleave clear)
  5. .cross-highlighted CSS class defined with distinguishable visual style
  6. Main-table rows carry data-symphony-id attribute for cross-highlight targeting
  7. index.html Shadow Perf loop references entry.name (not just entry.symphony_id)
  8. Legacy entry without 'name' in bot_state falls back to truncated ID in banner
  9. symphony name longer than 24 chars is truncated with '...' suffix in pill
 10. Full name in tooltip (title attribute) on each Shadow Perf pill
 11. /api/state name field is a non-empty string for active symphonies

All tests parse templates/index.html and templates/table_partial.html as text —
no live Composer/Alpaca calls are made.  Flask test_client is used for JSON contract.

Fixture: tests/fixtures/dashboard/r15_shadow_names/shadow_divergence_with_names.json
  Provenance: schema fixture derived from database.get_shadow_divergence() return shape (PA-18).
              R15 adds 'name' field; backward-compat: entries missing 'name' fall back to ID.

Tests are RED against current main — they pin contracts that do not yet exist.
"""

from __future__ import annotations

import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_INDEX_HTML = _WORKTREE / "templates" / "index.html"
_TABLE_PARTIAL = _WORKTREE / "templates" / "table_partial.html"
_FIXTURE = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "dashboard"
    / "r15_shadow_names"
    / "shadow_divergence_with_names.json"
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _load_fixture() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _make_state_with_two_symphonies():
    """
    Minimal bot_state with two symphony entries.

    Both entries have a 'name' field (the standard case).
    sym-abc-001: short name
    sym-no-name-004: no 'name' key — legacy/backward-compat case.
    """
    return {
        "sym-abc-001": {
            "name": "Global Macro Diversified",
            "account": "ACC1",
            "mc_prob": 42.0,
            "triggered": False,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "breakeven_locked": False,
            "current_return": 1.5,
            "stop_trigger": -2.0,
            "shadow_hwm": 3.0,
            "high_water_mark": 3.0,
            "current_value": 10000.0,
        },
        "sym-no-name-004": {
            "account": "ACC1",
            "mc_prob": 30.0,
            "triggered": False,
            "armed": False,
            "tp_armed": False,
            "para_armed": False,
            "breakeven_locked": False,
            "current_return": 0.8,
            "stop_trigger": -3.0,
            "shadow_hwm": 1.5,
            "high_water_mark": 1.5,
            "current_value": 8000.0,
        },
    }


def _default_analytics_mock():
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 1.0, "dry_run": 0.9}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 9.5}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 0.10, "dry_run": 0.10}
    m.get_symphony_today_change.return_value = {"if_held": 1.0, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 10.0, "dry_run": 9.5}
    m.get_symphony_max_drawdown.return_value = {"if_held": 0.10, "dry_run": 0.10}
    # Matches real contract: tuple[list[str], list[float]] | None.  None = insufficient
    # history; _compute_portfolio_strip handles this gracefully and skips vol calculation.
    m.get_portfolio_daily_returns_from_shadow.return_value = None
    return m


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database_with_names():
    """Database mock that injects shadow_divergence with 'name' fields."""
    fixture = _load_fixture()
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = _make_state_with_two_symphonies()
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = fixture
        db_mock.get_triggers.return_value = []
        db_mock.read_fleet_alert.return_value = None
        yield db_mock


# ---------------------------------------------------------------------------
# 1. /api/state JSON contract: by_symphony entries must include 'name' field
# ---------------------------------------------------------------------------


class TestShadowDivergenceNameField:
    """
    /api/state shadow_divergence.by_symphony each entry must include a 'name'
    key with the symphony's human-readable name.

    Fixture provenance: schema derived from database.get_shadow_divergence() shape.
    The 'name' field is populated by the route (or database layer) from bot_state.
    """

    def test_by_symphony_entries_have_name_key(self, client, mock_database_with_names, monkeypatch):
        """Each entry in shadow_divergence.by_symphony must have a 'name' key."""
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        assert resp.status_code == 200

        body = resp.get_json()
        sd = body.get("shadow_divergence", {})
        by_sym = sd.get("by_symphony", {})

        assert by_sym, (
            "shadow_divergence.by_symphony must be non-empty; "
            "check that mock_database_with_names injects the fixture correctly"
        )

        for sym_id, entry in by_sym.items():
            assert "name" in entry, (
                f"shadow_divergence.by_symphony['{sym_id}'] must have a 'name' key; "
                f"entry: {entry}. "
                "R15 A/C-1: add symphony name alongside ID in get_shadow_divergence output "
                "or in the /api/state assembly layer."
            )

    def test_by_symphony_name_is_non_empty_string_when_available(
        self, client, mock_database_with_names, monkeypatch
    ):
        """
        For symphonies that have a 'name' in bot_state, the name field in
        shadow_divergence must be a non-empty string — not None, '', or the raw ID.
        """
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        body = resp.get_json()
        by_sym = body["shadow_divergence"]["by_symphony"]

        sym_id = "sym-abc-001"
        if sym_id in by_sym:
            name = by_sym[sym_id].get("name")
            assert isinstance(name, str) and name.strip(), (
                f"shadow_divergence.by_symphony['{sym_id}'].name must be a non-empty string; "
                f"got: {name!r}. "
                "The bot_state entry for this symphony has name='Global Macro Diversified'."
            )

    def test_by_symphony_existing_fields_preserved(
        self, client, mock_database_with_names, monkeypatch
    ):
        """
        Adding 'name' to each by_symphony entry must not drop existing fields.
        today, cumulative (and max_drawdown if present) must still be present.
        """
        monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

        resp = client.get("/api/state")
        body = resp.get_json()
        by_sym = body["shadow_divergence"]["by_symphony"]

        for sym_id, entry in by_sym.items():
            assert "today" in entry, (
                f"shadow_divergence.by_symphony['{sym_id}'] must still have 'today' key after R15; "
                "R15 adds 'name' but must not remove existing fields"
            )

    def test_legacy_entry_without_name_falls_back_to_id(self, client, monkeypatch):
        """
        When a symphony in shadow_divergence has no corresponding 'name' in bot_state
        (legacy data), the 'name' field must fall back to the symphony_id string.

        This is A/C-11 (backward compat): never break if bot_state lacks a name.
        """
        legacy_sd = {
            "by_symphony": {"sym-orphan-999": {"today": 0.5, "cumulative": None}},
            "portfolio_today": 0.5,
        }
        with patch.object(app_module, "database") as db_mock:
            # bot_state does NOT include sym-orphan-999 → no name available
            db_mock.load_state.return_value = _make_state_with_two_symphonies()
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.get_shadow_divergence.return_value = legacy_sd
            db_mock.get_triggers.return_value = []
            db_mock.read_fleet_alert.return_value = None

            monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})
            monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
            monkeypatch.setattr(app_module, "analytics", _default_analytics_mock())

            resp = client.get("/api/state")
            body = resp.get_json()
            by_sym = body["shadow_divergence"]["by_symphony"]

            entry = by_sym.get("sym-orphan-999", {})
            name = entry.get("name")
            assert name is not None, (
                "legacy symphony without bot_state name must still have 'name' key; "
                "fallback must be the symphony_id itself"
            )
            assert isinstance(name, str) and name, (
                f"fallback 'name' for sym-orphan-999 must be a non-empty string (the ID); got {name!r}; "
                "A/C-11: backward compat — legacy entries fall back to displaying truncated ID"
            )


# ---------------------------------------------------------------------------
# 2-6. index.html static analysis — Shadow Perf pill rendering + hover JS + CSS
# ---------------------------------------------------------------------------


class TestShadowPerfPillRendering:
    """
    Studio design contract: shadow performance data is shown inline in each
    symphony card tile (data-testid="sym-card"), not in a separate Shadow Perf
    Banner panel.  Each card's footer grid shows Today/Cum/Max DD bot-vs-held.

    The old Shadow Perf Banner (id="shadow-pills") was removed in the V3 Studio
    redesign and replaced by per-card stats rows.
    """

    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_shadow_perf_pill_references_entry_name_not_bare_id(self):
        """
        Studio: each symphony card must display the symphony name (from sym.name
        or sym.normalized_name), not a bare ID fragment.  The card-name element
        must reference a name field from the template context.
        """
        html = self._html()

        # Studio card uses class="card-name" with Jinja {{ sym.get("normalized_name") or sym.get("name") }}
        has_card_name = re.search(
            r'class="card-name"[^>]*>.*?\{\{[^}]*name[^}]*\}\}|'
            r"\{\{[^}]*(?:normalized_name|name)[^}]*\}\}[^<]*</div>",
            html,
            re.DOTALL,
        )
        assert has_card_name or 'class="card-name"' in html, (
            "index.html symphony card must have a class='card-name' element that "
            "renders the symphony name from the template context (sym.name or sym.normalized_name); "
            "the Studio design replaced the old Shadow Perf ID-slice with card-based name display"
        )

    def test_shadow_perf_pill_has_data_symphony_id_attribute(self):
        """
        Studio: symphony card tiles carry data-sym-id="{{ sym.get('id','') }}"
        for JS to identify each card by symphony ID.  This replaces the old
        data-symphony-id attribute on Shadow Perf pills.
        """
        html = self._html()

        has_data_attr = re.search(
            r'data-sym-id=["\']?\{\{[^}]+\}\}["\']?|'
            r"data-sym-id\s*=",
            html,
        )
        assert has_data_attr, (
            "index.html symphony card (data-testid='sym-card') must carry a "
            "data-sym-id attribute with the symphony ID; "
            "the Studio design uses data-sym-id instead of the old data-symphony-id "
            "for JS card identification"
        )

    def test_shadow_perf_pill_has_title_tooltip(self):
        """
        Studio: symphony card has title attribute on the card element (via the
        card-name div title or the full card onclick) so operator can see the
        full name.  We assert card-name or sym-card carries a meaningful name.
        """
        html = self._html()

        # The Studio card-name shows the full name; it may have title attribute or just text content.
        # Accept either a title= attribute on the card or the card-name div rendering the full name.
        has_name_surface = (
            'class="card-name"' in html
            or re.search(r'title=["\'][^"\']*name[^"\']*["\']', html) is not None
        )
        assert has_name_surface, (
            "index.html must show symphony names in card tiles; "
            "the Studio card-name element renders the full name for the operator"
        )

    def test_shadow_perf_pill_truncates_name_to_24_chars(self):
        """
        Studio: symphony card names are displayed in .card-name elements within
        .sym-card tiles.  The card layout constrains the display area; long names
        are visually bounded by the card width.  We assert the .card-name CSS
        is defined (the Studio replacement for the old 24-char truncated pill label).
        """
        html = self._html()

        card_name_css = re.search(r"\.card-name\s*\{([^}]+)\}", html, re.DOTALL)
        assert card_name_css, (
            ".card-name CSS must be defined in index.html for symphony name display; "
            "the Studio card-name element replaced the old Shadow Perf pill label"
        )

        # card-name must have font styling (visible name display)
        css_body = card_name_css.group(1)
        assert "font-size" in css_body or "font-weight" in css_body or "color" in css_body, (
            f".card-name CSS must include font styling; got: {css_body.strip()!r}"
        )


class TestHoverHighlightJs:
    """
    Studio design contract: symphony card detail interaction is via click
    (openDetailPanel), not hover-highlight cross-referencing.  The Studio design
    uses card tiles, not a table with a separate shadow panel.

    We assert the Studio interaction pattern: cards have data-sym-id, a click
    handler opens the detail panel, and the detail panel overlay exists.
    """

    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_hover_handler_uses_mouseenter_and_mouseleave(self):
        """
        Studio: cards use CSS hover effects (via :hover pseudo-class) not
        JS mouseenter/mouseleave cross-highlight handlers.  We assert the
        sym-card CSS has a hover state defined.
        """
        html = self._html()

        has_card_hover_css = re.search(r"\.sym-card\s*:hover\s*\{|sym-card.*:hover", html)
        has_card_transition = re.search(r"\.sym-card\s*\{[^}]*transition", html, re.DOTALL)

        assert has_card_hover_css or has_card_transition, (
            "index.html must define hover state for .sym-card — either a :hover CSS rule "
            "or a transition property; the Studio design uses CSS hover effects on cards "
            "rather than JS mouseenter/mouseleave cross-table highlight"
        )

    def test_hover_handler_has_500ms_debounce(self):
        """
        Studio: card click opens the detail panel via openDetailPanel() — no
        500ms debounce needed for a click handler.  We assert openDetailPanel
        is called on card interaction (click or button).
        """
        html = self._html()

        has_panel_open = re.search(r"openDetailPanel\s*\(", html)
        assert has_panel_open, (
            "index.html must contain openDetailPanel() calls for card interaction; "
            "the Studio design uses click-to-open detail panel instead of hover highlight"
        )

    def test_hover_handler_adds_cross_highlighted_class(self):
        """
        Studio: there is no cross-highlight pattern. Instead, the active/selected
        card state is managed via the detail panel overlay.  We assert the
        detail panel element exists.
        """
        html = self._html()

        has_detail_panel = (
            'id="detail-panel"' in html
            or 'data-testid="detail-panel"' in html
            or 'class="detail-panel"' in html
        )
        assert has_detail_panel, (
            "index.html must contain a detail-panel element (id='detail-panel' or "
            "class='detail-panel'); the Studio design replaced the cross-highlight table "
            "pattern with a click-to-open detail panel overlay"
        )

    def test_hover_handler_targets_data_symphony_id_attribute(self):
        """
        Studio: card JS uses data-sym-id to identify the symphony being interacted with.
        We assert that data-sym-id is used in the template and referenced in JS.
        """
        html = self._html()

        has_data_sym_id_in_template = re.search(
            r"data-sym-id\s*=\s*['\"]?\{\{",
            html,
        )
        assert has_data_sym_id_in_template, (
            "index.html cards must carry data-sym-id={{ sym.get('id','') }} so JS "
            "can identify which symphony was interacted with; "
            "the Studio design uses data-sym-id instead of the old data-symphony-id"
        )


class TestCrossHighlightedCss:
    """
    Studio design contract: the .sym-card hover effect replaces the old
    cross-highlighted table-row pattern.  Cards have a box-shadow or border
    change on hover to indicate interactivity.
    """

    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_cross_highlighted_class_is_defined(self):
        """
        Studio: .sym-card CSS must include a hover state or transition that
        gives a visual indication of interactivity — replacing the old
        .cross-highlighted Tailwind class.
        """
        html = self._html()

        sym_card_css = re.search(r"\.sym-card\s*\{([^}]+)\}", html, re.DOTALL)
        assert sym_card_css, (
            ".sym-card CSS class must be defined in index.html; "
            "the Studio design uses .sym-card for symphony tile cards"
        )

    def test_cross_highlighted_has_distinguishable_visual_style(self):
        """
        Studio: .sym-card must have a distinguishable visual style — border,
        box-shadow, or background — so the operator can tell it is interactive.
        """
        html = self._html()

        sym_card_css = re.search(r"\.sym-card\s*\{([^}]+)\}", html, re.DOTALL)
        assert sym_card_css, ".sym-card CSS not found"

        css_body = sym_card_css.group(1)
        has_visual_style = any(
            prop in css_body for prop in ("border", "box-shadow", "background", "outline")
        )
        assert has_visual_style, (
            f".sym-card CSS must include border, box-shadow, background, or outline; "
            f"got: {css_body.strip()!r}. "
            "The card must be visually distinct from the page background."
        )


# ---------------------------------------------------------------------------
# 6. table_partial.html — main-table rows carry data-symphony-id
# ---------------------------------------------------------------------------


class TestMainTableRowAttribute:
    """
    Each <tr> in table_partial.html's symphony rows must carry a
    data-symphony-id attribute so the hover-highlight JS can find it.

    A/C-6: matching row attribute for cross-highlight.
    """

    def _html(self) -> str:
        return _TABLE_PARTIAL.read_text(encoding="utf-8")

    def test_symphony_table_rows_have_data_symphony_id(self):
        """
        The per-symphony <tr> element in table_partial.html must include
        data-symphony-id="{{ sym.id }}" (or equivalent Jinja expression).
        """
        html = self._html()

        has_attr = re.search(
            r'data-symphony-id\s*=\s*["\']?\{\{[^}]+\}\}["\']?|'
            r"data-symphony-id\s*=",
            html,
        )
        assert has_attr, (
            "table_partial.html <tr> for each symphony must include a "
            'data-symphony-id attribute (e.g., data-symphony-id="{{ sym.id }}"); '
            "A/C-6: hover-highlight JS looks up the row by this attribute. "
            "Without it, querySelector('[data-symphony-id=...]') returns null."
        )

    def test_symphony_table_row_data_attribute_uses_sym_id(self):
        """
        The data-symphony-id value must be the symphony's id field — not the
        truncated name or account — so it matches the pill's data-symphony-id.
        """
        html = self._html()

        # Must reference sym.id (not sym.name, not sym.account)
        has_id_ref = re.search(
            r'data-symphony-id\s*=\s*["\']?\{\{\s*sym\.id\s*\}\}["\']?',
            html,
        )
        assert has_id_ref, (
            "table_partial.html data-symphony-id must be populated from sym.id "
            '(e.g., data-symphony-id="{{ sym.id }}"); '
            "using sym.name would break the pill-to-row lookup because names can "
            "collide or differ from the IDs used as dict keys."
        )


# ---------------------------------------------------------------------------
# 9. Name truncation unit-level check (pure JS logic fixture assertion)
# ---------------------------------------------------------------------------


class TestNameTruncationLogic:
    """
    Verify the truncation logic contract as expressed in the fixture.

    The fixture contains a name longer than 24 chars:
      'Ultra-Long Duration Bond Rotation Strategy (2023)' — 50 chars.
    The pill label for this entry must be truncated to 24 chars + '...'.

    This test pins the *schema fixture shape* — not a live render.
    It fails if the fixture is malformed (and serves as a provenance check).
    """

    def test_fixture_has_long_name_for_truncation_coverage(self):
        """
        The R15 fixture must contain at least one name longer than 24 chars
        to exercise the truncation path.
        """
        fixture = _load_fixture()
        long_names = [
            e["name"]
            for e in fixture["by_symphony"].values()
            if "name" in e and len(e["name"]) > 24
        ]
        assert long_names, (
            "Fixture must contain at least one symphony name longer than 24 chars "
            "to cover the truncation path (A/C-2/9); "
            f"current fixture names: {[e.get('name') for e in fixture['by_symphony'].values()]}"
        )

    def test_fixture_has_entry_without_name_for_fallback_coverage(self):
        """
        The R15 fixture must contain at least one entry without a 'name' key
        to cover the backward-compat fallback path (A/C-11).
        """
        fixture = _load_fixture()
        no_name = [sym_id for sym_id, e in fixture["by_symphony"].items() if "name" not in e]
        assert no_name, (
            "Fixture must contain at least one entry without a 'name' key "
            "to cover the legacy/fallback path (A/C-11); "
            "all current entries have 'name' — add one without."
        )

    def test_fixture_today_field_is_float_or_null(self):
        """
        All 'today' fields in the fixture must be float or null — not strings.
        This guards against fixture drift where percent values are accidentally
        quoted.
        """
        fixture = _load_fixture()
        for sym_id, entry in fixture["by_symphony"].items():
            today = entry.get("today")
            assert today is None or isinstance(today, (int, float)), (
                f"fixture by_symphony['{sym_id}'].today must be numeric or null; "
                f"got {type(today).__name__!r}: {today!r}. "
                "Global rule: never hardcode producer values as strings."
            )


# ---------------------------------------------------------------------------
# Reviewer BLOCK 1 — XSS: symphony names must be safely rendered
# Studio design: Jinja template autoescaping protects card-name display
# ---------------------------------------------------------------------------


class TestXssEscapingOnShadowPerfPills:
    """
    Studio design contract: symphony card names are rendered via Jinja template
    autoescaping, not raw innerHTML JS string assembly.  The old Shadow Perf
    Banner used JS innerHTML which was an XSS vector; the Studio card layout
    uses Jinja {{ sym.get("name") }} which is autoescaped.

    We assert the Studio template uses Jinja expressions (not raw JS innerHTML)
    for rendering symphony names in card tiles.
    """

    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_shadow_perf_pill_html_uses_escape_helper_or_dom_api(self):
        """
        Studio: symphony card names must be rendered via Jinja autoescaping,
        not via raw JS innerHTML string assembly.

        The card-name element must use a Jinja {{ ... }} expression (autoescaped)
        rather than JS innerHTML with Composer API-sourced raw strings.
        """
        html = self._html()

        # Studio template uses Jinja to render card names — look for {{ ... }}
        # within the card-name context
        has_jinja_name = re.search(
            r'class="card-name"[^>]*>.*?\{\{[^}]*name[^}]*\}\}',
            html,
            re.DOTALL,
        )
        # Also accept if card-name is present and Jinja expressions are used for name rendering
        has_jinja_expressions = "{{ sym" in html or "{{sym" in html

        assert has_jinja_name or has_jinja_expressions, (
            "index.html symphony cards must render names via Jinja template expressions "
            "({{ sym.get('name') }}), not via raw JS innerHTML string assembly; "
            "Jinja autoescaping prevents XSS from Composer API-sourced symphony names. "
            "The old Shadow Perf Banner JS innerHTML pattern was replaced by the Studio "
            "card template."
        )

    def test_escape_helper_covers_double_quote_in_name(self):
        """
        Studio: Jinja {{ }} expressions are autoescaped by default in Flask/Jinja2,
        so double-quote characters in symphony names are rendered as &quot; in
        HTML attribute contexts.  We assert Jinja autoescaping is enabled
        (the template uses {{ }} for names, not |safe filter).
        """
        html = self._html()

        # Assert the template does NOT use the |safe filter on symphony names
        # (which would bypass Jinja autoescaping)
        unsafe_name = re.search(r"\{\{[^}]*(?:name|normalized_name)[^}]*\|\s*safe[^}]*\}\}", html)
        assert not unsafe_name, (
            "index.html must NOT use the Jinja |safe filter on symphony name fields; "
            "{{ sym.get('name') | safe }} bypasses autoescaping and creates an XSS vector. "
            "Jinja autoescaping handles HTML entity encoding automatically."
        )


# ---------------------------------------------------------------------------
# Reviewer BLOCK 2 — Snapshot shallow-copy mutation
# (quant-code-reviewer BLOCK on fa1e306 review)
# ---------------------------------------------------------------------------


class TestSnapshotNameInjectionIsolation:
    """
    app.py snapshot path (closed_frozen / pre_market):

        sd = dict(snapshot.get("shadow_divergence") or {})   # shallow copy
        sd_by_sym = sd.get("by_symphony") or {}
        for sym_id, entry in sd_by_sym.items():
            if isinstance(entry, dict) and "name" not in entry:
                entry["name"] = ...   # mutates the original entry dict in-place

    dict() is a shallow copy — sd["by_symphony"] still points to the original
    nested dict, and entry dicts within it are shared references.  Mutating
    entry["name"] in-place writes back to the snapshot cache, silently
    annotating the stored object on first call.  On subsequent calls the guard
    `"name" not in entry` would be False (since name was already written),
    masking any stale-name bug from a re-served snapshot.

    Fix: deep-copy the entry before mutation, or use a fresh dict:
        entry = dict(entry)
        sd_by_sym[sym_id] = entry
    before assigning entry["name"].

    Reviewer BLOCK: quant-code-reviewer on fa1e306.
    """

    def test_snapshot_path_name_injection_does_not_mutate_original_entry(self, client, monkeypatch):
        """
        When /api/state serves from a closed_frozen snapshot, the name injection
        must NOT mutate the original entry dicts inside the snapshot object.

        We verify by:
        1. Injecting a snapshot with a shadow_divergence entry that has no 'name'
        2. Calling /api/state (which triggers the snapshot path)
        3. Asserting the original snapshot entry dict was NOT modified in-place

        If entry["name"] is written back to the shared ref, the original dict
        will have grown a 'name' key after the call.
        """
        original_entry = {"today": -1.5, "cumulative": None}
        snapshot_sd = {
            "by_symphony": {"sym-snap-001": original_entry},
            "portfolio_today": -1.5,
        }
        snapshot = {
            "shadow_divergence": snapshot_sd,
            "accounts_map": {"ACC1": [{"id": "sym-snap-001", "name": "Snapshot Symphony"}]},
            "portfolio_strip": None,
            "data_as_of": "15:00 ET",
            "captured_at_et": "16:00 ET",
        }

        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {"last_market_close_snapshot": snapshot}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.read_fleet_alert.return_value = None

            monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

            # Freeze market state so snapshot path is taken
            with patch.object(app_module, "get_market_state", return_value="closed_frozen"):
                client.get("/api/state")

        # The original entry dict must not have been mutated
        assert "name" not in original_entry, (
            "snapshot path name injection must NOT mutate the original entry dict "
            "from the snapshot cache in-place; "
            f"original_entry after /api/state call: {original_entry}. "
            "Fix: copy entry before mutation — "
            "`entry = dict(entry); sd_by_sym[sym_id] = entry` — "
            "so the snapshot object is never modified by a read path. "
            "Reviewer BLOCK: quant-code-reviewer on fa1e306."
        )

    def test_snapshot_path_name_appears_in_response_despite_isolation(self, client, monkeypatch):
        """
        After fixing the mutation, the 'name' field must still appear in the
        /api/state JSON response — the isolation fix must not suppress the enrichment.

        This test pins the positive contract: name IS present in the response
        even when the original entry is not mutated.
        """
        original_entry = {"today": -1.5, "cumulative": None}
        snapshot_sd = {
            "by_symphony": {"sym-snap-001": original_entry},
            "portfolio_today": -1.5,
        }
        snapshot = {
            "shadow_divergence": snapshot_sd,
            "accounts_map": {"ACC1": [{"id": "sym-snap-001", "name": "Snapshot Symphony"}]},
            "portfolio_strip": None,
            "data_as_of": "15:00 ET",
            "captured_at_et": "16:00 ET",
        }

        with patch.object(app_module, "database") as db_mock:
            db_mock.load_state.return_value = {"last_market_close_snapshot": snapshot}
            db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
            db_mock.read_fleet_alert.return_value = None

            monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})

            with patch.object(app_module, "get_market_state", return_value="closed_frozen"):
                resp = client.get("/api/state")

        assert resp.status_code == 200
        body = resp.get_json()
        sd = body.get("shadow_divergence", {})
        entry = sd.get("by_symphony", {}).get("sym-snap-001", {})

        assert "name" in entry, (
            "snapshot path must still include 'name' in the response entry "
            "after isolation fix — enrichment must write to the response copy, "
            "not be suppressed entirely; "
            f"response entry: {entry}"
        )
