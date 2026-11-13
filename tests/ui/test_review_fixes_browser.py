"""The UX review's layout and rendered-word fixes, in a real browser (plan 06 AC-4 to AC-7).

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_review_fixes_browser.py``). Firefox headless through
Playwright, imported inside a fixture; a missing Playwright or Firefox fails with a message naming
it, never a skip. The server is the app in this process on the per-test database, seeded through the
real writers from captured records (``tests/ui/_shell_support``); every request to anything but that
server is refused and the developer ``.env`` is never read.

Layout facts (a box scrolls inside, text does not collide, an element is on screen, a column stays
put) are asserted from geometry and computed style by their relations; no colour, size or position is
compared with a number copied from a capture. What only a human can judge (is the card well
composed, are the Approve buttons aligned) is left to the next capture and named in the handoff.
"""

from __future__ import annotations

import os

import pytest

import database
from tests._advisor_observation_records import store
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
    document_scroll_width,
    firefox,
    open_page,
    open_probe,
    seed_dashboard_state,
    seed_every_page,
    seed_pending_frontrunner_proposal,
    wait_until,
)

pytestmark = pytest.mark.browser

SIZES = [NARROW, VERY_NARROW]
SIZE_IDS = ["375", "320"]
THEMES = ["light", "dark"]
SYMPHONY_ID = "iaSOOUsmnCJHiZvbrWfs"  # a captured state entry (tests/fixtures/prod_droplet)


def _seed_survivor() -> None:
    """One Strategy Builder survivor row, so the tab has a card with a Discuss button."""
    database.insert_advisor_observation(
        advisor_role="STRATEGY_BUILDER",
        subject_type="strategy_builder",
        subject_id="review-fixes-survivor",
        verdict="ADOPT_CANDIDATE",
        raw_response={
            "sharpe": 1.2,
            "cagr": 0.11,
            "calmar": 0.5,
            "max_drawdown": -0.2421,
            "candidate_hash": "reviewfixes0001",
            "template_id": "built-new",
        },
        symphony_id="",
    )


# ---------------------------------------------------------------------------
# AC-4: phone width
# ---------------------------------------------------------------------------

_TEXT_COLLISIONS = """
(root) => {
  const scope = document.querySelector(root);
  if (!scope) return { found: false, collisions: [] };
  const leaves = [...scope.querySelectorAll('*')].filter((e) => {
    if (e.children.length) return false;
    if (!e.textContent.trim()) return false;
    const s = getComputedStyle(e);
    const r = e.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && r.width > 0 && r.height > 0;
  });
  const out = [];
  for (let i = 0; i < leaves.length; i++) {
    for (let j = i + 1; j < leaves.length; j++) {
      const a = leaves[i].getBoundingClientRect();
      const b = leaves[j].getBoundingClientRect();
      const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
      const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
      if (w > 1 && h > 1) out.push([leaves[i].textContent.trim().slice(0, 14), leaves[j].textContent.trim().slice(0, 14)]);
    }
  }
  return { found: true, collisions: out.slice(0, 6) };
}
"""


@pytest.mark.parametrize("width, height", SIZES, ids=SIZE_IDS)
def test_card_stat_grids_have_no_colliding_text(firefox, advisor_server, width, height):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/")
        cards = probe.page.locator('[data-testid="sym-card"]')
        assert cards.count() >= 3
        for index in range(cards.count()):
            sym = cards.nth(index).get_attribute("data-sym-id")
            facts = probe.page.evaluate(
                _TEXT_COLLISIONS, f'.sym-card[data-sym-id="{sym}"] .card-footer-grid'
            )
            assert facts["found"], f"card {sym} has no stat grid"
            assert facts["collisions"] == [], (
                f"card {sym} at {width} px: texts overlap in the stat grid: {facts['collisions']}"
            )


@pytest.mark.parametrize("width, height", SIZES, ids=SIZE_IDS)
def test_the_window_picker_wraps_as_a_row_and_the_hero_text_reads_normally(
    firefox, advisor_server, width, height
):
    seed_dashboard_state()
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/")
        facts = probe.page.evaluate(
            """
            () => {
              const picker = document.querySelector('[data-testid="window-selector"]');
              const buttons = [...picker.querySelectorAll('button')];
              const rows = new Set(buttons.map((b) => Math.round(b.getBoundingClientRect().top)));
              const descriptor = document.querySelector('.guard-alpha-descriptor');
              const lineHeight = parseFloat(getComputedStyle(descriptor).lineHeight) || 16;
              const legend = [...document.querySelectorAll('[data-testid="chart-legend"] .legend-item')];
              const legendRows = new Set(legend.map((e) => Math.round(e.getBoundingClientRect().top)));
              return {
                pickerWidth: picker.getBoundingClientRect().width, vw: innerWidth, pickerRows: rows.size,
                descriptorLines: Math.round(descriptor.getBoundingClientRect().height / lineHeight),
                descriptorWidth: descriptor.getBoundingClientRect().width,
                legendRows: legendRows.size, legendCount: legend.length,
                docWidth: document.documentElement.scrollWidth,
              };
            }
            """
        )
        assert facts["pickerWidth"] >= 0.6 * facts["vw"], (
            f"the window picker is a thin column {facts['pickerWidth']:.0f} px wide in {facts['vw']} px"
        )
        assert facts["pickerRows"] <= 2, (
            f"the picker's buttons fall into {facts['pickerRows']} rows"
        )
        assert facts["descriptorLines"] <= 4 and facts["descriptorWidth"] >= 100, (
            f"the headline sentence wraps to {facts['descriptorLines']} lines in "
            f"{facts['descriptorWidth']:.0f} px"
        )
        assert facts["legendRows"] <= 2, f"the chart legend wraps into {facts['legendRows']} rows"
        assert facts["docWidth"] <= facts["vw"], "the dashboard scrolls sideways"


def test_settings_does_not_scroll_sideways_at_320_in_any_section(
    firefox, advisor_server, tmp_path, monkeypatch
):
    seed_every_page(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=VERY_NARROW[0], height=VERY_NARROW[1]) as probe:
        open_page(probe, "/settings")
        wide = []
        scroll, viewport = document_scroll_width(probe)
        if scroll != viewport:
            wide.append(("initial view", scroll))
        sections = probe.page.locator(".sn-btn[data-section]")
        for index in range(sections.count()):
            button = sections.nth(index)
            name = button.get_attribute("data-section")
            button.click()
            if name == "symphonies":
                probe.page.locator(".sym-list-btn").first.click()
                probe.page.wait_for_selector(".override-grid .override-input", state="attached")
            probe.page.wait_for_timeout(250)
            scroll, viewport = document_scroll_width(probe)
            if scroll != viewport:
                wide.append((name, scroll))
        assert wide == [], f"/settings at 320 px is wider than the screen in: {wide}"


def test_an_open_observation_history_does_not_widen_the_page_at_375(
    firefox, advisor_server, tmp_path, monkeypatch
):
    store("guardrail_clear", times=4)
    store("guardrail_watch", times=2)
    with open_probe(firefox, advisor_server, width=NARROW[0], height=NARROW[1]) as probe:
        open_page(probe, "/ai-advisor")
        probe.page.evaluate(
            "() => document.querySelectorAll('details.obs-history').forEach((d) => { d.open = true; })"
        )
        probe.page.wait_for_timeout(300)
        scroll, viewport = document_scroll_width(probe)
        assert scroll == viewport, f"an open history makes the page {scroll} px wide in {viewport}"


@pytest.mark.parametrize("width, height", SIZES, ids=SIZE_IDS)
def test_advisor_tabs_are_one_scrolling_row_so_content_is_on_the_first_screen(
    firefox, advisor_server, width, height
):
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/ai-advisor")
        facts = probe.page.evaluate(
            """
            () => {
              const nav = document.querySelector('.cap-nav');
              const tabs = [...nav.querySelectorAll('[role="tab"]')];
              const rows = new Set(tabs.map((t) => Math.round(t.getBoundingClientRect().top)));
              const before = nav.scrollLeft;
              nav.scrollLeft = nav.scrollWidth;
              const moved = nav.scrollLeft > before;
              nav.scrollLeft = before;
              const panel = document.querySelector('.tab-panel--active');
              return { rows: rows.size, overflowX: getComputedStyle(nav).overflowX, moved,
                       navHeight: nav.getBoundingClientRect().height, panelTop: panel.getBoundingClientRect().top,
                       vh: innerHeight };
            }
            """
        )
        assert facts["rows"] == 1, f"the {facts['rows']} rows of tabs push the content down"
        assert facts["overflowX"] in ("auto", "scroll") and facts["moved"], (
            "the single row of tabs must scroll sideways"
        )
        assert facts["panelTop"] < 0.5 * facts["vh"], (
            f"the first content starts at {facts['panelTop']:.0f} px of a {facts['vh']} px screen"
        )


@pytest.mark.parametrize("width, height", SIZES, ids=SIZE_IDS)
def test_the_heatmap_name_column_stays_while_its_columns_scroll(
    firefox, advisor_server, width, height
):
    captures.seed_run_from_captures(os.environ["DB_PATH"])
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/ai-advisor", fragment="#correlations")
        probe.page.wait_for_selector('[data-testid="correlation-matrix-table"]', state="visible")
        facts = probe.page.evaluate(
            """
            () => {
              const box = document.querySelector('.matrix-scroll');
              box.scrollLeft = box.scrollWidth;
              const name = document.querySelector('th.corr-row-label');
              const b = box.getBoundingClientRect();
              const r = name.getBoundingClientRect();
              return { scrolled: box.scrollLeft > 0, nameLeft: r.left - b.left, nameRight: r.right - b.left,
                       boxWidth: b.width };
            }
            """
        )
        assert facts["scrolled"], "premise: the matrix is wider than its box"
        assert facts["nameLeft"] >= -1 and facts["nameRight"] <= facts["boxWidth"] + 1, (
            f"the name column scrolled out of view (left {facts['nameLeft']:.0f}, "
            f"right {facts['nameRight']:.0f} of {facts['boxWidth']:.0f})"
        )


# ---------------------------------------------------------------------------
# AC-5: the settings modal shows what it holds
# ---------------------------------------------------------------------------

_MODAL_FACTS = """
() => {
  const surface = document.querySelector('[data-modal-surface]');
  const body = [...surface.children].find((c) => getComputedStyle(c).overflowY === 'auto');
  const clipped = [...body.querySelectorAll('*')].filter((e) => {
    const oy = getComputedStyle(e).overflowY;
    return (oy === 'hidden' || oy === 'clip') && e.scrollHeight > e.clientHeight + 1 && e.clientHeight < e.scrollHeight - 3;
  }).map((e) => e.tagName + ':' + e.textContent.trim().slice(0, 24));
  const keys = [...body.querySelectorAll('.sym-settings-locked-var-btn')].map((b) => b.dataset.key);
  const rows = keys.map((key) => {
    const hit = [...body.querySelectorAll('div')].find((d) => d.children.length >= 2
      && d.children[0].textContent.trim().startsWith(key) && !d.querySelector('button'));
    if (!hit) return { key, found: false };
    hit.scrollIntoView({ block: 'center' });
    const r = hit.getBoundingClientRect();
    const br = body.getBoundingClientRect();
    return { key, found: true, height: r.height, inBody: r.top >= br.top - 1 && r.bottom <= br.bottom + 1,
             hasValue: /\\d/.test(hit.children[hit.children.length - 1].textContent) };
  });
  const lockButtons = [...body.querySelectorAll('.sym-settings-locked-var-btn')].map((b) => {
    const label = [...b.querySelectorAll('span')].find((s) => s.textContent.trim() === b.dataset.key);
    const s = label ? getComputedStyle(label) : null;
    return { key: b.dataset.key, found: !!label,
             truncated: !!label && (label.scrollWidth > label.clientWidth + 1),
             ellipsis: !!label && s.textOverflow === 'ellipsis' && s.whiteSpace === 'nowrap' };
  });
  const cards = [...body.querySelectorAll('[data-testid="sym-settings-advisor-card"], .sym-settings-advisor-card')];
  return { clipped, rows, lockButtons, cardHeights: cards.map((c) => c.getBoundingClientRect().height) };
}
"""


def _open_modal(probe):
    probe.page.locator(f'button[data-symphony-id="{SYMPHONY_ID}"]').first.click()
    probe.page.wait_for_selector("[data-modal-surface]")
    wait_until(
        probe,
        "() => [...document.querySelectorAll('[data-modal-surface] button')]"
        ".some((b) => b.textContent.trim() === 'Cancel')",
        "the settings modal never rendered its footer",
    )


def _seed_modal_state() -> None:
    state = seed_dashboard_state()
    name = database.normalize_name(state[SYMPHONY_ID]["name"])
    for role, verdict in (("OVERFITTING_CONSCIENCE", "CLEAR"), ("SPEC_CRITIC", "WATCH")):
        database.insert_advisor_observation(
            advisor_role=role,
            subject_type="autotune_run",
            subject_id="modal-fixture",
            verdict=verdict,
            raw_response={"note": "a note the modal shows"},
            symphony_id=name,
        )


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_the_modal_shows_its_parameters_and_advisor_sections_and_the_body_scrolls(
    firefox, advisor_server, width, height, theme
):
    _seed_modal_state()
    with open_probe(firefox, advisor_server, width=width, height=height, theme=theme) as probe:
        open_page(probe, "/")
        _open_modal(probe)
        facts = probe.page.evaluate(_MODAL_FACTS)
        assert facts["clipped"] == [], (
            f"sections of the modal are squashed to their heading: {facts['clipped']}"
        )
        assert facts["rows"], "the modal lists no autotuner parameter"
        for row in facts["rows"]:
            assert row["found"], f"{row['key']} has no row in the modal"
            assert row["height"] >= 14, f"{row['key']}'s row is {row['height']:.0f} px tall"
            assert row["inBody"], f"{row['key']}'s row cannot be scrolled into the body"
            assert row["hasValue"], f"{row['key']}'s row shows no value"
        assert facts["cardHeights"] and all(h >= 20 for h in facts["cardHeights"]), (
            f"the AI Advisor cards are squashed: {facts['cardHeights']}"
        )
        scrolls = probe.page.evaluate(
            """
            () => {
              const s = document.querySelector('[data-modal-surface]');
              const b = [...s.children].find((c) => getComputedStyle(c).overflowY === 'auto');
              b.scrollTop = b.scrollHeight;
              return b.scrollTop > 0 || b.scrollHeight <= b.clientHeight;
            }
            """
        )
        assert scrolls, "the modal body does not scroll"


@pytest.mark.parametrize("width, height", [NARROW, VERY_NARROW], ids=["375", "320"])
def test_lock_controls_show_each_variables_full_name(firefox, advisor_server, width, height):
    _seed_modal_state()
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/")
        _open_modal(probe)
        facts = probe.page.evaluate(_MODAL_FACTS)
        buttons = facts["lockButtons"]
        assert len(buttons) >= 6
        assert all(b["found"] for b in buttons), "a lock control does not carry its variable's name"
        assert [b["key"] for b in buttons if b["truncated"] or b["ellipsis"]] == [], (
            "lock controls cut their variable's name: "
            f"{[b['key'] for b in buttons if b['truncated'] or b['ellipsis']]}"
        )
        names = [b["key"] for b in buttons]
        assert len(set(names)) == len(names)


# ---------------------------------------------------------------------------
# AC-6: words on screen
# ---------------------------------------------------------------------------

_GREEK_UNDER_UPPERCASE = """
() => {
  const bad = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!/[\\u03b1\\u03c1]/.test(node.nodeValue)) continue;
    const el = node.parentElement;
    if (!el || ['SCRIPT', 'STYLE', 'TEMPLATE'].includes(el.tagName)) continue;
    const r = el.getBoundingClientRect();
    const hidden = getComputedStyle(el).display === 'none' || (r.width === 0 && r.height === 0);
    if (hidden) continue;
    if (getComputedStyle(el).textTransform === 'uppercase') {
      bad.push(el.tagName + '.' + String(el.className).slice(0, 24) + ': ' + node.nodeValue.trim().slice(0, 30));
    }
  }
  return bad;
}
"""


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("path", ["/", "/history", "/performance", "/ai-advisor"])
def test_alpha_and_rho_are_never_uppercased_into_latin_letters(
    firefox, advisor_server, tmp_path, monkeypatch, path, theme
):
    seed_every_page(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1], theme=theme) as probe:
        open_page(probe, path)
        if path == "/history":
            # The captured days are older than the default 30-day window: pick 5Y so the
            # strip and the cards, which hold the labels, are shown.
            probe.page.click('#window-picker button[data-window="1825"]')
            probe.page.wait_for_selector('[data-testid="reason-card"]', state="visible")
        probe.page.wait_for_timeout(800)
        assert probe.page.evaluate(_GREEK_UNDER_UPPERCASE) == [], (
            f"{path}: a Greek letter sits under text-transform: uppercase and renders as A or P"
        )


def test_the_alpha_helper_is_one_shared_rule_not_one_per_page():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    pages = ["history.html", "performance.html", "index.html", "ai_advisor.html"]
    per_page = [
        p
        for p in pages
        if ".alpha-glyph" in (root / "templates" / p).read_text(encoding="utf-8")
        and "text-transform: none" in (root / "templates" / p).read_text(encoding="utf-8")
        and ".alpha-glyph {" in (root / "templates" / p).read_text(encoding="utf-8")
    ]
    assert per_page == [], f"pages that define their own alpha rule: {per_page}"
    shared = [
        p.name
        for p in (root / "static").glob("*.css")
        if ".alpha-glyph" in p.read_text(encoding="utf-8")
    ]
    assert len(shared) == 1, f"the glyph rule belongs in exactly one shared stylesheet: {shared}"


@pytest.mark.parametrize("path", ["/", "/history", "/performance", "/ai-advisor", "/settings"])
def test_native_buttons_and_selects_use_the_app_font(
    firefox, advisor_server, tmp_path, monkeypatch, path
):
    seed_every_page(tmp_path, monkeypatch.setattr)
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, path)
        probe.page.wait_for_timeout(500)
        facts = probe.page.evaluate(
            """
            () => {
              const app = getComputedStyle(document.body).fontFamily.split(',')[0].trim();
              const off = [...document.querySelectorAll('button, select, input[type="button"], input[type="submit"]')]
                .filter((e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })
                .filter((e) => getComputedStyle(e).fontFamily.split(',')[0].trim() !== app)
                .map((e) => (e.getAttribute('data-testid') || e.id || e.className || e.tagName).toString().slice(0, 30));
              return { app, off };
            }
            """
        )
        assert facts["off"] == [], (
            f"{path}: controls in a font other than {facts['app']}: {facts['off']}"
        )


def test_the_retirement_rows_open_tab_link_reads_as_a_link(firefox, advisor_server):
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/ai-advisor")
        facts = probe.page.evaluate(
            """
            () => {
              const a = document.querySelector(
                '.obs-producer[data-role="RETIREMENT"] [data-testid="retirement-pointer"]');
              const s = getComputedStyle(a);
              const accent = (() => { const p = document.createElement('span'); p.style.color = 'var(--studio-accent)';
                                      document.body.appendChild(p); const c = getComputedStyle(p).color; p.remove(); return c; })();
              return { tag: a.tagName, text: a.textContent.trim(),
                       underline: s.textDecorationLine.includes('underline'), color: s.color,
                       accent, size: parseFloat(s.fontSize), upper: s.textTransform === 'uppercase' };
            }
            """
        )
        assert facts["tag"] == "A" and facts["text"] == "Open tab"
        assert facts["underline"] or facts["color"] == facts["accent"], (
            "nothing marks the Open tab link as a link to the Retirement tab"
        )
        assert not facts["upper"], "the link is set in capitals"


# ---------------------------------------------------------------------------
# AC-7: Advisor layout
# ---------------------------------------------------------------------------


def test_the_autotune_block_takes_the_width_when_suggestions_hold_only_their_empty_state(
    firefox, advisor_server
):
    for index in range(3):
        database.save_autotune_run(
            f"2026-10-0{index + 1}T04:00:00",
            f"layoutsym{index}",
            0.5,
            1.0,
            "Reverted to Fallback",
            0.4,
            0.3,
        )
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/ai-advisor")
        probe.page.wait_for_selector('[data-testid="autotune-run-row"]', state="attached")
        facts = probe.page.evaluate(
            """
            () => {
              const grid = document.querySelector('[data-testid="advisor-grid"]').getBoundingClientRect();
              const panel = document.querySelector('[data-testid="autotune-panel"]').getBoundingClientRect();
              const col = document.querySelector('[data-testid="suggestions-column"]');
              const placeholder = col.querySelector('[data-testid="suggestions-placeholder"]');
              return { gridW: grid.width, panelW: panel.width, onlyPlaceholder: col.children.length === 1 && !!placeholder };
            }
            """
        )
        assert facts["onlyPlaceholder"], "premise: no suggestions have been requested"
        assert facts["panelW"] >= 0.85 * facts["gridW"], (
            f"the autotune block is {facts['panelW']:.0f} px of a {facts['gridW']:.0f} px grid, "
            "beside a column that holds one line"
        )


@pytest.mark.parametrize("width, height", [WIDE, NARROW], ids=["1440", "375"])
def test_discuss_opens_the_chat_tab_with_its_context_and_the_chat_in_view(
    firefox, advisor_server, width, height
):
    _seed_survivor()
    with open_probe(firefox, advisor_server, width=width, height=height) as probe:
        open_page(probe, "/ai-advisor", fragment="#strategy-builder")
        probe.page.wait_for_selector('[data-testid="discuss-proposal-btn"]', state="attached")
        probe.page.locator('[data-testid="discuss-proposal-btn"]').first.click()
        probe.page.wait_for_timeout(800)
        facts = probe.page.evaluate(
            """
            () => {
              const chatTab = document.querySelector('[role="tabpanel"][data-tab="chat"]');
              const panel = document.querySelector('[data-testid="chat-panel"]');
              const r = panel.getBoundingClientRect();
              const title = document.getElementById('chat-panel-title');
              const objective = document.getElementById('artifact-objective');
              const keyStat = document.getElementById('artifact-key-stat');
              return { chatActive: chatTab.classList.contains('tab-panel--active'),
                       open: panel.classList.contains('chat-panel--open'),
                       left: r.left, right: r.right, vw: innerWidth,
                       titled: !!title && title.textContent.trim().length > 0 && title.textContent.trim() !== 'Explain',
                       context: (!!objective && objective.textContent.trim().length > 0)
                         || (!!keyStat && keyStat.textContent.trim().length > 0),
                       hash: location.hash, path: location.pathname };
            }
            """
        )
        assert facts["path"] == "/ai-advisor", f"Discuss navigated to {facts['path']}"
        assert facts["chatActive"], "the Chat tab did not open"
        assert facts["open"], "the chat panel did not open"
        assert facts["left"] >= -0.5 and facts["right"] <= facts["vw"] + 0.5, (
            "the chat panel is outside the viewport"
        )
        assert facts["titled"] and facts["context"], "the chat opened without the card's context"


def test_the_chat_route_keeps_its_tab():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        response = client.get("/ai-advisor/chat?from_strategy_builder=1&symphony_id=x")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("#chat"), response.headers["Location"]


def test_heatmap_cells_show_depth_without_hover_and_thin_marker_keeps_the_fill_readable(
    firefox, advisor_server
):
    captures.seed_run_from_captures(os.environ["DB_PATH"])
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/ai-advisor", fragment="#correlations")
        probe.page.wait_for_selector('[data-testid="correlation-matrix-table"]', state="visible")
        facts = probe.page.evaluate(
            """
            () => {
              const cells = [...document.querySelectorAll('td[data-testid="corr-cell"]')];
              const missingDepth = cells.filter((c) => {
                const n = c.getAttribute('data-n-obs');
                return n !== null && n !== '' && !new RegExp('(^|[^0-9.])' + n + '([^0-9]|$)').test(c.textContent);
              }).length;
              const thin = cells.filter((c) => c.getAttribute('data-thin') !== 'none');
              const hatched = thin.filter((c) => /repeating/.test(getComputedStyle(c).backgroundImage)).length;
              const unmarked = thin.filter((c) => {
                const after = getComputedStyle(c, '::after').content;
                const before = getComputedStyle(c, '::before').content;
                const hasMarkerEl = !!c.querySelector('[data-testid="corr-thin-marker"], .corr-thin-marker');
                const shown = (v) => v && v !== 'none' && v !== 'normal' && v !== '""';
                return !(shown(after) || shown(before) || hasMarkerEl);
              }).length;
              const filled = thin.filter((c) => getComputedStyle(c).backgroundColor !== 'rgba(0, 0, 0, 0)').length;
              return { cells: cells.length, missingDepth, thin: thin.length, hatched, unmarked, filled };
            }
            """
        )
        assert facts["cells"] > 0 and facts["thin"] > 0, "premise: the captured run has thin pairs"
        assert facts["missingDepth"] == 0, (
            f"{facts['missingDepth']} cells show their depth only in a hover title"
        )
        assert facts["hatched"] == 0, "digits still sit on diagonal stripes in thin cells"
        assert facts["unmarked"] == 0, "thin cells carry no marker of any kind"
        assert facts["filled"] > 0, "the fill the operator asked for is lost under the marker"


def test_each_symphony_first_and_last_series_dates_are_on_the_correlations_tab(
    firefox, advisor_server
):
    summary = captures.seed_run_from_captures(os.environ["DB_PATH"])
    depth = summary["series_windows"]
    assert depth, "premise: the run stored per-symphony series windows"
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/ai-advisor", fragment="#correlations")
        probe.page.wait_for_selector('[data-testid="correlation-matrix-table"]', state="visible")
        text = probe.page.locator('[data-testid="tab-panel-correlations"]').inner_text()
        for symphony_id, item in depth.items():
            assert item["series_first"] in text, f"{symphony_id}: first series date not on the tab"
            assert item["series_last"] in text, f"{symphony_id}: last series date not on the tab"


def test_frontrunner_list_states_identity_once_and_the_card_keeps_its_overlay_line(
    firefox, advisor_server
):
    seed_pending_frontrunner_proposal()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/ai-advisor", fragment="#frontrunner-builder")
        probe.page.wait_for_selector('[data-testid="frontrunner-proposal-card"]', state="visible")
        facts = probe.page.evaluate(
            """
            () => {
              const card = document.querySelector('[data-testid="frontrunner-proposal-card"]');
              const shared = document.querySelector('[data-testid="fr-shared-notes"]');
              // The "On approval" line restated the risk banner and is gone: the shared notes hold
              // the Identity line alone, which says the incumbent is never modified.
              const lines = [
                shared.querySelector('[data-testid="fr-identity-note"]'),
                card.querySelector('[data-testid="fr-overlay-summary"]'),
              ];
              const labels = lines.map((l) => l && l.querySelector('.fr-shared-label, .fr-line-label'));
              const overlayRaw = 'relative-strength-index(SPY,10) gt 80 rotates into UVXY';
              const preview = card.querySelector('[data-testid="fr-overlay-preview"]');
              const inLines = lines.filter((l) => l && l.textContent.includes(overlayRaw)).length;
              return { present: lines.map((l) => !!l), labelled: labels.map((l) => !!l && l.textContent.trim().length > 0),
                       labelTexts: labels.map((l) => l ? l.textContent.trim() : null),
                       rawInLines: inLines, rawInPreview: !!preview && preview.textContent.includes(overlayRaw),
                       nodeDelta: (card.querySelector('[data-testid="fr-node-count-delta"]') || {}).textContent };
            }
            """
        )
        assert all(facts["present"]), (
            f"the list or the card lacks one of its two lines: {facts['present']}"
        )
        assert all(facts["labelled"]), f"a line has no label: {facts['labelTexts']}"
        assert len(set(facts["labelTexts"])) == 2
        assert probe.page.locator('[data-testid="fr-explainer-line"]').count() == 0, (
            "the On approval line is back"
        )
        assert facts["rawInLines"] == 0, "the raw overlay string is still in the paragraph"
        assert facts["rawInPreview"], "the raw overlay is not under its preview"
        assert "incumbent" in " ".join((facts["nodeDelta"] or "").split()).lower(), (
            "the node count delta is an unexplained number"
        )


def test_the_actionable_retirement_card_comes_before_the_blocks_around_it(firefox, advisor_server):
    from tests.advisors.test_retirement_recommender_composer_source import _series_cluster_scenario
    from tests.app import _retirement_tab_world as w

    mp = pytest.MonkeyPatch()
    try:
        w.run_tick_with_recommendations(mp, _series_cluster_scenario())
    finally:
        mp.undo()
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        open_page(probe, "/ai-advisor", fragment="#retirement")
        probe.page.wait_for_selector('[data-testid="retirement-approve-btn"]', state="attached")
        tops = probe.page.evaluate(
            """
            () => {
              const top = (sel) => { const e = document.querySelector(sel); return e ? e.getBoundingClientRect().top + scrollY : null; };
              return { card: top('[data-testid="retirement-recommendation-card"]'),
                       lastRun: top('[data-testid="retirement-last-run"]'),
                       tenure: top('[data-testid="tenure-review-entry"]') };
            }
            """
        )
        assert tops["card"] is not None
        for name in ("lastRun", "tenure"):
            if tops[name] is not None:
                assert tops["card"] < tops[name], (
                    f"the recommendation to act on is below the {name} block"
                )
