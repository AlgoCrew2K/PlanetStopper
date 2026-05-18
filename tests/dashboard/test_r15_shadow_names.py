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

    def test_by_symphony_entries_have_name_key(
        self, client, mock_database_with_names, monkeypatch
    ):
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

    def test_legacy_entry_without_name_falls_back_to_id(
        self, client, monkeypatch
    ):
        """
        When a symphony in shadow_divergence has no corresponding 'name' in bot_state
        (legacy data), the 'name' field must fall back to the symphony_id string.

        This is A/C-11 (backward compat): never break if bot_state lacks a name.
        """
        legacy_sd = {
            "by_symphony": {
                "sym-orphan-999": {"today": 0.5, "cumulative": None}
            },
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
    Static analysis of templates/index.html.

    Tests are RED against current main which renders symId.slice(-8) with no
    symphony name, has no data-symphony-id attribute, and has no hover handler.
    """

    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_shadow_perf_pill_references_entry_name_not_bare_id(self):
        """
        The Shadow Perf pill render loop must use entry.name (or d.name) to
        display the label — NOT the bare symId.slice(-8).

        A/C-2: operator sees symphony name truncated to ~24 chars.
        A/C-7 (template check): pill loop references entry.name.
        """
        html = self._html()

        # Locate the shadow-pills rendering JS block
        shadow_pills_block_match = re.search(
            r"shadow-pills[\s\S]{0,2000}\.join\(['\"]",
            html,
        )
        assert shadow_pills_block_match, (
            "Could not locate the shadow-pills innerHTML JS block in index.html; "
            "check that the Shadow Perf rendering code is still present"
        )
        block = shadow_pills_block_match.group(0)

        # Must reference .name (from the symphony entry dict) in the pill label
        has_name_ref = re.search(r"\bd\.name\b|\bentry\.name\b|\['\"]name['\"]", block)
        assert has_name_ref, (
            "Shadow Perf pill render loop must reference d.name (or entry.name) "
            "to display the symphony name; "
            "current code uses symId.slice(-8) which shows an opaque ID fragment. "
            "R15 A/C-2: replace ID slice with truncated name (max 24 chars + '...')."
        )

    def test_shadow_perf_pill_has_data_symphony_id_attribute(self):
        """
        Each Shadow Perf pill must carry a data-symphony-id attribute so the
        hover-highlight JS can look up the matching main-table row.

        A/C-3 + A/C-6: both pill and main-table row need the same attribute.
        """
        html = self._html()

        shadow_pills_block_match = re.search(
            r"shadow-pills[\s\S]{0,2000}\.join\(['\"]",
            html,
        )
        assert shadow_pills_block_match, (
            "shadow-pills innerHTML block not found; cannot verify data-symphony-id"
        )
        block = shadow_pills_block_match.group(0)

        has_data_attr = re.search(r"data-symphony-id", block)
        assert has_data_attr, (
            "Shadow Perf pill template must include data-symphony-id attribute; "
            "e.g.: data-symphony-id=\"${symId}\". "
            "A/C-3: hover-highlight JS needs this to find the matching table row."
        )

    def test_shadow_perf_pill_has_title_tooltip(self):
        """
        Each Shadow Perf pill must include a title attribute carrying the full
        symphony name (for tooltip on hover).

        A/C-4 + A/C-10: full name shown in tooltip.
        """
        html = self._html()

        shadow_pills_block_match = re.search(
            r"shadow-pills[\s\S]{0,2000}\.join\(['\"]",
            html,
        )
        assert shadow_pills_block_match, (
            "shadow-pills innerHTML block not found; cannot verify title tooltip"
        )
        block = shadow_pills_block_match.group(0)

        has_title = re.search(r"\btitle\s*=", block)
        assert has_title, (
            "Shadow Perf pill template must include a title= attribute for full symphony name; "
            "A/C-4/10: full name shown on tooltip so operator can read it without hover."
        )

    def test_shadow_perf_pill_truncates_name_to_24_chars(self):
        """
        The JS that builds pill labels must truncate names longer than 24 chars
        and append '...'.

        A/C-2: truncated to ~24 chars + '...' suffix.
        We look for the truncation logic in the shadow-pills block.
        """
        html = self._html()

        shadow_pills_block_match = re.search(
            r"shadow-pills[\s\S]{0,2000}\.join\(['\"]",
            html,
        )
        assert shadow_pills_block_match, (
            "shadow-pills innerHTML block not found; cannot verify truncation"
        )
        block = shadow_pills_block_match.group(0)

        # Accept: .slice(0, 24) + '...' OR .substring(0, 24) OR length > 24 check
        has_truncation = re.search(
            r"\.slice\s*\(\s*0\s*,\s*2[0-9]\s*\)|"
            r"\.substring\s*\(\s*0\s*,\s*2[0-9]\s*\)|"
            r"length\s*[><=!]+\s*2[0-9]",
            block,
        )
        assert has_truncation, (
            "Shadow Perf pill JS must truncate symphony names longer than ~24 chars; "
            "expected .slice(0, 24) or equivalent length check + '...' suffix. "
            "A/C-2: pills must fit in the banner without overflow."
        )


class TestHoverHighlightJs:
    """
    Static analysis: index.html must wire a hover handler on Shadow Perf pills
    that applies .cross-highlighted to the matching main-table row after 500ms.
    """

    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_hover_handler_uses_mouseenter_and_mouseleave(self):
        """
        The hover-highlight JS must use mouseenter (or mouseover) to arm and
        mouseleave (or mouseout) to clear — not just onclick.

        A/C-3/8: hover handler wired.
        """
        html = self._html()

        has_mouseenter = re.search(r"\bmouseenter\b|\bmouseover\b", html)
        has_mouseleave = re.search(r"\bmouseleave\b|\bmouseout\b", html)

        assert has_mouseenter, (
            "index.html must contain a mouseenter (or mouseover) event handler "
            "for the Shadow Perf hover-highlight; "
            "A/C-8: hover-highlight arms on mouseenter."
        )
        assert has_mouseleave, (
            "index.html must contain a mouseleave (or mouseout) event handler "
            "to clear the cross-highlight when operator moves away; "
            "A/C-3: highlight clears on mouseleave."
        )

    def test_hover_handler_has_500ms_debounce(self):
        """
        The hover handler must use a 500ms setTimeout before applying the
        highlight class — so brief hover-over doesn't flash the table.

        A/C-3: highlight applied after 500ms, not instantly.
        """
        html = self._html()

        # Accept 500 as an integer literal near setTimeout
        has_500ms = re.search(r"setTimeout\s*\([^)]{0,200}500\b", html)
        assert has_500ms, (
            "index.html hover handler must use setTimeout(..., 500) to debounce "
            "the highlight application; "
            "A/C-3: operator must hover for 500ms before highlight fires — "
            "instant highlight is too noisy for a poll-driven UI."
        )

    def test_hover_handler_adds_cross_highlighted_class(self):
        """
        The hover JS must add .cross-highlighted (or an equivalent named class)
        to the matching main-table row.

        A/C-3: '.cross-highlighted' class applied to table row.
        """
        html = self._html()

        has_class = re.search(r"cross.highlighted|crossHighlighted", html)
        assert has_class, (
            "index.html hover JS must add a 'cross-highlighted' (or 'crossHighlighted') "
            "class to the matched symphony row in the main table; "
            "A/C-3: operator sees visual association between Shadow Perf pill and table row."
        )

    def test_hover_handler_targets_data_symphony_id_attribute(self):
        """
        The hover JS must use data-symphony-id to find the matching table row —
        not positional index or other unreliable selectors.

        A/C-6: both pill and table row carry data-symphony-id for the lookup.
        """
        html = self._html()

        has_selector = re.search(
            r'querySelector\s*\([^\)]*data-symphony-id|'
            r'querySelectorAll\s*\([^\)]*data-symphony-id|'
            r'dataset\.symphonyId|'
            r'\[data-symphony-id',
            html,
        )
        assert has_selector, (
            "hover JS must use data-symphony-id attribute to select the matching "
            "main-table row (e.g., querySelector('[data-symphony-id=\"...\"]')); "
            "A/C-6: positional selectors would break if row order changes."
        )


class TestCrossHighlightedCss:
    """
    The .cross-highlighted CSS class must be defined in index.html
    (inline <style> or Tailwind utility) with a visually distinguishable style.

    A/C-9: .cross-highlighted has a visible style like bg-yellow-100 or outline-2 outline-amber-400.
    """

    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_cross_highlighted_class_is_defined(self):
        """
        The .cross-highlighted CSS class (or JS-added equivalent) must be defined
        somewhere in index.html — either in a <style> block or via Tailwind class strings.
        """
        html = self._html()

        defined = re.search(
            r"\.cross-highlighted\s*\{|"           # CSS class definition
            r"cross-highlighted.*bg-|"              # Tailwind bg-* in class string
            r"cross-highlighted.*outline|"          # Tailwind outline-* in class string
            r"crossHighlighted.*bg-|"
            r"crossHighlighted.*outline",
            html,
        )
        assert defined, (
            ".cross-highlighted CSS class (or Tailwind equivalent) must be defined in index.html; "
            "A/C-9: the class must have a distinguishable visual style "
            "(e.g., bg-yellow-100/20 or outline outline-amber-400). "
            "Without a definition the addClass() call is a no-op."
        )

    def test_cross_highlighted_has_distinguishable_visual_style(self):
        """
        The .cross-highlighted style must use a visible color or outline — not
        just a cursor or opacity change.  Background or border/outline is required.
        """
        html = self._html()

        # Look for a background or border/outline near the class definition
        visual = re.search(
            r"cross.highlighted[^}]{0,200}(?:background|bg-|border|outline|ring-)",
            html,
            re.DOTALL,
        )
        assert visual, (
            ".cross-highlighted must include a background, border, or outline style; "
            "a cursor change alone is not sufficient for the operator to notice the highlight. "
            "A/C-9: e.g., bg-yellow-100/20 dark:bg-amber-900/20."
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
            r'data-symphony-id\s*=',
            html,
        )
        assert has_attr, (
            "table_partial.html <tr> for each symphony must include a "
            "data-symphony-id attribute (e.g., data-symphony-id=\"{{ sym.id }}\"); "
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
            "(e.g., data-symphony-id=\"{{ sym.id }}\"); "
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
        no_name = [
            sym_id
            for sym_id, e in fixture["by_symphony"].items()
            if "name" not in e
        ]
        assert no_name, (
            "Fixture must contain at least one entry without a 'name' key "
            "to cover the legacy/fallback path (A/C-11); "
            f"all current entries have 'name' — add one without."
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
# Reviewer BLOCK 1 — XSS: rawName raw-interpolated into innerHTML
# (quant-code-reviewer BLOCK on fa1e306 review)
# ---------------------------------------------------------------------------


class TestXssEscapingOnShadowPerfPills:
    """
    The Shadow Perf pill JS uses innerHTML string assembly with rawName (from
    d.name, a Composer API-sourced symphony name).  Interpolating an unescaped
    external string into innerHTML is an XSS vector:

      title="${rawName}"   — a name containing `"` breaks the attribute
      >${label}            — a name containing `</span><img ...>` executes in DOM

    Jinja autoescape does NOT apply to JS innerHTML assignments.

    Fix: either use document.createElement + setAttribute/textContent instead of
    innerHTML string assembly, or introduce an escapeHtml() helper and apply it
    to both rawName and label before interpolation.

    Reviewer BLOCK: quant-code-reviewer on fa1e306.
    """

    def _html(self) -> str:
        return _INDEX_HTML.read_text(encoding="utf-8")

    def test_shadow_perf_pill_html_uses_escape_helper_or_dom_api(self):
        """
        The Shadow Perf pill rendering JS must NOT raw-interpolate rawName or
        label directly into an innerHTML template literal without escaping.

        Acceptable fixes:
        (a) An escapeHtml() / htmlEscape() / sanitize() function applied to
            rawName and label before interpolation, OR
        (b) DOM API construction (createElement + setAttribute + textContent)
            that never calls innerHTML with tainted data.

        This test fails on the current implementation where rawName is used
        verbatim in template literals assigned to innerHTML.
        """
        html = self._html()

        shadow_pills_block_match = re.search(
            r"shadow-pills[\s\S]{0,3000}\.join\(['\"]",
            html,
        )
        assert shadow_pills_block_match, (
            "shadow-pills innerHTML block not found in index.html"
        )
        block = shadow_pills_block_match.group(0)

        # Option (a): an escape helper is defined and applied to rawName/label
        has_escape_helper = re.search(
            r"(?:escapeHtml|htmlEscape|escapeAttr|sanitize)\s*\([^)]*(?:rawName|label)",
            block,
        )

        # Option (b): DOM API construction — no innerHTML with tainted data
        # The block must NOT use innerHTML with rawName/label directly if DOM API is used.
        # We detect DOM API by presence of createElement or textContent in the shadow block.
        uses_dom_api = re.search(
            r"createElement\s*\(|\.textContent\s*=|\.setAttribute\s*\(",
            block,
        )

        # If neither fix is present, the test fails.
        assert has_escape_helper or uses_dom_api, (
            "Shadow Perf pill JS interpolates rawName/label (Composer API-sourced) "
            "directly into innerHTML without HTML escaping. "
            "A symphony name containing '\"' breaks attribute quoting; "
            "one containing '</span><img src=x onerror=...>' executes in the DOM. "
            "Fix: apply escapeHtml(rawName) and escapeHtml(label) before interpolation, "
            "or build pill elements with createElement + setAttribute/textContent. "
            "Reviewer BLOCK: quant-code-reviewer on fa1e306."
        )

    def test_escape_helper_covers_double_quote_in_name(self):
        """
        If an escapeHtml() helper is defined, it must convert '\"' to '&quot;'
        (or '&#34;') so it cannot break out of a title= attribute.

        This test locates the escapeHtml function definition and asserts it
        handles the double-quote character.  If DOM API is used exclusively,
        this test passes vacuously (no innerHTML with raw strings to escape).
        """
        html = self._html()

        # If no escapeHtml function is defined, check that DOM API is used instead.
        has_escape_fn = re.search(
            r"function\s+(?:escapeHtml|htmlEscape|escapeAttr)\s*\(|"
            r"const\s+(?:escapeHtml|htmlEscape|escapeAttr)\s*=",
            html,
        )
        uses_dom_api = re.search(
            r"createElement\s*\(|\.textContent\s*=",
            html,
        )

        if not uses_dom_api:
            assert has_escape_fn, (
                "If innerHTML string assembly is used for Shadow Perf pills, "
                "an escapeHtml (or equivalent) function must be defined to sanitize "
                "Composer API-sourced symphony names before interpolation. "
                "Alternatively, use DOM API (createElement + textContent) to avoid "
                "innerHTML entirely."
            )
            # Verify the escape function handles double-quote
            escape_fn_match = re.search(
                r"function\s+(?:escapeHtml|htmlEscape|escapeAttr)\s*\([^)]*\)\s*\{[^}]+\}|"
                r"const\s+(?:escapeHtml|htmlEscape|escapeAttr)\s*=[^;]+;",
                html,
                re.DOTALL,
            )
            if escape_fn_match:
                fn_body = escape_fn_match.group(0)
                handles_dquote = re.search(
                    r'["\']"["\']|"&quot;"|"&#34;"|\\x22|\\u0022',
                    fn_body,
                )
                assert handles_dquote, (
                    "escapeHtml function must handle the double-quote character ('\"' → '&quot;'); "
                    "a name containing '\"' breaks out of the title= attribute in innerHTML. "
                    "Ensure the function replaces '\"' with '&quot;' or '&#34;'."
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

    def test_snapshot_path_name_injection_does_not_mutate_original_entry(
        self, client, monkeypatch
    ):
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
            db_mock.load_state.return_value = {
                "last_market_close_snapshot": snapshot
            }
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

    def test_snapshot_path_name_appears_in_response_despite_isolation(
        self, client, monkeypatch
    ):
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
            db_mock.load_state.return_value = {
                "last_market_close_snapshot": snapshot
            }
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
