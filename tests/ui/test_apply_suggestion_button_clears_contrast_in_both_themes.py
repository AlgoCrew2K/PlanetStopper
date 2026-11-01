"""The Apply suggestion button's text clears 4.5:1 on its fill in both themes.

The button was ``--studio-white`` text on a ``--studio-pos`` fill: 5.3:1 in the light theme, 2.0:1 in the
dark one, where the positive colour is a light green. The fix is a text token that contrasts with the
fill in both themes, not a hard-coded colour for one of them.

Real call path: the real ``static/ai_advisor.js`` source (the button is drawn by the script, its colours
named as ``cssVar('--token')`` in an inline style) and the real tokens (``static/tokens.css``), through
tests/_contrast_support.py. The fill and the text are read from the button's inline style, or from the
rule of its class where the colours moved there.

Contract pinned
* Both the fill and the text are design tokens, never a literal colour.
* The composited ratio of the text over the fill is at least 4.5 in the light and in the dark theme.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests import _contrast_support as contrast

JS = Path(__file__).resolve().parents[2] / "static" / "ai_advisor.js"
CLASS_NAME = "suggestion-apply-btn"


def _colours() -> tuple[str, str]:
    """(text value, fill value) the button is drawn with, as the source names them."""
    source = JS.read_text(encoding="utf-8")
    start = source.index(f'class="{CLASS_NAME}"')
    segment = source[start : source.index("Apply suggestion", start)]
    inline = dict(re.findall(r"(background|color):'\s*\+\s*(cssVar\('--[\w-]+'\)|[^;']+)", segment))
    text = inline.get("color")
    fill = inline.get("background")
    for _src, _sel, props in contrast.class_rules(CLASS_NAME):
        text = text if text else props.get("color")
        fill = fill if fill else props.get("background") or props.get("background-color")
    assert text and fill, f"the button's text {text!r} and fill {fill!r} are not both named"
    return _as_var(text), _as_var(fill)


def _as_var(value: str) -> str:
    named = re.fullmatch(r"cssVar\('(--[\w-]+)'\)", value.strip())
    return f"var({named.group(1)})" if named else value.strip()


def test_the_button_is_drawn_with_tokens_only():
    text, fill = _colours()
    assert contrast.token_of(text), f"the button's text is {text!r}, not a design token"
    assert contrast.token_of(fill), f"the button's fill is {fill!r}, not a design token"


@pytest.mark.parametrize("theme", list(contrast.THEMES))
def test_the_text_clears_the_bar_on_the_fill(theme):
    text, fill = _colours()
    ratio = contrast.pair_ratio(contrast.token_of(text), contrast.token_of(fill), theme)
    assert ratio >= contrast.MIN_BODY_TEXT_RATIO, (
        f"{text} on {fill} is {ratio:.2f}:1 in the {theme} theme, below {contrast.MIN_BODY_TEXT_RATIO}:1"
    )
