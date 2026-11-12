"""Source and render contracts of the page-explainers fix round (no browser).

* Dead or duplicate stylesheet rules are gone: the three ``.detail-btn`` consumers were removed
  with their buttons, so their rules go; one ``[hidden]`` rule serves ``.dash-panel``.
* One closed-disclosure behaviour, not three page-local rules whose only effect is on what a
  measurement script reads. The harness treats the content of a closed ``<details>`` as not
  visible instead (``tests/ui/test_page_explainers_measure_browser.py`` shows both halves).
* A comment never splits a selector list in a way that makes it describe only part of it.
* The never-run Retirement layout is keyed to a class the template sets, not to the DOM shape of
  one copy line (a second line in that block would silently undo a structural selector).
"""

from __future__ import annotations

import pathlib
import re

import pytest

from tests.ui import _page_explainers_advisor as adv
from tests.ui.test_page_copy_advisor import (  # noqa: F401  (fixtures and the page harness)
    Advisor,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = sorted((REPO / "templates").glob("*.html"))
STYLESHEETS = sorted((REPO / "static").glob("*.css"))
SCRIPTS = sorted((REPO / "static").glob("*.js"))
ADVISOR_TEMPLATE = REPO / "templates" / "ai_advisor.html"
DASHBOARD_TEMPLATE = REPO / "templates" / "index.html"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _style_text(path: pathlib.Path) -> str:
    source = _read(path)
    if path.suffix == ".css":
        return source
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", source, re.S))


def test_no_stylesheet_rule_or_script_names_the_removed_detail_buttons():
    holders = [
        f"{p.relative_to(REPO)}:{n}"
        for p in [*TEMPLATES, *STYLESHEETS, *SCRIPTS]
        for n, line in enumerate(_read(p).splitlines(), 1)
        if "detail-btn" in line
    ]
    assert not holders, f"rules for buttons that no longer render: {holders}"


def test_the_dashboard_has_one_hidden_rule_for_a_dash_panel():
    rules = re.findall(
        r"[^{}]*\.dash-panel\[hidden\][^{}]*\{[^}]*\}", _style_text(DASHBOARD_TEMPLATE)
    )
    assert len(rules) == 1, f"the same [hidden] rule is written {len(rules)} times: {rules}"


CLOSED_DISCLOSURE_RULE = re.compile(r":not\(\[open\]\)[^{}]*\{[^}]*display\s*:\s*none", re.S)


@pytest.mark.parametrize("path", [*TEMPLATES, *STYLESHEETS], ids=lambda p: str(p.relative_to(REPO)))
def test_no_stylesheet_hides_the_content_of_a_closed_disclosure_for_a_script_to_find(path):
    found = CLOSED_DISCLOSURE_RULE.findall(_style_text(path))
    assert not found, (
        "a rule that only changes what geometry a script reads: the harness treats a closed "
        f"details' content as not visible instead: {found}"
    )


SPLIT_SELECTOR_LIST = re.compile(r",[ \t]*\n[ \t]*/\*.*?\*/", re.S)


@pytest.mark.parametrize("path", [*TEMPLATES, *STYLESHEETS], ids=lambda p: str(p.relative_to(REPO)))
def test_no_comment_sits_inside_a_selector_list(path):
    found = SPLIT_SELECTOR_LIST.findall(_style_text(path))
    assert not found, (
        f"a comment between the selectors of one rule describes only part of it: {found}"
    )


def _uncommented_style() -> str:
    return re.sub(r"/\*.*?\*/", "", _style_text(ADVISOR_TEMPLATE), flags=re.S)


def test_no_rule_sets_columns_for_a_pair_of_retirement_subsections():
    """The two Retirement subsections stack: the reference's 4fr 3fr and 3fr 4fr column rules, and
    every rule whose selector names a pair of them, are gone with the script that set the modifier."""
    css = _uncommented_style()
    paired = re.findall(r"([^{}]*)\{[^{}]*grid-template-columns:\s*[34]fr [34]fr[^{}]*\}", css)
    assert not paired, f"a rule still gives the subsections two columns: {paired}"
    named = re.findall(r"([^{}]*retirement-pair[^{}]*)\{", css)
    assert not named, f"a rule still names the pair of subsections: {named}"


def test_the_retirement_tab_renders_no_pair_class_on_any_state(advisor):
    for world in ("empty", "full"):
        html = advisor.open(world).html
        assert "retirement-pair--" not in html, f"a pair modifier is rendered in the {world} world"


def test_no_retirement_rule_is_keyed_to_the_shape_of_a_copy_line():
    keyed = [
        line.strip()
        for line in _style_text(ADVISOR_TEMPLATE).splitlines()
        if "retirement-last-run-line" in line and (":only-child" in line or ":has(" in line)
    ]
    assert not keyed, keyed
