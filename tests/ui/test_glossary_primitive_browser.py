"""The Glossary disclosure and the title script behave as the pages need them to.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_glossary_primitive_browser.py``).

Each case is a small page built here (no network) that inlines the real ``static/tokens.css``,
``static/layout.css`` and ``static/glossary.js``, with the Glossary written through the real macro
(``templates/_glossary.html``). The rules shown:

* the Glossary is one closed disclosure named "Glossary" that opens and closes with Enter and Space
  and returns to its closed height;
* a label whose text names a term (a table header, a tile label, an element whose whole text is a
  term and a colon) carries the whole Glossary line as its ``title``; an element that names a
  Glossary key through ``data-glossary`` carries it too, whatever its text;
* the lines a label reads are those of the Glossary in its own host: the nearest Glossary that is
  the last child of an ancestor of the label (a tab panel, the detail panel, the modal, the page);
* a ``title`` the page already carried is never touched; every title the script sets is marked
  ``data-glossary-titled``;
* rows a page script rewrites are stamped again within an animation frame or two;
* the script writes attributes only: no markup is added or changed, and a line holding markup is
  a title string and nothing else.
"""

from __future__ import annotations

import html
import pathlib
import re
from contextlib import contextmanager

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    firefox,
)

pytestmark = pytest.mark.browser

REPO = pathlib.Path(__file__).resolve().parents[2]
STATIC = REPO / "static"
MACRO = REPO / "templates" / "_glossary.html"
TOKENS = (STATIC / "tokens.css").read_text(encoding="utf-8")
LAYOUT = (STATIC / "layout.css").read_text(encoding="utf-8")

CALMAR_A = "Calmar: the return per unit of the worst drop, over the window."
CALMAR_B = "Calmar: panel B says it another way."
ANALYSED = "Analysed: how many symphonies the run could score."
DELTA = "Delta: bot minus held. Positive: the bot did better."


def _script() -> str:
    path = STATIC / "glossary.js"
    assert path.is_file(), "static/glossary.js does not exist"
    return path.read_text(encoding="utf-8")


def _glossary(host: str, lines: list[tuple[str, str]]) -> str:
    """The Glossary through the real macro (a missing macro fails with a message)."""
    assert MACRO.is_file(), "templates/_glossary.html does not exist"
    from flask import render_template_string

    import app as app_module

    body = "".join(
        f'<p data-term="{key}">{html.escape(text, quote=False)}</p>' for key, text in lines
    )
    template = "{% from '_glossary.html' import glossary %}{% call glossary(host) %}" + body
    template += "{% endcall %}"
    with app_module.app.test_request_context():
        return render_template_string(template, host=host)


def _page(body: str, *, script_in_head: bool = False, with_script: bool = True) -> str:
    tag = f"<script>{_script()}</script>" if with_script else ""
    return (
        "<!doctype html><html><head><style>"
        f"{TOKENS}\n{LAYOUT}\nbody {{ margin: 0; font: 14px/20px sans-serif; }}</style>"
        f"{tag if script_in_head else ''}</head>"
        f"<body>{body}{'' if script_in_head else tag}</body></html>"
    )


@contextmanager
def _open(firefox, markup: str, width: int = 1200):  # noqa: F811
    context = firefox.new_context(viewport={"width": width, "height": 900})
    try:
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))
        page.set_content(markup)
        page.errors = errors  # type: ignore[attr-defined]
        yield page
    finally:
        context.close()


TITLE_OF = "(sel) => { const e = document.querySelector(sel); return e ? e.getAttribute('title') : 'NO ELEMENT'; }"
MARKED = "(sel) => document.querySelector(sel).hasAttribute('data-glossary-titled')"


def _panel(panel_id: str, host: str, calmar: str, extra: str = "", extra_lines: tuple = ()) -> str:
    lines = [("K1", calmar), ("K2", ANALYSED), ("K3", DELTA), ("K4", "A line that names no term.")]
    lines += list(extra_lines)
    return (
        f'<section id="{panel_id}" data-testid="panel-{panel_id}">'
        "<table><thead><tr>"
        f'<th id="{panel_id}-calmar">Calmar</th>'
        f'<th id="{panel_id}-kept" title="kept title">Delta</th>'
        f'<th id="{panel_id}-icon">Analysed<svg width="8" height="8"></svg></th>'
        "</tr></thead></table>"
        f'<span id="{panel_id}-tile" class="tile-label">Analysed</span>'
        f'<span id="{panel_id}-colon">Analysed:</span>'
        f'<span id="{panel_id}-spaced"> Calmar: </span>'
        f'<span id="{panel_id}-partial">Calmar ratio</span>'
        f'<span id="{panel_id}-lower">calmar</span>'
        f'<span id="{panel_id}-second">Positive</span>'
        f'<span id="{panel_id}-keyed" data-glossary="K3">Whatever text</span>'
        f'<span id="{panel_id}-unknown" data-glossary="NOPE">Whatever</span>'
        f"{extra}{_glossary(host, lines)}</section>"
    )


def test_the_glossary_is_closed_named_and_opens_and_closes_with_enter_and_space(firefox):  # noqa: F811
    markup = _page(_panel("a", "Retirement", CALMAR_A))
    with _open(firefox, markup) as page:
        summary = page.locator("[data-testid='glossary'] > summary")
        details = "document.querySelector(\"[data-testid='glossary']\").open"
        assert summary.inner_text().strip() == "Glossary"
        assert page.evaluate(details) is False
        closed_height = page.evaluate("document.querySelector('#a').getBoundingClientRect().height")
        for key in ("Enter", "Space"):
            summary.focus()
            page.keyboard.press(key)
            assert page.evaluate(details) is True, f"{key} did not open the Glossary"
            opened = page.evaluate("document.querySelector('#a').getBoundingClientRect().height")
            assert opened > closed_height + 20
            page.keyboard.press(key)
            assert page.evaluate(details) is False, f"{key} did not close the Glossary"
            back = page.evaluate("document.querySelector('#a').getBoundingClientRect().height")
            assert back == pytest.approx(closed_height, abs=1), (
                f"{key}: height {back} != {closed_height}"
            )


def test_a_label_that_names_a_term_carries_the_whole_line_as_its_title(firefox):  # noqa: F811
    with _open(firefox, _page(_panel("a", "Retirement", CALMAR_A))) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        assert page.evaluate(TITLE_OF, "#a-calmar") == CALMAR_A
        assert page.evaluate(TITLE_OF, "#a-tile") == ANALYSED
        assert page.evaluate(TITLE_OF, "#a-colon") == ANALYSED, "a trailing colon is dropped"
        assert page.evaluate(TITLE_OF, "#a-spaced") == CALMAR_A, "whitespace is trimmed"
        assert page.evaluate(TITLE_OF, "#a-icon") == ANALYSED, "a header with an icon is a label"
        assert page.evaluate(MARKED, "#a-calmar") is True


def test_only_a_whole_text_equal_to_a_lead_term_is_a_label(firefox):  # noqa: F811
    with _open(firefox, _page(_panel("a", "Retirement", CALMAR_A))) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        for untitled in ("#a-partial", "#a-lower", "#a-second", "#a-unknown"):
            assert page.evaluate(TITLE_OF, untitled) is None, f"{untitled} was given a title"


def test_the_lead_term_is_the_text_before_the_first_colon_and_space_only(firefox):  # noqa: F811
    with _open(firefox, _page(_panel("a", "Retirement", CALMAR_A))) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        # "Positive" is the text before a later ": " inside the Delta line, not its lead term.
        assert page.evaluate(TITLE_OF, "#a-second") is None


def test_an_element_that_names_a_glossary_key_carries_that_line_whatever_its_text(firefox):  # noqa: F811
    with _open(firefox, _page(_panel("a", "Retirement", CALMAR_A))) as page:
        page.wait_for_function("document.querySelector('#a-keyed').hasAttribute('title')")
        assert page.evaluate(TITLE_OF, "#a-keyed") == DELTA
        assert page.evaluate(MARKED, "#a-keyed") is True
        assert page.evaluate(TITLE_OF, "#a-unknown") is None, "an unknown key sets nothing"
        assert page.errors == []


def test_a_title_the_page_already_carried_is_never_touched_or_marked(firefox):  # noqa: F811
    with _open(firefox, _page(_panel("a", "Retirement", CALMAR_A))) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        assert page.evaluate(TITLE_OF, "#a-kept") == "kept title"
        assert page.evaluate(MARKED, "#a-kept") is False


def test_each_label_reads_the_glossary_of_its_own_host(firefox):  # noqa: F811
    outside = '<span id="outside">Calmar</span>'
    special = '<span id="{}-special">Special</span>'
    body = (
        _panel(
            "a",
            "Retirement",
            CALMAR_A,
            extra=special.format("a"),
            extra_lines=(("K5", "Special: only the first host defines this."),),
        )
        + _panel("b", "Overview", CALMAR_B, extra=special.format("b"))
        + outside
    )
    with _open(firefox, _page(body)) as page:
        page.wait_for_function("document.querySelector('#b-calmar').hasAttribute('title')")
        assert page.evaluate(TITLE_OF, "#a-calmar") == CALMAR_A
        assert page.evaluate(TITLE_OF, "#b-calmar") == CALMAR_B
        assert page.evaluate(TITLE_OF, "#a-special") == "Special: only the first host defines this."
        assert page.evaluate(TITLE_OF, "#b-special") is None, "a term only another host defines"
        assert page.evaluate(TITLE_OF, "#outside") is None, "a label outside every Glossary's host"


def test_the_nearest_glossary_wins_for_a_label_inside_a_host_inside_a_host(firefox):  # noqa: F811
    inner = _panel("inner", "detail panel", CALMAR_B)
    outer_lines = [("K1", CALMAR_A), ("K2", ANALYSED)]
    body = (
        '<main id="page"><span id="page-label">Calmar</span>'
        f'<aside id="detail">{inner}</aside>{_glossary("Dashboard", outer_lines)}</main>'
    )
    with _open(firefox, _page(body)) as page:
        page.wait_for_function("document.querySelector('#inner-calmar').hasAttribute('title')")
        assert page.evaluate(TITLE_OF, "#inner-calmar") == CALMAR_B
        assert page.evaluate(TITLE_OF, "#page-label") == CALMAR_A


def test_a_label_in_a_hidden_panel_is_stamped_too(firefox):  # noqa: F811
    body = _panel("a", "Retirement", CALMAR_A).replace(
        '<section id="a"', '<section id="a" hidden style="display:none"'
    )
    with _open(firefox, _page(body)) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        assert page.evaluate(TITLE_OF, "#a-calmar") == CALMAR_A


@pytest.mark.parametrize("script_in_head", [False, True])
def test_the_titles_are_set_whether_the_script_loads_before_or_after_the_body(
    firefox, script_in_head
):  # noqa: F811
    markup = _page(_panel("a", "Retirement", CALMAR_A), script_in_head=script_in_head)
    with _open(firefox, markup) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        assert page.evaluate(TITLE_OF, "#a-calmar") == CALMAR_A
        assert page.errors == []


def test_rows_a_page_script_rewrites_are_stamped_again(firefox):  # noqa: F811
    body = _panel("a", "Retirement", CALMAR_A) + '<div id="live"></div>'
    with _open(firefox, _page(body)) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        page.evaluate(
            "document.querySelector('#a').insertAdjacentHTML('afterbegin',"
            ' \'<table id="t1"><tr><th id="late">Calmar</th></tr></table>\')'
        )
        page.wait_for_function(
            "document.querySelector('#late').hasAttribute('title')", timeout=3000
        )
        assert page.evaluate(TITLE_OF, "#late") == CALMAR_A
        # a refresh replaces the whole table, as the autotune poll does every 15 seconds
        for _ in range(3):
            page.evaluate(
                "const t = document.querySelector('#t1'); t.outerHTML ="
                ' \'<table id="t1"><tr><th id="late">Calmar</th></tr></table>\''
            )
            page.wait_for_function(
                "document.querySelector('#late').hasAttribute('title')", timeout=3000
            )
            assert page.evaluate(TITLE_OF, "#late") == CALMAR_A


def test_a_glossary_written_after_load_stamps_the_labels_beside_it(firefox):  # noqa: F811
    with _open(firefox, _page('<div id="modal"><span id="m-label">Analysed</span></div>')) as page:
        assert page.evaluate(TITLE_OF, "#m-label") is None
        glossary = _glossary("settings modal", [("M1", ANALYSED)])
        page.evaluate(
            "(markup) => document.querySelector('#modal').insertAdjacentHTML('beforeend', markup)",
            glossary,
        )
        page.wait_for_function(
            "document.querySelector('#m-label').hasAttribute('title')", timeout=3000
        )
        assert page.evaluate(TITLE_OF, "#m-label") == ANALYSED


def test_apply_can_be_called_again_and_changes_nothing(firefox):  # noqa: F811
    with _open(firefox, _page(_panel("a", "Retirement", CALMAR_A))) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        before = page.evaluate("document.body.innerHTML")
        page.evaluate("window.PSGlossary.apply(document); window.PSGlossary.apply(document.body)")
        page.wait_for_timeout(200)
        assert page.evaluate("document.body.innerHTML") == before
        assert page.evaluate("typeof window.PSGlossary.apply") == "function"


def test_the_script_adds_attributes_and_nothing_else(firefox):  # noqa: F811
    body = _panel("a", "Retirement", CALMAR_A)
    with _open(firefox, _page(body, with_script=False)) as page:
        baseline = page.evaluate("document.body.innerHTML")
    with _open(firefox, _page(body)) as page:
        page.wait_for_function("document.querySelector('#a-calmar').hasAttribute('title')")
        stripped = page.evaluate(
            "() => { const root = document.body.cloneNode(true);"
            " root.querySelectorAll('[data-glossary-titled]').forEach((e) => {"
            "   e.removeAttribute('title'); e.removeAttribute('data-glossary-titled'); });"
            " root.querySelectorAll('script').forEach((s) => s.remove());"
            " return root.innerHTML; }"
        )
    clean = lambda text: re.sub(r"\s+", " ", text)  # noqa: E731
    assert clean(stripped) == clean(re.sub(r"<script>.*?</script>", "", baseline, flags=re.S))


def test_a_line_holding_markup_becomes_a_title_string_and_creates_no_element(firefox):  # noqa: F811
    hostile = 'Hostile: <img src=x onerror="window.pwned=1"> text'
    body = '<span id="h">Hostile</span>' + _glossary("Overview", [("K9", hostile)])
    with _open(firefox, _page(body)) as page:
        page.wait_for_function("document.querySelector('#h').hasAttribute('title')")
        assert page.evaluate(TITLE_OF, "#h") == hostile
        assert page.evaluate("document.querySelectorAll('img').length") == 0
        assert page.evaluate("window.pwned") is None
        assert page.errors == []


def test_a_page_without_a_glossary_is_left_alone_and_raises_nothing(firefox):  # noqa: F811
    with _open(
        firefox, _page('<span id="x">Calmar</span><table><tr><th>Calmar</th></tr></table>')
    ) as page:
        page.wait_for_timeout(300)
        assert page.evaluate(TITLE_OF, "#x") is None
        assert page.errors == []


INHERITED_NAMES = ("constructor", "toString", "valueOf", "hasOwnProperty")


def test_a_label_or_key_named_like_an_object_property_is_not_a_term(firefox):  # noqa: F811
    """A display name or label that happens to be ``constructor`` or ``valueOf`` is no term."""
    labels = "".join(f'<span id="l-{n}">{n}</span>' for n in INHERITED_NAMES)
    keyed = "".join(f'<span id="k-{n}" data-glossary="{n}">x</span>' for n in INHERITED_NAMES)
    body = f"<section>{labels}{keyed}{_glossary('Overview', [('K1', CALMAR_A)])}</section>"
    with _open(firefox, _page(body)) as page:
        page.wait_for_timeout(300)
        for name in INHERITED_NAMES:
            assert page.evaluate(TITLE_OF, f"#l-{name}") is None, f"label {name} got a title"
            assert page.evaluate(TITLE_OF, f"#k-{name}") is None, f"key {name} got a title"
        assert page.errors == []


def test_a_line_whose_lead_term_is_an_object_property_name_is_a_term(firefox):  # noqa: F811
    line = "constructor: a term that shares a name with an object property."
    body = (
        f'<section><span id="t">constructor</span>{_glossary("Overview", [("K1", line)])}</section>'
    )
    with _open(firefox, _page(body)) as page:
        page.wait_for_function("document.querySelector('#t').hasAttribute('title')", timeout=3000)
        assert page.evaluate(TITLE_OF, "#t") == line
