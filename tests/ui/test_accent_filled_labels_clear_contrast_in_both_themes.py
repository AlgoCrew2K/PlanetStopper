"""A label on an accent-filled control clears 4.5:1 in the light and in the dark theme.

The accent token turns lighter in the dark theme, so white label text on it falls from 5.3:1 to 3.1:1
at 12 to 14 px. Apply suggestion already took a text token that contrasts with its fill in both themes
(tests/ui/test_apply_suggestion_button_clears_contrast_in_both_themes.py); the other controls drawn on
the accent fill did not: the advisor's Run buttons, Chat about this, Force run now and the rest.

Real call path: the real templates, stylesheets and page scripts, scanned for every rule or inline
style that fills with ``var(--studio-accent)`` and also names a text colour, and the real tokens
(``static/tokens.css``), through tests/_contrast_support.py. ``static/tokens.css`` is frozen, so the fix
is a text token chosen per theme, never a token edit.

Contract pinned
* Every control that sets a text colour on the accent fill names a design token (not a literal).
* The composited ratio of that token over the accent fill is at least 4.5 in each theme.
* The sweep is not empty (the patterns still find the controls the finding names).
"""

from __future__ import annotations

import re

import pytest

from tests import _contrast_support as contrast
from tests.ui.test_text_carrying_a_figure_clears_contrast import (
    declarations_matching,
    primary_token,
)

ACCENT_FILL = re.compile(r"(?<![\w-])background(?:-color)?\s*:\s*var\(\s*--studio-accent\s*\)")
TEXT_COLOUR = re.compile(r"(?<![\w-])color\s*:\s*([^;\"']+)")
FILL = "--studio-accent"


DARK_THEME_SELECTOR = re.compile(r"data-theme\s*=\s*[\"']?dark")
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")

# A grouped rule lists many selectors; the precondition names a control by finding it in the label,
# so the label must be long enough to keep the last selector of the longest group.
_LABEL_CHARS = 200


def _dark_override(label: str) -> str | None:
    """The text colour a dark-theme rule gives the control, if one targets it by class or test id."""
    classes = set(re.findall(r"\.([\w-]+)", label if "=" not in label else ""))
    for attribute in re.findall(r'class="([^"]*)"', label):
        classes.update(attribute.split())
    for name in classes:
        for _source, selector, props in contrast.class_rules(name):
            if DARK_THEME_SELECTOR.search(selector) and "color" in props:
                return props["color"]
    for test_id in re.findall(r'data-testid="([^"]+)"', label):
        for _source, css in contrast.stylesheet_texts():
            for selector, body in RULE.findall(css):
                if DARK_THEME_SELECTOR.search(selector) and test_id in selector:
                    colour = contrast.style_properties(body).get("color")
                    if colour:
                        return colour
    return None


def _labelled_fills() -> list[tuple[str, str, str | None]]:
    """(where, text colour value, dark-theme override or None) for every declaration list that fills
    with the accent and sets a text colour of its own."""
    found = []
    for use in declarations_matching(ACCENT_FILL):
        text = TEXT_COLOUR.search(use.body)
        if text:
            where = f"{use.name}:{use.line} {use.label[:_LABEL_CHARS]}"
            found.append((where, text.group(1).strip(), _dark_override(use.label)))
    return found


def test_precondition_the_sweep_finds_the_controls_the_finding_names():
    names = " | ".join(where for where, _, _ in _labelled_fills())
    for fragment in (
        ".run-btn",
        "force-run-btn",
        "chat-about-btn",
        ".chat-send-btn",
        ".run-trigger-btn",
    ):
        assert fragment in names, f"the sweep no longer finds {fragment}: {names}"


def test_every_text_colour_on_the_accent_fill_is_a_design_token():
    literal = [
        f"{where}: {value}"
        for where, value, _dark in _labelled_fills()
        if primary_token(value) is None
    ]
    assert not literal, (
        "labels on the accent fill with a colour that is not a token:\n" + "\n".join(literal)
    )


@pytest.mark.parametrize("theme", list(contrast.THEMES))
def test_every_label_on_the_accent_fill_clears_the_bar(theme):
    below = []
    for where, value, dark_override in _labelled_fills():
        token = primary_token(dark_override if theme == "dark" and dark_override else value)
        if token is None:
            continue
        ratio = contrast.pair_ratio(token, FILL, theme)
        if ratio < contrast.MIN_BODY_TEXT_RATIO:
            below.append(f"{where}: {token} on {FILL} is {ratio:.2f}:1")
    assert not below, (
        f"labels on the accent fill below {contrast.MIN_BODY_TEXT_RATIO}:1 in the {theme} theme:\n"
        + "\n".join(below)
    )
