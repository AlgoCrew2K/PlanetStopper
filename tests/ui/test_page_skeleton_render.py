"""Every page opens on the dashboard's skeleton: no title card, one panel primitive, one card, one
label, one stat row, one number face.

Subject: the server-rendered History, Performance, Settings and AI Advisor pages (and the card
renderers in ``static/history.js`` and ``static/ai_advisor*.js``). The dashboard's primitives
(``.hero-section``, ``.sym-card`` with ``.card-name``, ``.section-header``, ``.mini-stats`` with its
parts, ``.legend-as-of``) are defined once, in ``static/layout.css``; a page uses them and keeps
only layout rules beside them.

Method: Flask test-client renders of the real routes parsed into a small tree
(``tests/_ux_truth_support``), source scans of the templates and renderers, and a scan of every
page style block for a rule that gives a former page-local primitive a radius, border, padding,
font, colour or shadow. Class and token usage are asserted, never a computed colour or size; a
rendered page is judged in ``tests/ui/test_page_skeleton_browser.py`` and on the screenshot gate.

Titles and back links are gone, so the legacy pins that required them were updated with this file
(``tests/ui/test_cycle_5_history.py``, ``tests/ui/test_cycle_4_advisor.py``,
``tests/ai_advisor/test_r1_attribution_honesty.py``,
``tests/ai_advisor/test_advisor_observations_ui.py``).
"""

from __future__ import annotations

import pathlib
import re

import pytest

import app as app_module
from tests._ux_truth_support import Node, parse_html
from tests.ui import _dashboard_primitives_capture as cap
from tests.ui import _page_copy as page_copy
from tests.ui._shell_support import seed_pending_frontrunner_proposal

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates"
STATIC = REPO / "static"

CONTENT_PAGES = ["/history", "/performance", "/settings", "/ai-advisor"]
PAGE_TEMPLATES = ["history.html", "performance.html", "settings.html", "ai_advisor.html"]
PAGE_LINK = {
    "/history": "/history",
    "/performance": "/performance",
    "/settings": "/settings",
    "/ai-advisor": "/ai-advisor",
}

# Properties a page may set beside a primitive (layout only); anything starting with one of the
# forbidden prefixes would be a page-local value standing in for the primitive.
FORBIDDEN_PREFIXES = (
    "border",
    "padding",
    "font",
    "color",
    "background",
    "box-shadow",
    "letter-spacing",
    "text-transform",
    "line-height",
    "outline",
    "text-decoration",
)

# The page-local copies of a primitive that the pages carried before, by template. A rule on one
# of these may remain (a layout hook) but may not carry a primitive's values.
FORMER_LOCAL_PRIMITIVES = {
    "history.html": [
        ".hist-header",
        ".hist-title",
        ".hist-subtitle",
        ".back-link",
        ".history-hero",
        ".hero-stat",
        ".hero-stat-label",
        ".hero-stat-value",
        ".section-card",
        ".section-title",
        ".reason-card-inner",
        ".reason-name",
    ],
    "performance.html": [
        ".perf-header",
        ".perf-title",
        ".perf-subtitle",
        ".headline-strip",
        ".headline-stat",
        ".headline-stat-label",
        ".headline-stat-value",
        ".chart-block",
        ".chart-block-title",
        ".metrics-section",
        ".metrics-title",
    ],
    "settings.html": [
        ".settings-page-header",
        ".settings-page-title",
        ".settings-page-subtitle",
        ".sec-title",
    ],
    "ai_advisor.html": [
        ".adv-header",
        ".adv-title",
        ".adv-subtitle",
        ".back-link",
        ".proposal-card",
        ".autotune-panel",
        ".matrix-card",
        ".try-swap-panel",
        ".try-tweak-panel",
        ".run-controls-panel",
        ".prism-block",
        ".obs-section",
        ".autotune-run-card",
        ".card-candidate-id",
        ".section-header",
    ],
}

ADVISOR_PANEL_CLASSES = (
    "autotune-panel",
    "matrix-card",
    "try-swap-panel",
    "try-tweak-panel",
    "run-controls-panel",
    "prism-block",
    "obs-section",
)
ADVISOR_PANEL_HEADER_CLASSES = (
    "matrix-card-header",
    "autotune-header",
    "prism-block-title",
    "run-controls-header",
    "try-swap-header",
)
ADVISOR_CARD_CLASSES = ("proposal-card", "autotune-run-card", "retirement-rec-card")


def _token(name: str) -> re.Pattern:
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _style_css(template_name: str) -> str:
    html = _read(TEMPLATES / template_name)
    return "\n".join(
        re.findall(r"<style\b[^>]*>(.*?)</style>", html, flags=re.IGNORECASE | re.DOTALL)
    )


def _page(client, path: str) -> Node:
    response = client.get(path)
    assert response.status_code == 200, f"GET {path} answered {response.status_code}"
    return parse_html(response.get_data(as_text=True))


def _children(node: Node) -> list[Node]:
    return [c for c in node.children if isinstance(c, Node)]


def _wrap(root: Node) -> Node:
    wrap = root.find(cls="page-wrap")
    assert wrap is not None, "the page has no .page-wrap"
    return wrap


def _first_panel(root: Node) -> Node:
    first = _children(_wrap(root))[0]
    assert "hero-section" in first.classes, (
        f"the first element of .page-wrap is <{first.tag} class={first.classes}>, "
        "not a .hero-section panel"
    )
    return first


def _inside(node: Node, ancestor: Node) -> bool:
    cursor: Node | None = node
    while cursor is not None:
        if cursor is ancestor:
            return True
        cursor = cursor.parent
    return False


def _inline_props(node: Node) -> set[str]:
    style = node.attrs.get("style", "")
    return {d.split(":", 1)[0].strip().lower() for d in style.split(";") if ":" in d}


# ---------------------------------------------------------------------------
# no title card, no back link, content inside .page-wrap, captions kept
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", CONTENT_PAGES)
def test_the_page_has_no_title_card_and_no_back_link(client, path):
    root = _page(client, path)
    assert root.find_all("h1") == [], "the page still opens on an h1 title card"
    assert root.find_all(cls="back-link") == []
    wrap = _wrap(root)
    for anchor in wrap.find_all("a"):
        assert "dashboard" not in anchor.text.lower() or anchor.attrs.get("href") != "/", (
            "a Dashboard back link remains"
        )
    for testid in ("history-header", "advisor-header"):
        assert root.find(testid=testid) is None, f"{testid} (the old title header) remains"


@pytest.mark.parametrize("path", ["/history", "/performance", "/ai-advisor"])
def test_the_pages_first_element_is_a_panel_inside_page_wrap(client, path):
    """content starts inside the shared skeleton, with its first panel."""
    root = _page(client, path)
    first = _first_panel(root)
    assert not first.find_all("h1")
    outside = [n for n in root.find_all(cls="hero-section") if not _inside(n, _wrap(root))]
    assert outside == [], "a panel sits outside .page-wrap"


def test_settings_content_sits_inside_page_wrap_with_its_actions_row_first(client):
    """Save and Discard open the settings content, above the section body."""
    root = _page(client, "/settings")
    wrap = _wrap(root)
    body = root.find(cls="settings-body")
    assert body is not None and _inside(body, wrap), ".settings-body must be inside .page-wrap"
    first = _children(wrap)[0]
    save, discard = root.find(attr=("id", "btn-save")), root.find(attr=("id", "btn-discard"))
    assert save is not None and discard is not None
    assert _inside(save, first) and _inside(discard, first), (
        "Save and Discard must be in the first element of .page-wrap"
    )
    assert not _inside(first, body), "the actions row sits above .settings-body"
    content = root.find(cls="settings-content")
    assert content is not None
    assert not _inside(save, content) and not _inside(discard, content), (
        "Save and Discard stay outside the scrolling settings content"
    )


@pytest.mark.parametrize("path", CONTENT_PAGES)
def test_the_nav_link_for_the_page_carries_aria_current(client, path):
    root = _page(client, path)
    nav = root.find(testid="studio-nav")
    assert nav is not None
    current = [a for a in nav.find_all("a") if a.attrs.get("aria-current") == "page"]
    assert [a.attrs.get("href") for a in current] == [PAGE_LINK[path]]


def test_history_caption_is_a_legend_in_the_first_panel(client):
    """History's description line kept as a caption."""
    root = _page(client, "/history")
    caption = root.find(testid="history-caption")
    assert caption is not None, "History has no data-testid=history-caption"
    assert "legend-as-of" in caption.classes
    assert _inside(caption, _first_panel(root))
    assert " ".join(caption.text.split()) == page_copy.text("Y02", "C")


def test_performance_basis_and_observation_captions_are_legends_in_the_first_panel(client):
    root = _page(client, "/performance")
    first = _first_panel(root)
    legends = [n for n in first.find_all(cls="legend-as-of")]
    basis = [n for n in legends if page_copy.text("F04", "C") in " ".join(n.text.split())]
    assert basis, "the basis sentence is not a .legend-as-of caption in the first panel"
    observation = root.find(testid="obs-caption")
    assert observation is not None
    assert "legend-as-of" in observation.classes and _inside(observation, first)
    assert observation.attrs.get("id") == "obs-caption"


def test_performance_observation_caption_reads_the_dash_before_a_count(client):
    """as at the ref."""
    root = _page(client, "/performance")
    assert root.find(testid="obs-caption").text == "--"


def test_advisor_description_is_a_legend_in_the_first_panel(client):
    root = _page(client, "/ai-advisor")
    caption = root.find(testid="advisor-caption")
    assert caption is not None, "the Advisor has no data-testid=advisor-caption"
    assert "legend-as-of" in caption.classes and _inside(caption, _first_panel(root))
    assert "operator must approve" in caption.text.lower()
    assert "claude-powered" not in caption.text.lower()


def test_settings_caption_is_a_legend_in_the_actions_row(client):
    """Settings keeps its description as a caption beside Save and Discard."""
    root = _page(client, "/settings")
    caption = root.find(testid="settings-caption")
    assert caption is not None, "Settings has no data-testid=settings-caption"
    assert "legend-as-of" in caption.classes
    assert _inside(caption, _children(_wrap(root))[0])
    assert ".env" in caption.text


def test_the_advisor_tab_bar_is_in_the_first_panel_with_every_tab_attribute(client):
    """the switcher reads role, aria-controls and data-tab; moving the bar keeps them."""
    root = _page(client, "/ai-advisor")
    first = _first_panel(root)
    tabs = [n for n in first.find_all("button") if "data-tab" in n.attrs]
    names = {t.attrs["data-tab"] for t in tabs}
    assert {
        "overview",
        "correlations",
        "retirement",
        "asset-swaps",
        "logic-changes",
        "chat",
        "strategy-builder",
        "frontrunner-builder",
    } <= names
    for tab in tabs:
        assert tab.attrs.get("role") == "tab"
        panel_id = tab.attrs.get("aria-controls")
        assert panel_id and root.find(attr=("id", panel_id)) is not None, tab.attrs


def test_save_and_discard_are_disabled_with_no_edit(client):
    """as at the ref."""
    root = _page(client, "/settings")
    for button_id in ("btn-save", "btn-discard"):
        button = root.find(attr=("id", button_id))
        assert button is not None and "disabled" in button.attrs, button_id


# ---------------------------------------------------------------------------
# panels, cards, labels, stat rows
# ---------------------------------------------------------------------------


def test_history_panels_labels_and_hero_figures_use_the_panel_and_figure_row(client):
    """The panels and headings are the shared primitives; the four headline figures are one row
    of figures (``.hp-figs``), not four boxed tiles."""
    root = _page(client, "/history")
    for testid in ("daily-strip", "by-reason-section", "recent-triggers"):
        panel = root.find(testid=testid)
        assert panel is not None and "hero-section" in panel.classes, testid
        assert panel.find(cls="section-header") is not None, f"{testid} has no .section-header"
    hero = root.find(testid="history-hero")
    assert hero is not None and hero.find(cls="hp-figs") is not None
    assert hero.find_all(cls="mini-stat") == [], "the headline figures are still boxed tiles"
    for testid in ("stat-total-alpha", "stat-total-saved", "stat-trigger-count", "stat-win-rate"):
        tile = hero.find(testid=testid)
        assert tile is not None and "hp-fig" in tile.classes, testid
        assert tile.find(cls="hp-fig-value") is not None
        assert tile.find(cls="hp-fig-label") is not None
    for old in ("section-card", "section-title", "hero-stat"):
        assert root.find_all(cls=old) == [], f"an element still carries .{old}"


def test_history_reason_rows_are_ledger_rows_with_a_dot_and_a_name(client):
    """the server-rendered placeholders: one table row per reason, its name led by a status dot,
    its seven cells under one header row, and no pill or card box."""
    root = _page(client, "/history")
    rows = root.find_all(testid="reason-card")
    assert len(rows) == 4
    for row in rows:
        assert row.tag == "tr"
        assert row.find(cls="hp-dot") is not None, "a reason row has no status dot"
        assert len(row.find_all("td")) == 7
        assert row.find(cls="status-pill") is None and "sym-card" not in row.classes
    assert len(root.find(testid="by-reason-section").find_all("th")) == 7


def test_history_empty_state_sits_in_the_panel_primitive_without_its_own_box(client):
    root = _page(client, "/history")
    empty = root.find(testid="history-empty-state")
    assert empty is not None
    panel = empty if "hero-section" in empty.classes else empty.ancestor_with_class("hero-section")
    assert panel is not None, "the empty state is not inside a .hero-section"
    own = _inline_props(empty)
    assert not [p for p in own if p.startswith(("border", "padding", "background"))], sorted(own)


def test_history_reason_rows_script_emits_a_dot_row_and_no_pill_or_box():
    """renderReasonCards draws a ledger row per reason: the status dot, the figures under the
    table's headers, and no pill, card or inline box."""
    source = _read(STATIC / "history.js")
    body = source[source.index("function renderReasonCards") :]
    body = body[: body.index("\n    function ", 10)]
    assert "reasonDotHtml(reason)" in body, "renderReasonCards emits no status dot"
    assert '<tr data-testid="reason-card">' in body
    for gone in ("status-pill", "sym-card", "card-name", "border-radius", "border:"):
        assert gone not in body, f"renderReasonCards still writes {gone}"


def test_performance_strip_charts_and_tables_use_the_primitives(client):
    root = _page(client, "/performance")
    strip = root.find(testid="headline-strip")
    assert strip is not None and "hp-figs" in strip.classes
    assert strip.find_all(cls="mini-stat") == [], "the headline figures are still boxed tiles"
    for testid in (
        "guard-alpha-stat",
        "sharpe-delta-stat",
        "mdd-reduction-stat",
        "sortino-delta-stat",
    ):
        tile = strip.find(testid=testid)
        assert tile is not None and "hp-fig" in tile.classes, testid
        assert tile.find(cls="hp-fig-value") is not None
        assert tile.find(cls="hp-fig-label") is not None
    for testid in (
        "perf-chart-block",
        "metrics-table",
        "guard-alpha-preconditions-panel",
        "exit-turnover-panel",
    ):
        panel = root.find(testid=testid)
        assert panel is not None and "hero-section" in panel.classes, testid
        assert panel.find(cls="section-header") is not None, f"{testid} has no .section-header"
        assert "margin-top" not in panel.attrs.get("style", ""), (
            f"{testid} keeps an inline margin-top the primitive's own margin replaces"
        )


def test_performance_has_no_monospace_face_in_its_rules():
    """table numbers take the sans face with tabular figures."""
    css = _style_css("performance.html")
    mono = [
        (selectors, dict(decls))
        for _ctx, selectors, decls in cap.iter_rules(css)
        if "monospace" in dict(decls).get("font-family", "")
    ]
    assert mono == [], f"rules still set a monospace face: {mono}"
    tabular = [
        selectors
        for _ctx, selectors, decls in cap.iter_rules(css)
        if dict(decls).get("font-variant-numeric") == "tabular-nums"
        and any(".metrics-tbl" in s for s in selectors)
    ]
    assert tabular, "the metrics tables do not take tabular figures"


def test_settings_sections_and_their_titles_use_the_primitives(client):
    root = _page(client, "/settings")
    sections = root.find_all(cls="settings-section")
    assert len(sections) == 5
    for section in sections:
        assert "hero-section" in section.classes, section.attrs.get("id")
        assert section.find(cls="section-header") is not None, section.attrs.get("id")
    assert root.find_all(cls="sec-title") == [] or all(
        "section-header" in n.classes for n in root.find_all(cls="sec-title")
    )
    live = root.find(attr=("id", "live-card"))
    assert live is not None and "sym-card" in live.classes and "data-live" in live.attrs


def _class_attribute_values(text: str) -> list[str]:
    return re.findall(r'class="([^"]*)"', text)


def _string_literals(text: str) -> list[str]:
    found = re.findall(r"""(?:"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`)""", text)
    return [a or b or c for a, b, c in found]


def test_every_advisor_panel_uses_the_panel_primitive_and_its_header_the_label():
    """Template scan."""
    template = _read(TEMPLATES / "ai_advisor.html")
    body = template[template.index("<body") :]
    values = _class_attribute_values(body)
    for name in ADVISOR_PANEL_CLASSES:
        holders = [v for v in values if _token(name).search(v)]
        assert holders, f"the Advisor template renders no .{name}"
        missing = [v for v in holders if "hero-section" not in v.split()]
        assert missing == [], f".{name} panels without .hero-section: {missing[:3]}"
    for name in ADVISOR_PANEL_HEADER_CLASSES:
        holders = [v for v in values if _token(name).search(v)]
        missing = [v for v in holders if "section-header" not in v.split()]
        assert missing == [], f".{name} headers without .section-header: {missing[:3]}"


def test_every_advisor_card_is_a_sym_card_and_names_itself_with_card_name():
    """Template and renderer scan: proposal, Frontrunner, retirement and run cards."""
    sources = {
        "templates/ai_advisor.html": _read(TEMPLATES / "ai_advisor.html"),
        **{f"static/{p.name}": _read(p) for p in sorted(STATIC.glob("ai_advisor*.js"))},
    }
    for label, text in sources.items():
        literals = (
            _class_attribute_values(text)
            if label.endswith(".html")
            else [s for s in _string_literals(text) if s]
        )
        for name in ADVISOR_CARD_CLASSES:
            for value in literals:
                if _token(name).search(value) and "proposal-cards" not in value.split():
                    assert _token("sym-card").search(value), (
                        f"{label}: a .{name} is not a .sym-card: {value[:90]!r}"
                    )
        assert "card-candidate-id" not in text, f"{label}: the name still uses .card-candidate-id"


def test_a_pending_frontrunner_card_is_a_sym_card_named_with_card_name(client):
    """Rendered: the card the real writer's row produces."""
    proposal_id = seed_pending_frontrunner_proposal()
    root = _page(client, "/ai-advisor")
    card = root.find(attr=("id", f"fr-card-{proposal_id}"))
    assert card is not None, "the pending proposal renders no card"
    assert "sym-card" in card.classes
    assert card.find(cls="card-name") is not None


# ---------------------------------------------------------------------------
# no page redefines a primitive; former page-local copies carry no primitive value
# ---------------------------------------------------------------------------


def _forbidden_declarations(decls) -> list[str]:
    return [p for p, _v in decls if p.startswith(FORBIDDEN_PREFIXES)]


@pytest.mark.parametrize("template", PAGE_TEMPLATES)
def test_a_former_page_local_primitive_carries_no_primitive_value(template):
    """no page-local radius, border, padding, font, colour or shadow stands in for one."""
    css = _style_css(template)
    names = FORMER_LOCAL_PRIMITIVES[template]
    offenders = []
    for context, selectors, decls in cap.iter_rules(css):
        for selector in selectors:
            last = selector.split()[-1] if selector.split() else selector
            for name in names:
                if re.match(rf"^{re.escape(name)}(?![\w-])", last):
                    bad = _forbidden_declarations(decls)
                    if bad:
                        offenders.append(f"{template}: {context or ''} {selector} sets {bad}")
    assert offenders == []


@pytest.mark.parametrize("template", PAGE_TEMPLATES)
def test_the_template_deletes_its_title_card_rules(template):
    """The title card and back link rules are gone with the markup."""
    css = _style_css(template)
    gone = [".hist-title", ".perf-title", ".adv-title", ".back-link", ".settings-page-title"]
    present = [g for g in gone if re.search(rf"{re.escape(g)}(?![\w-])", css)]
    assert present == []


def test_the_shared_primitives_take_their_colours_from_tokens_only():
    """Rule scan: every colour value in a primitive rule is a --studio token."""
    css = _read(STATIC / "layout.css")
    colour_props = ("color", "background", "border", "outline", "fill", "stroke")
    offenders = []
    for rule in cap.ordered_primitive_rules(css):
        for prop, value in rule["declarations"]:
            if not prop.startswith(colour_props):
                continue
            stripped = re.sub(r"var\(--studio-[\w-]+(?:\s*,[^)]*)?\)", "", value)
            if re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", stripped):
                offenders.append(f"{rule['selectors']} {prop}: {value}")
    assert offenders == []
