"""The one Glossary wrapper and the script that stamps titles from it, read without a browser.

``templates/_glossary.html`` holds one call macro, ``glossary(host)``. A host moves its definition
paragraphs (each a ``<p data-term="KEY">``) into one call block, as the last element of the host,
and the macro wraps them in one closed disclosure whose summary reads "Glossary", in the look of
the shared ``.hp-more`` disclosure. Every host writes the markup through this one macro, so no host
repeats the attributes by hand and a page's Glossary cannot drift from another's.

``static/glossary.js`` exposes ``window.PSGlossary.apply(root)``, which writes ``title`` attributes
only. Its behaviour is shown in a browser by ``test_glossary_primitive_browser.py``; here its source
is held to the rules that need no browser: it writes attributes, never markup.
"""

from __future__ import annotations

import pathlib
import re

import pytest
from bs4 import BeautifulSoup

REPO = pathlib.Path(__file__).resolve().parents[2]
MACRO = REPO / "templates" / "_glossary.html"
SCRIPT = REPO / "static" / "glossary.js"

LINES = (
    '<p data-term="K1">Calmar: the return per unit of the worst drop.</p>'
    '<p data-term="K2">Delta: bot minus held. Positive: the bot did better.</p>'
    '<p data-term="K3">A line that names no term.</p>'
)


def _source(path: pathlib.Path) -> str:
    assert path.is_file(), f"{path.relative_to(REPO)} does not exist"
    return path.read_text(encoding="utf-8")


def _render(host: str, lines: str = LINES, *, twice: bool = False) -> BeautifulSoup:
    from flask import render_template_string

    import app as app_module

    _source(MACRO)
    call = "{% call glossary(host) %}" + lines + "{% endcall %}"
    template = "{% from '_glossary.html' import glossary %}" + call * (2 if twice else 1)
    with app_module.app.test_request_context():
        return BeautifulSoup(render_template_string(template, host=host), "html.parser")


def test_the_macro_renders_one_closed_disclosure_in_the_shared_look_with_its_host_named():
    _source(MACRO)
    found = _render("Overview").select("details")
    assert len(found) == 1
    details = found[0]
    assert "hp-more" in details.get("class", [])
    assert details.get("data-terms") == "glossary"
    assert details.get("data-testid") == "glossary"
    assert details.get("data-glossary-host") == "Overview"
    assert not details.has_attr("open"), "a Glossary is closed on load"


def test_the_summary_is_the_first_child_and_reads_glossary():
    details = _render("Overview").select_one("details")
    first = next(child for child in details.children if getattr(child, "name", None))
    assert first.name == "summary"
    assert first.get_text(strip=True) == "Glossary"


def test_the_callers_paragraphs_follow_the_summary_in_order_and_unchanged():
    details = _render("Overview").select_one("details")
    paragraphs = details.select("p[data-term]")
    assert [p["data-term"] for p in paragraphs] == ["K1", "K2", "K3"]
    assert paragraphs[1].get_text() == "Delta: bot minus held. Positive: the bot did better."
    assert details.find("summary").find_next_sibling() is paragraphs[0]


def test_a_host_name_is_escaped_in_its_attribute():
    hostile = '"><script>alert(1)</script><b x="'
    soup = _render(hostile)
    assert soup.select_one("details")["data-glossary-host"] == hostile
    assert soup.find("script") is None and soup.find("b") is None


def test_a_glossary_with_no_lines_is_still_one_closed_disclosure_with_its_summary():
    details = _render("Chat", lines="").select_one("details")
    assert details is not None and not details.has_attr("open")
    assert details.find("summary").get_text(strip=True) == "Glossary"
    assert not details.select("p")


def test_two_glossaries_on_one_page_are_two_disclosures_and_repeat_no_id():
    soup = _render("Overview", twice=True)
    assert len(soup.select("details[data-testid='glossary']")) == 2
    assert not soup.select("[id]"), "an id in the macro would repeat on a page with two Glossaries"


def test_the_macro_marks_nothing_safe_and_leaves_autoescape_on():
    source = _source(MACRO)
    assert not re.search(r"\|\s*safe\b|Markup\(|autoescape\s+false", source), source
    assert source.count("{% macro") == 1 and "{% macro glossary(host)" in source
    assert "caller()" in source


def test_the_script_exposes_an_apply_function_on_a_namespaced_global():
    source = _source(SCRIPT)
    assert re.search(r"window\.PSGlossary\s*=", source) or "window.PSGlossary" in source
    assert re.search(r"\bapply\b", source)


@pytest.mark.parametrize(
    "forbidden",
    [
        r"\.innerHTML\s*=",
        r"\.outerHTML\s*=",
        r"insertAdjacentHTML",
        r"document\.write",
        r"\beval\(",
        r"new Function",
    ],
)
def test_the_script_writes_attributes_and_never_markup(forbidden):
    assert not re.search(forbidden, _source(SCRIPT)), f"glossary.js matches {forbidden}"


def test_the_script_batches_its_work_on_the_animation_frame_and_observes_child_lists():
    source = _source(SCRIPT)
    assert "MutationObserver" in source and "requestAnimationFrame" in source
    assert re.search(r"childList\s*:\s*true", source) and re.search(r"subtree\s*:\s*true", source)


def test_the_script_marks_each_title_it_sets_so_it_never_touches_another():
    assert "data-glossary-titled" in _source(SCRIPT)
