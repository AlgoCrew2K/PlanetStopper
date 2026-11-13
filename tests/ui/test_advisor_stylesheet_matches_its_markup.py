"""The Advisor page's stylesheet holds no rule for markup the page no longer has.

Subject: the ``<style>`` blocks of ``templates/ai_advisor.html``, read as text. This slice removed
markup (the page caption beside the tab bar, the "What the terms mean" disclosures, the guardrail
paragraph, the Retirement pointer in the toolbar, the side-by-side Retirement pairing); a rule left
behind matches nothing and a comment left behind describes what is not there.

Contract pinned (the plan's AC-7, scenario S-7.17):

* No rule names the removed pieces: the caption class, the guardrail note class, a disclosure that
  follows a description, the disclosure and note cells of the navigation panel, and the empty-panel
  rule that places the removed disclosure.
* The navigation panel is only a box round the tab bar: no rule gives it a grid or places a child
  of it.
* The toolbar grid has the tracks of what it holds (the picker and the hint), not a third track for
  the Retirement pointer that moved out of it.
* No comment in the template describes a removed piece.
* A class a rule names is used by the template's markup, a page script or the server, apart from a
  frozen list of classes written by string concatenation (a state suffix a script or Jinja appends)
  and the few that had no user at the reference commit. A new entry is a finding, not a line to add.

The check reads text only: no browser, no request.
"""

from __future__ import annotations

import glob
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TEMPLATE = REPO / "templates" / "ai_advisor.html"


def _template() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def _style_blocks(text: str) -> str:
    return "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", text, re.S))


def _css_without_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", _style_blocks(text), flags=re.S)


def _rules(css: str) -> list[tuple[str, str]]:
    """(selector text, declarations) of every rule, media-query wrappers looked through."""
    flat = re.sub(r"@media[^{]*\{", "", css)
    return [
        (" ".join(selector.split()), body)
        for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", flat)
        if selector.strip()
    ]


def _selectors() -> list[str]:
    out: list[str] = []
    for selector_text, _ in _rules(_css_without_comments(_template())):
        out.extend(" ".join(part.split()) for part in selector_text.split(","))
    return out


# Selectors of markup this slice removed. Each matched an element at the reference commit.
STALE_SELECTORS = (
    ".adv-caption",
    ".obs-framing-note",
    ".adv-explainer:has(+ .hp-more)",
    ".adv-explainer + .hp-more",
    ".autotune-panel:has(.autotune-empty) > details.hp-more",
    ".adv-panel--nav > .adv-note",
    ".adv-panel--nav > .hp-more",
)

# Phrases of comments that described the removed pieces.
STALE_COMMENT_PHRASES = (
    "above the tab bar",
    "the one-line hint, the Retirement pointer",
    "pointer at the Retirement tab",
    "pointer text comes from",
    "terms disclosure",
    "and its terms",
    "tab bar takes the left",
)


@pytest.mark.parametrize("stale", STALE_SELECTORS)
def test_no_rule_names_markup_the_page_no_longer_has(stale):
    """Rejects a rule left behind after the markup it styled was removed."""
    found = [s for s in _selectors() if stale in s]
    assert found == [], f"a rule still names removed markup: {found}"


def test_the_navigation_panel_is_a_box_round_the_tab_bar_and_places_nothing():
    """The panel holds only the tab bar, so no rule makes it a grid or places a child of it."""
    problems = []
    for selector, body in _rules(_css_without_comments(_template())):
        for part in (p.strip() for p in selector.split(",")):
            if part == ".adv-panel--nav" and re.search(r"grid-template|display:\s*grid", body):
                problems.append(f"{part} {{ {' '.join(body.split())} }}")
            if part.startswith(".adv-panel--nav >") and ".cap-nav-wrap" not in part:
                problems.append(part)
    assert problems == [], problems


def test_the_toolbar_grid_has_no_track_for_the_pointer_that_left_it():
    """The toolbar row holds the picker and the hint: at most two tracks in every rule."""
    problems = []
    for selector, body in _rules(_css_without_comments(_template())):
        if selector.strip() != ".adv-grid":
            continue
        for declaration in body.split(";"):
            name, _, value = declaration.partition(":")
            if name.strip() != "grid-template-columns":
                continue
            tracks = re.findall(r"minmax\([^)]*\)|repeat\([^)]*\)|[^\s]+", value.strip())
            if len(tracks) > 2:
                problems.append(f"{selector}: {value.strip()} ({len(tracks)} tracks)")
    assert problems == [], problems


@pytest.mark.parametrize("phrase", STALE_COMMENT_PHRASES)
def test_no_comment_describes_markup_that_was_removed(phrase):
    assert phrase not in _template(), f"a comment still says {phrase!r}"


# Classes a rule names that no markup, script or server text carries as a whole word: the state
# suffixes a script or Jinja appends to a base class ("decision-pill--" + a verdict), and classes
# that had no user at the reference commit either. The list is frozen: a class that joins it is a
# rule left without markup.
FROZEN_CLASSES_WITHOUT_A_STATIC_USER = frozenset(
    {
        "adv-panel--stack",
        "autotune-notice",
        "corr-band-danger",
        "corr-band-diversifying",
        "corr-band-neutral",
        "corr-band-undefined",
        "corr-band-warn",
        "corr-thin-stress",
        "corr-thin-uncertainty",
        "decision-pill--neg",
        "decision-pill--pos",
        "decision-pill--warn",
        "frozen-eval-pill--neg",
        "frozen-eval-pill--pos",
        "frozen-eval-pill--warn",
        "gate-dot--neg",
        "gate-dot--pos",
        "incubation-status-chip--expired",
        "incubation-status-chip--failed",
        "incubation-status-chip--incubating",
        "incubation-status-chip--promoted",
        "org",
        "prism-sources-header",
        "refresh-link",
        "tenure-verdict-chip--keep",
        "tenure-verdict-chip--none",
        "tenure-verdict-chip--retire_candidate",
        "tenure-verdict-chip--watch",
        "w3",
    }
)


def _classes_without_a_static_user() -> set[str]:
    text = _template()
    css = _css_without_comments(text)
    markup = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.S)
    markup = re.sub(r"\{#.*?#\}", "", markup, flags=re.S)
    markup = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    scripts = "".join(
        pathlib.Path(p).read_text(encoding="utf-8")
        for p in glob.glob(str(REPO / "static" / "*.js"))
    )
    server = (REPO / "app.py").read_text(encoding="utf-8")
    named = {c for c in re.findall(r"\.([A-Za-z_][\w-]*)", css) if not c.isdigit()}

    def used(name: str, where: str) -> bool:
        return re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", where) is not None

    return {c for c in named if not (used(c, markup) or used(c, scripts) or used(c, server))}


def test_every_class_a_rule_names_is_used_by_the_page_apart_from_the_frozen_list():
    """S-7.17: a rule whose class nothing writes is a rule left behind. Rejects the caption and
    guardrail-note rules this slice's removals orphaned, and any later one."""
    orphans = _classes_without_a_static_user()
    assert orphans - FROZEN_CLASSES_WITHOUT_A_STATIC_USER == set(), sorted(
        orphans - FROZEN_CLASSES_WITHOUT_A_STATIC_USER
    )
