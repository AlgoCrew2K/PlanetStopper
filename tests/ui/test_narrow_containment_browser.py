"""Wide content fits a 375 px screen: tables scroll inside their own box, the settings page and
modal are not clipped, the Chat panel opens on screen, Frontrunner cards keep their buttons, and
the Advisor's chip tints follow the dark theme, and the floating gear steps aside for the open Chat
panel.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_narrow_containment_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails
with a message naming it, never a skip.

The server is the app in this process on the per-test database (``tests/ui/_shell_support``),
seeded through the real writers from captured records: the captured bot state, the captured
post-mortem days (each trigger carries the one derived key the validity guard requires), a pending
Frontrunner proposal over the captured incumbent tree, and the realized-series run
``tests.advisors._series_captures.seed_run_from_captures`` writes through the production writer.
Every request to anything but the server is refused, and the developer ``.env`` is never read.
Requests that would write (the settings modal's Save, the settings page's Save) are intercepted in
the browser and answered there: nothing is written.

Layout facts (a box scrolls inside, an element is inside the viewport and uncovered, a column is
reachable by scrolling) are asserted on a live render; colours and sizes are never asserted. The
tints are compared between a light and a dark render of the same element: a tint that follows a
token changes with the theme, a fixed literal does not.
"""

from __future__ import annotations

import os

import pytest

from tests.advisors import _series_captures as captures
from tests.ui._shell_support import (  # noqa: F401
    NARROW,
    VERY_NARROW,
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    assert_fully_visible,
    assert_not_clipped_by_its_containers,
    document_scroll_width,
    fail,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
    seed_history_exits,
    seed_pending_frontrunner_proposal,
    wait_until,
)

pytestmark = pytest.mark.browser

_SCROLLER_FACTS = """
(el) => {
  let box = el;
  for (let a = el; a && a !== document.body; a = a.parentElement) {
    const ox = getComputedStyle(a).overflowX;
    if (ox === 'auto' || ox === 'scroll') { box = a; break; }
  }
  const scrolls = ['auto', 'scroll'].includes(getComputedStyle(box).overflowX);
  const before = box.scrollLeft;
  box.scrollLeft = box.scrollWidth;
  const moved = box.scrollLeft > before;
  box.scrollLeft = before;
  return {
    scrolls, wider: box.scrollWidth > box.clientWidth + 1, moved,
    box: [box.tagName, String(box.className).slice(0, 40)],
    isTable: box === el,
  };
}
"""


def _scroller(locator) -> dict:
    return locator.evaluate(_SCROLLER_FACTS)


def _assert_scrolls_inside(locator, label: str) -> None:
    facts = _scroller(locator)
    assert not facts["isTable"], f"{label}: no container around the table scrolls it"
    assert facts["scrolls"], f"{label}: its container {facts['box']} does not scroll sideways"
    assert facts["wider"], f"{label}: nothing overflows its container at this width"
    assert facts["moved"], f"{label}: the container cannot be scrolled to its last column"


def _assert_scrolls_when_wider(locator, label: str) -> None:
    """The table sits in a sideways-scrolling container, and wherever its columns are wider than
    the container the container can be scrolled to the last one (an empty table may fit)."""
    facts = _scroller(locator)
    assert not facts["isTable"], f"{label}: no container around the table scrolls it"
    assert facts["scrolls"], f"{label}: its container {facts['box']} does not scroll sideways"
    if facts["wider"]:
        assert facts["moved"], f"{label}: the container cannot be scrolled to its last column"


def _assert_header_cells_do_not_overlap(locator, label: str) -> None:
    overlaps = locator.evaluate(
        """
        (table) => {
          // a header cell the phone layout hides takes no room, so it overlaps nothing
          const cells = [...table.querySelectorAll('thead th')].filter((c) => c.getClientRects().length > 0);
          const out = [];
          for (let i = 0; i + 1 < cells.length; i++) {
            const a = cells[i].getBoundingClientRect();
            const b = cells[i + 1].getBoundingClientRect();
            if (a.right > b.left + 1) out.push([i, Math.round(a.right - b.left)]);
          }
          return out;
        }
        """
    )
    assert overlaps == [], f"{label}: header cells overlap {overlaps}"


# ---------------------------------------------------------------------------
# History and Performance tables
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_the_performance_tables_fit_the_screen_at_narrow_widths(
    firefox, advisor_server, width, height
):
    """three tables, none overlapping its columns; headline labels not truncated.

    The risk-metrics table has four columns and fits the screen (its labels and headers wrap). The
    preconditions and exit-turnover tables stack on a phone (the symphony on a line, its figures
    on one line under it, under one header row), so they fit as well: nothing waits off-screen in
    a scroll box.
    """
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/performance")
        tables = probe.page.locator("table.metrics-tbl")
        assert tables.count() == 3
        for index in (0, 1, 2):
            fits = tables.nth(index).evaluate(
                "(t) => t.scrollWidth <= t.closest('.metrics-overflow').clientWidth + 1"
            )
            assert fits, f"Performance table {index} does not fit the screen"
            _assert_header_cells_do_not_overlap(tables.nth(index), f"Performance table {index}")
        truncated = probe.page.evaluate(
            """
            () => [...document.querySelectorAll('[data-testid="headline-strip"] [class*="label"]')]
              .filter((e) => e.scrollWidth > e.clientWidth + 1)
              .map((e) => e.textContent.trim())
            """
        )
        assert truncated == [], f"headline labels are truncated: {truncated}"


# ---------------------------------------------------------------------------
# The per-symphony settings modal
# ---------------------------------------------------------------------------


def _open_settings_modal(probe):
    seed = probe.page
    seed.locator('button[onclick^="openSymphonySettings"]').first.click()
    seed.wait_for_selector("[data-modal-surface]")
    wait_until(
        probe,
        "() => [...document.querySelectorAll('[data-modal-surface] button')]"
        ".some((b) => b.textContent.trim() === 'Cancel')",
        "the settings modal never rendered its footer",
    )
    return seed.locator("[data-modal-surface]")


def _modal_button(surface, name: str):
    return surface.locator("button", has_text=name).last


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_the_settings_modal_fits_at_narrow_widths_and_its_footer_is_reachable(
    firefox, advisor_server, width, height
):
    """the locked-variables grid is not clipped; Cancel and Save are on screen."""
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/")
        surface = _open_settings_modal(probe)
        assert_fully_visible(_modal_button(surface, "Cancel"), "modal Cancel at 375")
        assert_fully_visible(_modal_button(surface, "Save settings"), "modal Save at 375")
        grid = surface.locator("div[style*='grid-template-columns']").first
        for index in range(grid.locator("> *").count()):
            child = grid.locator("> *").nth(index)
            assert_not_clipped_by_its_containers(child, f"locked-variable cell {index}")
            inside = child.evaluate(
                "(c) => c.getBoundingClientRect().right <= c.parentElement.getBoundingClientRect().right + 0.5"
            )
            assert inside, f"locked-variable cell {index} runs past the grid's right edge"
        body_scrolls = surface.evaluate(
            """
            (s) => {
              const body = [...s.children].find((c) => getComputedStyle(c).overflowY === 'auto');
              if (!body) return null;
              body.scrollTop = body.scrollHeight;
              return body.scrollTop > 0;
            }
            """
        )
        assert body_scrolls is True, "the modal body does not scroll inside the capped surface"
        assert_fully_visible(_modal_button(surface, "Save settings"), "modal Save after scroll")


def test_the_settings_modal_footer_is_reachable_at_1440(firefox, advisor_server):
    """the audit could not confirm this at 1440x900."""
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/")
        surface = _open_settings_modal(probe)
        assert_fully_visible(_modal_button(surface, "Cancel"), "modal Cancel at 1440")
        assert_fully_visible(_modal_button(surface, "Save settings"), "modal Save at 1440")


def test_an_edit_in_the_modal_survives_scrolling_its_body_and_saves(firefox, advisor_server):
    """the edit and Save's enabled state are kept; the POST is intercepted."""
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, "/")
        posted: list = []

        def handler(route):
            if route.request.method == "POST":
                posted.append(route.request)
                route.fulfill(
                    status=200, content_type="application/json", body='{"status":"success"}'
                )
            else:
                route.continue_()

        probe.page.route("**/api/symphony-settings/**", handler)
        surface = _open_settings_modal(probe)
        save = _modal_button(surface, "Save settings")
        chip = surface.locator("button", has_text="TAKE_PROFIT_MC_PCT").first
        before = chip.inner_text()
        chip.click()
        after = chip.inner_text()
        assert after != before, "premise: clicking a locked-variable chip changes it"
        surface.evaluate(
            "(s) => { const b = [...s.children].find((c) => getComputedStyle(c).overflowY === 'auto');"
            " if (b) { b.scrollTop = b.scrollHeight; b.scrollTop = 0; b.scrollTop = b.scrollHeight; } }"
        )
        assert chip.inner_text() == after, "scrolling the body lost the edit"
        assert save.is_enabled()
        assert_fully_visible(save, "modal Save after the edit and scroll")
        save.click()
        probe.page.wait_for_timeout(500)  # the intercepted POST is recorded by then
        assert len(posted) == 1
        assert "TAKE_PROFIT_MC_PCT" in (posted[0].post_data or "")


# ---------------------------------------------------------------------------
# Settings page, Chat panel, Frontrunner cards, Correlations matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_every_settings_label_and_input_fits_at_narrow_widths_with_the_side_nav_above(
    firefox, advisor_server, width, height
):
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/settings")
        for section in ("master", "algorithm", "credentials"):
            probe.page.click(f'.sn-btn[data-section="{section}"]')
            probe.page.wait_for_selector(f"#sec-{section}", state="visible")
            probe.page.wait_for_timeout(300)
            elements = probe.page.locator(
                f"#sec-{section} label, #sec-{section} input, #sec-{section} select"
            )
            for index in range(elements.count()):
                element = elements.nth(index)
                if not element.is_visible():
                    continue
                box = element.bounding_box()
                assert box["x"] >= -0.5 and box["x"] + box["width"] <= width + 0.5, (
                    f"{section} control {index} is outside the viewport: {box}"
                )
                assert_not_clipped_by_its_containers(element, f"{section} control {index}")
        facts = probe.page.evaluate(
            """
            () => {
              const nav = document.querySelector('.settings-sidenav').getBoundingClientRect();
              const content = document.querySelector('.settings-content').getBoundingClientRect();
              return { navBottom: nav.bottom, contentTop: content.top,
                       contentLeft: content.left, contentRight: content.right, vw: innerWidth };
            }
            """
        )
        assert facts["navBottom"] <= facts["contentTop"] + 1, (
            "the side nav is not above the content"
        )
        assert facts["contentLeft"] >= -0.5 and facts["contentRight"] <= facts["vw"] + 0.5


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_the_chat_panel_opens_inside_the_viewport_at_narrow_widths(
    firefox, advisor_server, width, height
):
    """after a Discuss action the panel's left edge is at or inside the viewport's."""
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        probe.page.goto(probe.server.base_url + "/ai-advisor")
        wait_until(
            probe,
            "() => typeof window.openChatPanel === 'function'",
            "the Advisor never defined openChatPanel (the function every Discuss button calls)",
        )
        probe.page.evaluate(
            "() => window.openChatPanel({artifactId: 'shell-check', artifactType: 'asset_swap',"
            " title: 'Explain', contextLabel: 'Asset Swap', objective: 'check', gateDecision: null,"
            " keyStat: '', artifactContext: {}})"
        )
        wait_until(
            probe,
            "() => !!document.querySelector('.chat-panel.chat-panel--open')",
            "the chat panel never opened",
        )
        probe.page.wait_for_timeout(600)  # the panel slides in over 0.25 s
        left = probe.page.evaluate(
            "() => document.querySelector('[data-testid=\"chat-panel\"]').getBoundingClientRect().left"
        )
        assert left >= -0.5, f"the open chat panel starts at x={left}, off the left of the screen"
        right = probe.page.evaluate(
            "() => document.querySelector('[data-testid=\"chat-panel\"]').getBoundingClientRect().right"
        )
        assert right <= width + 0.5


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_a_pending_frontrunner_cards_buttons_are_visible_at_narrow_widths(
    firefox, advisor_server, width, height
):
    seed_pending_frontrunner_proposal()
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/ai-advisor", fragment="#frontrunner-builder")
        probe.page.wait_for_selector('[data-testid="fr-approve-btn"]', state="visible")
        approve = probe.page.locator('[data-testid="fr-approve-btn"]').first
        reject = probe.page.locator('[data-testid="fr-reject-btn"]').first
        card = probe.page.locator('[data-testid="frontrunner-proposal-card"]').first
        card_box = card.bounding_box()
        assert card_box["x"] >= -0.5 and card_box["x"] + card_box["width"] <= width + 0.5, (
            f"the Frontrunner card is wider than the screen: {card_box}"
        )
        for button, label in ((approve, "Approve"), (reject, "Reject")):
            # Vertical scroll only: scrolling a control into view would also scroll the page
            # sideways, which hides exactly the clipping this test looks for.
            button.evaluate(
                "(b) => window.scrollTo(0, window.scrollY + b.getBoundingClientRect().top"
                " - innerHeight / 2)"
            )
            box = button.bounding_box()
            assert box["x"] >= -0.5 and box["x"] + box["width"] <= width + 0.5, (
                f"{label} is outside the viewport horizontally: {box}"
            )
            assert_not_clipped_by_its_containers(button, label)


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_the_correlations_matrix_scrolls_inside_its_container_at_narrow_widths(
    firefox, advisor_server, width, height
):
    """the matrix wrapper scrolls; the page does not."""
    captures.seed_run_from_captures(os.environ["DB_PATH"])
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/ai-advisor", fragment="#correlations")
        probe.page.wait_for_selector('[data-testid="correlation-matrix-table"]', state="visible")
        _assert_scrolls_inside(
            probe.page.locator('[data-testid="correlation-matrix-table"]'), "Correlations matrix"
        )


# ---------------------------------------------------------------------------
# the five tints follow the theme
# ---------------------------------------------------------------------------

_TINT_CLASSES = {
    "explain-only notice": ["explain-only-notice"],
    "promoted incubation chip": ["incubation-status-chip", "incubation-status-chip--promoted"],
    "passed gate pill": ["gate-pill", "gate-pill--passed"],
    "risk-on sentiment chip": ["prism-sentiment-chip", "prism-sentiment-chip--risk-on"],
    "approved retirement status": ["status-pill", "adv-status", "armed"],
}

# A status is a dot and a word: its tint is the dot's, which is the ::before box.
_DOT_TINTS = ["approved retirement status"]

_TINT_PROBE = """
([tints, dots]) => {
  const out = {};
  for (const [name, classes] of Object.entries(tints)) {
    const el = document.createElement('span');
    el.className = classes.join(' ');
    el.textContent = name;
    document.body.appendChild(el);
    const s = getComputedStyle(el);
    // An outcome is a status dot and one word: its signal is the dot's fill (the ::before box); a
    // notice has no dot and carries its signal in its text colour.
    const dot = getComputedStyle(el, '::before');
    const hasDot = dots.includes(name) || (dot.content !== 'none' && dot.content !== 'normal');
    out[name] = hasDot ? dot.backgroundColor : s.color;
    el.remove();
  }
  return out;
}
"""


def _tints(firefox, server, theme):
    with open_probe(firefox, server, width=WIDE[0], height=WIDE[1], theme=theme) as probe:
        probe.page.goto(probe.server.base_url + "/ai-advisor")
        wait_until(
            probe,
            "(t) => document.documentElement.getAttribute('data-theme') === t",
            f"the {theme} theme was never applied",
            arg=theme or "light",
        )
        return probe.page.evaluate(_TINT_PROBE, [_TINT_CLASSES, _DOT_TINTS])


def test_each_of_the_five_tints_changes_with_the_theme(firefox, advisor_server):
    """a signal colour built from a token differs between light and dark; a literal does not."""
    light = _tints(firefox, advisor_server, None)
    dark = _tints(firefox, advisor_server, "dark")
    fixed = [name for name in _TINT_CLASSES if light[name] == dark[name]]
    assert fixed == [], f"these tints are identical in light and dark: {fixed}"


_GEAR_AGAINST_CHAT_PANEL = """
() => {
  const gear = document.querySelector('[data-testid="tweaks-btn"]');
  const panel = document.querySelector('[data-testid="chat-panel"]');
  const g = gear.getBoundingClientRect();
  const p = panel.getBoundingClientRect();
  const style = getComputedStyle(gear);
  const rendered = style.display !== 'none' && style.visibility !== 'hidden'
    && g.width > 0 && g.height > 0;
  const overlaps = rendered && g.left < p.right && g.right > p.left
    && g.top < p.bottom && g.bottom > p.top;
  const top = document.elementFromPoint((g.left + g.right) / 2, (g.top + g.bottom) / 2);
  const onTop = rendered && !!top && (top === gear || gear.contains(top));
  return { rendered, overlaps, onTop, gear: [g.left, g.top, g.right, g.bottom],
           panel: [p.left, p.top, p.right, p.bottom] };
}
"""

_OPEN_CHAT = (
    "() => window.openChatPanel({artifactId: 'gear-check', artifactType: 'asset_swap',"
    " title: 'Explain', contextLabel: 'Asset Swap', objective: 'check', gateDecision: null,"
    " keyStat: '', artifactContext: {}})"
)


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_the_floating_gear_does_not_sit_over_the_open_chat_panel_on_a_phone(
    firefox, advisor_server, width, height
):
    """the gear steps aside while the chat panel is open (as it does for the detail panel) and is
    back, on top and rendered, when the panel closes: hiding it for good would also fail."""
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        probe.page.goto(probe.server.base_url + "/ai-advisor")
        wait_until(
            probe,
            "() => typeof window.openChatPanel === 'function'",
            "the Advisor never defined openChatPanel (the function every Discuss button calls)",
        )
        closed = probe.page.evaluate(_GEAR_AGAINST_CHAT_PANEL)
        assert closed["rendered"] and closed["onTop"], (
            f"precondition: with the panel closed the gear is on screen and on top: {closed}"
        )

        probe.page.evaluate(_OPEN_CHAT)
        wait_until(
            probe,
            "() => !!document.querySelector('.chat-panel.chat-panel--open')",
            "the chat panel never opened",
        )
        probe.page.wait_for_timeout(600)  # the panel slides in over 0.25 s
        opened = probe.page.evaluate(_GEAR_AGAINST_CHAT_PANEL)
        assert not (opened["overlaps"] and opened["onTop"]), (
            f"the gear sits over the open chat panel at {width} px: {opened}"
        )

        probe.page.evaluate("() => window.closeChatPanel()")
        probe.page.wait_for_timeout(600)
        reclosed = probe.page.evaluate(_GEAR_AGAINST_CHAT_PANEL)
        assert reclosed["rendered"] and reclosed["onTop"], (
            f"the gear did not come back when the chat panel closed: {reclosed}"
        )
