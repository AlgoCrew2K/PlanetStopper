"""The stylesheets of the dashboard, History and Performance keep no rule for markup the change removed.

No browser: the templates, the page scripts they load and the committed token inventory are read as
text. A rule that styled an element at the reference and styles none now is a rule a later reader
trusts and cannot test, so the removals of this change take their rules and their comments with them.

What is pinned, per page (``index``, ``history``, ``performance``), over the page's own ``<style>``
block:

* no selector names a class, an id or an exact ``data-testid`` that the page carried at the reference
  (``tests/fixtures/page_explainers/ref_token_inventory.json``, the literal tokens of its template,
  the header and the scripts it loads) and that its markup, the header, the Glossary macro and its
  scripts no longer mention anywhere;
* no ``grid-template-areas`` names an area that no ``grid-area`` rule of the same block assigns (a
  removed element leaves an empty row);
* no comment, and no other text of the template, describes markup that is gone: the terms disclosure
  of the dashboard, or the fill-space rule "so the band beside ... is not empty".

The first rule is a guard (it holds at the reference's neighbour by design and fails on a removal
that leaves its rule behind); the other two fail until the leftovers go.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = REPO / "templates"
STATIC = REPO / "static"
INVENTORY = json.loads(
    (REPO / "tests" / "fixtures" / "page_explainers" / "ref_token_inventory.json").read_text(
        encoding="utf-8"
    )
)["files"]

PAGES = ("index", "history", "performance")
# What a page's elements can come from: its template, the header it includes and the Glossary macro,
# and the scripts the template or the header loads.
SHARED_MARKUP = ("_chrome.html", "_glossary.html", "_fonts.html")
FORBIDDEN_PHRASES = {
    "index": ("terms disclosure", "What the terms mean"),
    "history": ("band beside", "is not empty", "What the exit reasons mean"),
    "performance": ("band beside", "is not empty"),
}

_JINJA = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}", re.S)
_STYLE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_SCRIPT_FILE = re.compile(r"filename=['\"]([\w./-]+\.js)['\"]")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _style_of(page: str) -> str:
    return "\n".join(_STYLE.findall(_JINJA.sub("", _read(TEMPLATES / f"{page}.html"))))


def _selectors_of(css: str) -> list[str]:
    """Every selector of every style rule (at-rules descended into, keyframe steps skipped)."""
    css = _CSS_COMMENT.sub("", css)
    selectors: list[str] = []
    buffer: list[str] = []
    for char in css:
        if char == "{":
            prelude = "".join(buffer).strip()
            buffer = []
            if prelude and not prelude.startswith("@"):
                selectors.extend(_split_top_level(prelude))
        elif char == "}":
            buffer = []
        else:
            buffer.append(char)
    return selectors


def _split_top_level(prelude: str) -> list[str]:
    parts, depth, current = [], 0, []
    for char in prelude:
        if char in "([":
            depth += 1
        elif char in ")]":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _tokens_of_selector(selector: str) -> set[str]:
    """The ``class:``, ``id:`` and ``testid:`` tokens a selector needs to match (a ``:not()`` group
    needs none)."""
    selector = re.sub(r":not\([^)]*\)", "", selector)
    found = {f"testid:{m}" for m in re.findall(r"\[data-testid=[\"']([^\"']+)[\"']\]", selector)}
    selector = re.sub(r"\[[^\]]*\]", "", selector)
    found |= {f"class:{m}" for m in re.findall(r"\.(-?[A-Za-z_][\w-]*)", selector)}
    found |= {f"id:{m}" for m in re.findall(r"#([A-Za-z_][\w-]*)", selector)}
    return found


def _page_files(page: str) -> list[str]:
    template = _read(TEMPLATES / f"{page}.html")
    chrome = _read(TEMPLATES / "_chrome.html")
    scripts = sorted(set(_SCRIPT_FILE.findall(template) + _SCRIPT_FILE.findall(chrome)))
    return [f"templates/{page}.html", "templates/_chrome.html"] + [f"static/{s}" for s in scripts]


def _reference_tokens(page: str) -> set[str]:
    tokens: set[str] = set()
    for path in _page_files(page):
        tokens |= set(INVENTORY.get(path, []))
    return tokens


def _current_text(page: str) -> str:
    """What can still use a token: the template without its style block, the shared markup and the
    page's scripts."""
    template = _STYLE.sub("", _read(TEMPLATES / f"{page}.html"))
    parts = [template] + [_read(TEMPLATES / name) for name in SHARED_MARKUP]
    parts += [_read(STATIC / s) for s in sorted(set(_SCRIPT_FILE.findall(template)))]
    parts += [
        _read(STATIC / s)
        for s in sorted(set(_SCRIPT_FILE.findall(_read(TEMPLATES / "_chrome.html"))))
    ]
    return "\n".join(parts)


def _mentions(text: str, token: str) -> bool:
    name = token.split(":", 1)[1]
    return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text) is not None


def _dead_selectors(css: str, reference: set[str], text: str) -> list[str]:
    dead = []
    for selector in _selectors_of(css):
        for token in sorted(_tokens_of_selector(selector)):
            if token in reference and not _mentions(text, token):
                dead.append(f"{selector!r} needs {token}")
    return dead


def test_the_scan_reports_a_planted_rule_for_removed_markup_and_spares_a_live_one():
    """A scan that cannot fail proves nothing: a rule for a reference class no text mentions is
    reported (a comma list, a media rule and an attribute selector included); one whose class is
    still in the markup, a class the reference never had and a ``:not()`` group are not."""
    css = (
        "/* a comment .gone-in-a-comment */ .kept-box { color: red; }"
        " .gone-box .kept-box, .kept-box > span { margin: 0; }"
        " @media (max-width: 700px) { .gone-box { display: none; } }"
        ' [data-testid="gone-id"] { top: 0; } .kept-box:not(.gone-box) { left: 0; }'
        " .never-at-reference { right: 0; }"
    )
    reference = {"class:kept-box", "class:gone-box", "testid:gone-id"}
    text = '<div class="kept-box"></div> some-gone-box-lookalike'
    assert _dead_selectors(css, reference, text) == [
        "'.gone-box .kept-box' needs class:gone-box",
        "'.gone-box' needs class:gone-box",
        "'[data-testid=\"gone-id\"]' needs testid:gone-id",
    ]


@pytest.mark.parametrize("page", PAGES)
def test_no_selector_names_a_class_id_or_testid_the_page_carried_and_no_longer_mentions(page):
    """Rejects a removal that leaves its rule behind: a selector for markup the page had at the
    reference and no longer builds anywhere."""
    dead = _dead_selectors(_style_of(page), _reference_tokens(page), _current_text(page))
    assert not dead, f"{page}.html styles markup that is gone:\n" + "\n".join(dead)


@pytest.mark.parametrize("page", PAGES)
def test_no_grid_template_areas_names_an_area_no_rule_assigns(page):
    """Rejects an area row left behind when the element that filled it was removed."""
    css = _CSS_COMMENT.sub("", _style_of(page))
    areas: set[str] = set()
    for block in re.findall(r"grid-template-areas\s*:\s*((?:\"[^\"]*\"\s*)+);", css):
        for row in re.findall(r"\"([^\"]*)\"", block):
            areas |= {name for name in row.split() if name != "."}
    assigned = set(re.findall(r"grid-area\s*:\s*([A-Za-z_][\w-]*)\s*;", css))
    orphans = sorted(areas - assigned)
    assert not orphans, f"{page}.html names grid areas no rule assigns: {orphans}"


@pytest.mark.parametrize("page", PAGES)
def test_no_text_of_the_template_describes_markup_that_is_gone(page):
    """Rejects a comment, kept after its element left, that explains a placement the page no longer
    has (the dashboard's terms disclosure, a column "so the band beside ... is not empty")."""
    text = _read(TEMPLATES / f"{page}.html")
    hits = [phrase for phrase in FORBIDDEN_PHRASES[page] if phrase.lower() in text.lower()]
    assert not hits, f"{page}.html still says: {hits}"
