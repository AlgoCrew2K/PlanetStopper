"""The History "Exits" button is a styled control, and the drilled-day heading uses the page's date form.

Two findings on /history:

* The way back from a drilled day (``history-todays-exits-btn``) was a bare ``<button>``: it rendered
  with the browser's default look, outside the design system and the theme. The window picker's buttons
  are styled by ``.window-selector button``; this one sits outside that container.
* A drilled day's heading read ``2026-08-03 exits (5)`` while the closed-day heading reads
  ``Exits on Oct 30 (8)``: two date forms on one table.

Real call path: the real ``GET /history`` (tests/ui/_page_js_harness.py) read as markup; ``static/history.js``
runs unmodified over it, with the real ``/api/history/30`` body of the captured post-mortem days
(tests/ui/_history_answers.py). The date text of the heading is built from the clicked bar's own
``data-date`` with the month abbreviation the closed-day heading already uses.

Contract pinned
* The button's look comes from the design system: it sits inside the window picker container, or carries
  a class that a shipped stylesheet or the page's own style block defines. It has no inline colour that is
  not a token, and any colour its class sets is a token.
* The drilled-day heading carries the clicked day as ``<Mon> <D>`` (the form of the closed-day heading)
  and no ISO date, and still ends with the count in brackets.
"""

from __future__ import annotations

import re

import pytest

from tests import _contrast_support as contrast
from tests.dashboard._index_js_harness import find_all, parse_html
from tests.ui import _history_answers as hist
from tests.ui._page_js_harness import render_page, route, run_page
from tests.ui._shell_support import (  # noqa: F401
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    seed_history_exits,
)
from tests.ui.test_history_refresh_client import drill_target

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
HISTORY = r"/api/history/(\d+|ytd)$"
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


@pytest.fixture
def days(tmp_path, monkeypatch):
    return seed_history_exits(tmp_path, monkeypatch.setattr)


def _button_and_ancestors():
    """The return button's node and the classes of every element that contains it."""
    tree = parse_html(render_page("/history"))
    buttons = find_all(tree, lambda n: n["a"].get("data-testid") == "history-todays-exits-btn")
    assert buttons, "the page has no return button"
    button = buttons[0]
    chain: list[dict] = []

    def walk(node, path):
        if node is button:
            chain.extend(path)
            return True
        for child in node["c"]:
            if isinstance(child, dict) and walk(child, path + [node]):
                return True
        return False

    assert walk(tree, [])
    return button, chain


def _class_is_defined(class_name: str) -> bool:
    pattern = re.compile(r"\." + re.escape(class_name) + r"(?![\w-])")
    return any(pattern.search(css) for _source, css in contrast.stylesheet_texts())


def test_the_return_button_takes_its_look_from_the_design_system():
    button, ancestors = _button_and_ancestors()
    inside_picker = any(
        "window-selector" in node["a"].get("class", "").split() for node in ancestors
    )
    own_classes = button["a"].get("class", "").split()
    defined = [c for c in own_classes if _class_is_defined(c)]
    assert inside_picker or defined, (
        "the return button is a bare element: it is outside the window picker and carries "
        f"no class a stylesheet defines (classes: {own_classes})"
    )


def test_the_return_button_has_no_inline_colour_that_is_not_a_token():
    button, _ = _button_and_ancestors()
    inline = contrast.style_properties(button["a"].get("style"))
    for prop in ("color", "background", "background-color", "border-color", "border"):
        value = inline.get(prop)
        if value is None:
            continue
        assert re.search(r"var\(--studio-[\w-]+\)", value) or value in ("none", "transparent"), (
            f"inline {prop}: {value!r} is not a design token"
        )
    for class_name in button["a"].get("class", "").split():
        for source, selector, props in contrast.class_rules(class_name):
            if "color" in props:
                assert contrast.token_of(props["color"]), (
                    f"{source}: {selector} colours the button with {props['color']!r}"
                )


def test_a_drilled_days_heading_uses_the_month_and_day_form(days):
    answer = hist.history_answer(30)
    day, count = drill_target(answer["json"])
    _year, month, dom = (int(part) for part in day.split("-"))
    label = f"{MONTHS[month - 1]} {dom}"
    run = run_page(
        render_page("/history"),
        f"""
        await H.boot();
        await H.click('#daily-chart-svg rect[data-date="{day}"]');
        return H.text('#todays-exits-heading');
        """,
        routes=[route(HISTORY, answer, method="GET")],
        pathname="/history",
    )
    heading = " ".join(str(run.result).split())
    assert label in heading, f"the drilled heading reads {heading!r}, not the day as {label!r}"
    assert not ISO_DATE.search(heading), f"the drilled heading carries an ISO date: {heading!r}"
    assert heading.endswith(f"({count})"), f"the count is not at the end: {heading!r}"
