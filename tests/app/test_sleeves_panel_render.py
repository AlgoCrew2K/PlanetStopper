"""
RED tests — Managed Sleeves P3: dashboard Sleeves panel (AC-16).

CONTRACT this file specifies for the GREEN implementer (s3-dashboard, visual
gate s3-ux):

  * templates/index.html gains a Sleeves panel section matching the existing
    light card-UI (Settings/Advisor card markup) — data-testid="sleeves-panel"
    wrapper, data-testid="sleeve-card" per sleeve, data-testid=
    "sleeve-status-badge" carrying the literal status string (one of SHADOW /
    PAPER / LIVE / BENCHED / PAUSED_RECONCILIATION / stale), and
    data-testid="atlas-cache-health-badge" for the Atlas cache-health
    observability element (audit MEDIUM-2).
  * app.py's dashboard() route (GET "/") passes a `sleeves` list (from
    database.get_all_sleeves()) into the render_template("index.html", ...)
    context so the panel is server-rendered on initial page load — mirrors
    the existing vars_locked_count / edit-vars-indicator precedent.
  * Sleeve/rule names are rendered through Jinja's normal autoescaping (no
    |safe filter, no raw string concatenation into HTML) — a sleeve name
    containing HTML/script content must render escaped, never executable.
  * No dark-theme CSS classes are introduced (house incident — "WHAT HAVE YOU
    DONE TO THE FUCKING UI" — never repeat it).

Route-level tests use the REAL dashboard() route, a REAL (per-test isolated)
SQLite DB, and the existing tests/app/conftest.py autouse
_stub_get_api_state_dict fixture (so Composer/network state is stubbed but
the sleeves panel itself is rendered from real database rows) — this is the
"route-level test with real producer modules, mock only DB/network" house
rule (dashboard-truth incident).
"""

from __future__ import annotations

import pathlib

import pytest

import app as app_module
import database

_TEMPLATES_DIR = pathlib.Path(__file__).parent.parent.parent / "templates"
_INDEX_HTML = _TEMPLATES_DIR / "index.html"


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Static template markup (fast, no live rendering — mirrors the guard-alpha
# panel test convention in test_guard_alpha_panel_ui.py)
# ---------------------------------------------------------------------------


class TestSleevesPanelMarkup:
    def test_sleeves_panel_wrapper_element_present(self):
        html = _html()
        assert 'data-testid="sleeves-panel"' in html, (
            'AC-16 FAIL: templates/index.html missing data-testid="sleeves-panel". '
            "The Sleeves panel has not been added to the template yet."
        )

    def test_sleeve_status_badge_element_present(self):
        html = _html()
        assert 'data-testid="sleeve-status-badge"' in html, (
            "AC-16 FAIL: templates/index.html missing a per-sleeve "
            'data-testid="sleeve-status-badge" element.'
        )

    def test_atlas_cache_health_badge_present(self):
        html = _html()
        assert 'data-testid="atlas-cache-health-badge"' in html, (
            "AC-16 FAIL: templates/index.html missing "
            'data-testid="atlas-cache-health-badge" (audit MEDIUM-2 — cache-row '
            "age + last available=False reason)."
        )

    def test_arm_disarm_controls_present(self):
        html = _html()
        assert (
            'data-testid="sleeve-arm-control"' in html
            or 'data-testid="sleeve-disarm-control"' in html
        ), "AC-16 FAIL: the Sleeves panel must expose arm/disarm controls per sleeve"

    def test_panel_uses_light_card_ui_not_dark_theme(self):
        """The panel must reuse the existing light card-UI theme — never
        introduce dark/foreign CSS classes (house incident, standing rule)."""
        html = _html()
        if 'data-testid="sleeves-panel"' not in html:
            pytest.skip("Panel not yet present — prior test covers this.")
        dark_markers = ["bg-dark", "dark-card", "theme-dark"]
        found = [m for m in dark_markers if m in html]
        assert not found, (
            f"AC-16 theme FAIL: dark/foreign CSS class(es) {found} found in "
            "templates/index.html. The Sleeves panel must use the existing "
            "light card-UI CSS. Never introduce dark/foreign theme classes."
        )


# ---------------------------------------------------------------------------
# Route-level render smoke test — real DB, real dashboard() route
# ---------------------------------------------------------------------------


class TestSleevesPanelRendersSeededStatuses:
    _STATUSES = ("SHADOW", "PAPER", "LIVE", "BENCHED", "PAUSED_RECONCILIATION", "stale")

    def test_panel_renders_for_each_seeded_status_without_error(self, client):
        seeded_names = []
        for status in self._STATUSES:
            name = f"panel-render-{status.lower()}-sleeve"
            sleeve_id = database.create_sleeve(name, 1000.0, envelope_json="{}")
            database.update_sleeve_status(sleeve_id, status)
            seeded_names.append((name, status))

        resp = client.get("/")
        assert resp.status_code == 200, (
            f"GET / must render 200 with sleeves seeded in every documented "
            f"status; got {resp.status_code}"
        )
        html = resp.get_data(as_text=True)

        for name, status in seeded_names:
            assert name in html, (
                f"seeded sleeve {name!r} (status={status}) did not appear "
                f"anywhere in the rendered dashboard HTML"
            )

    def test_panel_renders_correct_status_text_for_a_paused_sleeve(self, client):
        name = "panel-paused-reconciliation-sleeve"
        sleeve_id = database.create_sleeve(name, 1000.0, envelope_json="{}")
        database.update_sleeve_status(sleeve_id, "PAUSED_RECONCILIATION")

        resp = client.get("/")
        html = resp.get_data(as_text=True)
        assert name in html
        assert "PAUSED_RECONCILIATION" in html, (
            "a sleeve paused for reconciliation drift must show its actual "
            "status text on the panel — an operator must be able to see this "
            "at a glance, not infer it."
        )


# ---------------------------------------------------------------------------
# XSS: sleeve names render escaped, never as raw executable markup
# ---------------------------------------------------------------------------


class TestSleeveNameXSSEscaping:
    def test_script_tag_in_sleeve_name_renders_escaped(self, client):
        malicious_name = "<script>alert('xss')</script>"
        database.create_sleeve(malicious_name, 1000.0, envelope_json="{}")

        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.get_data(as_text=True)

        assert "<script>alert('xss')</script>" not in html, (
            "a sleeve name containing a raw <script> tag must NEVER appear "
            "unescaped in the rendered dashboard HTML — Jinja autoescaping "
            "must be in effect for every sleeve-supplied string."
        )
        assert "&lt;script&gt;" in html, (
            "the sleeve name must appear in its Jinja-escaped entity form "
            "(&lt;script&gt;...) — proof autoescaping actually ran, not just "
            "that the raw tag is absent (e.g. via silent truncation)."
        )
