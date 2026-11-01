"""The History exits table's style rules break lines only where the text allows.

The default-run counterpart of tests/ui/test_history_exits_table_fit_browser.py (browser modules do not
run in CI). ``overflow-wrap: anywhere`` and any ``word-break`` let a line end inside a word, which is
how a phone render of the table read "Breakdo wn". A rule on ``.triggers-tbl`` or its cells carries
neither; where a word is wider than its column the table has to give the column more room or the text
a smaller size, not cut the word.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / "templates" / "history.html"
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def _declarations_for_triggers_table() -> list[tuple[str, str]]:
    source = CSS_COMMENT.sub("", TEMPLATE.read_text(encoding="utf-8"))
    return [
        (" ".join(selector.split()), " ".join(body.split()).lower())
        for selector, body in RULE.findall(source)
        if ".triggers-tbl" in selector
    ]


def test_premise_the_template_styles_the_exits_table():
    assert _declarations_for_triggers_table(), (
        "no rule in templates/history.html names .triggers-tbl"
    )


@pytest.mark.parametrize(
    "forbidden",
    [r"overflow-wrap\s*:\s*anywhere", r"word-wrap\s*:\s*anywhere", r"word-break\s*:"],
    ids=["overflow-wrap-anywhere", "word-wrap-anywhere", "word-break"],
)
def test_no_rule_on_the_exits_table_lets_a_line_end_inside_a_word(forbidden):
    offenders = [
        selector
        for selector, body in _declarations_for_triggers_table()
        if re.search(forbidden, body)
    ]
    assert offenders == [], f"rules that break words: {offenders}"
