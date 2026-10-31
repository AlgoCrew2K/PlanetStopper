"""The shared skeleton in a real browser: Settings keeps Save and Discard working, the primitives
follow the dark theme through tokens, and an empty History sits inside the panel primitive.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_skeleton_browser.py``). Firefox headless
through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with a
message naming it, never a skip.

The server is the app in this process on the per-test database (``tests/ui/_shell_support``),
seeded through the real writers from captured records; every request to anything but the server
is refused. Settings' Save is intercepted in the browser and answered there: nothing is written.

The dark render is judged by token, never by a colour: a primitive's computed background or text
colour must equal what ``var(--studio-...)`` computes to on the same page in the same theme, so
the assertion holds whatever the token values are and fails for any literal.
"""

from __future__ import annotations

import json

import pytest

from tests.ui._shell_support import (  # noqa: F401
    NARROW,
    PAGE_IDS,
    PAGES,
    REPO,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    assert_fully_visible,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
    seed_every_page,
    settings_edit_and_save,
    wait_until,
)

pytestmark = pytest.mark.browser

BASELINE = json.loads(
    (REPO / "tests" / "fixtures" / "dashboard" / "shell_baseline" / "baseline.json").read_text(
        encoding="utf-8"
    )
)["pages_1440"]


@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_save_and_discard_stay_enabled_and_reachable_and_save_sends_the_base_request(
    firefox, advisor_server, width, height
):
    """edit, scroll to the bottom, scroll back; Save writes the pending edit as before."""
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/settings")
        facts = settings_edit_and_save(probe, scroll_to_bottom=True)
        assert facts["save_enabled_after_edit"] is True
        assert facts["save_enabled_at_bottom"] is True
        assert facts["discard_enabled_at_bottom"] is True
        assert facts["request"] == BASELINE["/settings"]["save_request"]


def test_save_and_discard_are_disabled_with_no_edit(firefox, advisor_server):
    """Live: as at the base."""
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/settings")
        assert probe.page.is_disabled("#btn-save")
        assert probe.page.is_disabled("#btn-discard")
        assert_fully_visible(probe.page.locator("#btn-save"), "Save changes")
        assert_fully_visible(probe.page.locator("#btn-discard"), "Discard changes")


_TOKEN_COLOUR = """
([prop, token]) => {
  const probe = document.createElement('span');
  probe.style.setProperty(prop, 'var(' + token + ')');
  document.body.appendChild(probe);
  const value = getComputedStyle(probe)[prop === 'background' ? 'backgroundColor' : prop];
  probe.remove();
  return value;
}
"""

_COMPUTED = """
([selector, prop]) => {
  const el = document.querySelector(selector);
  return el ? getComputedStyle(el)[prop] : null;
}
"""

# (page, selector, computed property, css property for the token probe, token)
_PRIMITIVE_COLOURS = [
    ("/history", ".hero-section", "backgroundColor", "background", "--studio-paper"),
    ("/history", ".section-header", "color", "color", "--studio-ink-faint"),
    ("/history", ".mini-stat", "backgroundColor", "background", "--studio-chip-bg"),
    ("/history", "#reason-cards > *", "backgroundColor", "background", "--studio-paper"),
    ("/performance", ".hero-section", "backgroundColor", "background", "--studio-paper"),
    ("/performance", ".section-header", "color", "color", "--studio-ink-faint"),
    ("/performance", ".mini-stat", "backgroundColor", "background", "--studio-chip-bg"),
    ("/settings", ".hero-section", "backgroundColor", "background", "--studio-paper"),
    ("/settings", ".section-header", "color", "color", "--studio-ink-faint"),
    ("/ai-advisor", ".hero-section", "backgroundColor", "background", "--studio-paper"),
    ("/ai-advisor", ".section-header", "color", "color", "--studio-ink-faint"),
]


@pytest.mark.parametrize(
    "path, selector, computed, probe_prop, token",
    _PRIMITIVE_COLOURS,
    ids=[f"{p[0].strip('/') or 'dashboard'}-{p[1]}-{p[4]}" for p in _PRIMITIVE_COLOURS],
)
def test_each_primitive_takes_its_colour_from_its_token_in_the_dark_theme(
    firefox, advisor_server, tmp_path, monkeypatch, path, selector, computed, probe_prop, token
):
    """dark render; the primitive's colour is its token's, not a literal."""
    seed_every_page(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1], theme="dark") as probe:
        open_page(probe, path)
        wait_until(
            probe,
            "() => document.documentElement.getAttribute('data-theme') === 'dark'",
            "the dark theme was never applied",
        )
        assert probe.page.evaluate("(s) => !!document.querySelector(s)", selector), (
            f"{path} renders no {selector}"
        )
        expected = probe.page.evaluate(_TOKEN_COLOUR, [probe_prop, token])
        actual = probe.page.evaluate(_COMPUTED, [selector, computed])
        assert actual == expected, (
            f"{path} {selector}: {computed} is {actual}, {token} is {expected}"
        )


def test_an_empty_history_window_shows_its_empty_state_inside_a_panel(firefox, advisor_server):
    """Live: with no exits in the window the empty state is a panel's content."""
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/history")
        probe.page.wait_for_selector('[data-testid="history-empty-state"]', state="visible")
        inside = probe.page.evaluate(
            """
            () => {
              const el = document.querySelector('[data-testid="history-empty-state"]');
              const panel = el.classList.contains('hero-section') ? el : el.closest('.hero-section');
              if (!panel) return null;
              const a = el.getBoundingClientRect();
              const b = panel.getBoundingClientRect();
              return a.left >= b.left - 0.5 && a.right <= b.right + 0.5
                  && a.top >= b.top - 0.5 && a.bottom <= b.bottom + 0.5;
            }
            """
        )
        assert inside is True, "the empty state is not drawn inside a .hero-section"


@pytest.mark.parametrize("path", PAGES, ids=PAGE_IDS)
def test_the_page_raises_no_script_error_on_load(firefox, advisor_server, path):
    """The skeleton moves the tab bar and the actions row; the scripts that bind them still run."""
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, path)
        probe.page.wait_for_timeout(500)
        assert probe.page_errors == [], probe.page_errors


def test_the_advisor_tab_bar_still_switches_tabs(firefox, advisor_server):
    """the tab bar moved into the first panel; clicking a tab still shows its panel."""
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/ai-advisor")
        probe.page.click('[data-tab="chat"]')
        wait_until(
            probe,
            "() => { const p = document.querySelector('[data-testid=\"tab-panel-chat\"]');"
            " return !!p && getComputedStyle(p).display !== 'none' && !p.hidden; }",
            "the Chat panel never showed after its tab was clicked",
        )
        hidden = probe.page.evaluate(
            "() => { const p = document.querySelector('[data-testid=\"tab-panel-overview\"]');"
            " return getComputedStyle(p).display === 'none' || p.hidden; }"
        )
        assert hidden is True
