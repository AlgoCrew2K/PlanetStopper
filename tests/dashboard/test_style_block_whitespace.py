"""The page style blocks carry no blank-line runs, and each page differs from the reference only there.

Removing a rule from a page's inline style left a doubled blank line in two templates and a lone
blank line between a comment and the rule it describes in three. Cleaning that up must change
nothing a browser renders. Two checks hold it:

* every template, with the whitespace-only lines of its style blocks removed, hashes to the digest
  the reference templates had (LF line ends), so the only difference from the reference is blank
  lines inside a style block, never a character of a rule, a comment, a script or the markup;
* no style block holds two consecutive blank lines, and each named comment sits directly above the
  line that follows it with no blank line between.

The digests were computed at the reference commit from the templates there, by `_without_blank_style_lines`
below; the computed-style check of the pages is `tests/ui/test_heading_margin_shared_browser.py`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"

# sha256 of each template, LF line ends, whitespace-only lines inside <style> blocks removed.
_REFERENCE_DIGESTS = {
    "ai_advisor.html": "e84c5a744cdd3cd4fbca65ee7e02e03efd39d79be450b28803e332d5281be278",
    # Without its .hp-more rules, which moved to static/layout.css (shared disclosure look).
    # Re-pinned after the Performance and History explainers, relabels and density layout were
    # added, and settings.html after the Settings explainers and density change (each diff
    # from the previous pin is that change and nothing else).
    "history.html": "209372a0869e67a5e614b7e3437c9e71e5b33d3c114ef5cde12ff28a0cb204cb",
    "performance.html": "3c3d00a0e96a3611c3721043ed1aaa635d62ac16945a66066522a9a7d37dd282",
    "settings.html": "6e07c5e17761f047e2493862f075709e748d7efd282a277e9fd4e636582acb78",
}

_STYLE_BLOCK = re.compile(r"(<style\b[^>]*>)(.*?)(</style>)", re.S | re.I)


def _read(name: str) -> str:
    return (TEMPLATES / name).read_bytes().decode("utf-8").replace("\r\n", "\n")


def _without_blank_style_lines(text: str) -> str:
    def keep_content(match: re.Match) -> str:
        body = "\n".join(line for line in match.group(2).split("\n") if line.strip())
        return match.group(1) + body + match.group(3)

    return _STYLE_BLOCK.sub(keep_content, text)


def _style_bodies(text: str) -> list[list[str]]:
    return [match.group(2).split("\n") for match in _STYLE_BLOCK.finditer(text)]


@pytest.mark.parametrize("name", sorted(_REFERENCE_DIGESTS))
def test_the_template_differs_from_the_reference_only_by_blank_style_lines(name):
    digest = hashlib.sha256(_without_blank_style_lines(_read(name)).encode("utf-8")).hexdigest()

    assert digest == _REFERENCE_DIGESTS[name], (
        f"{name} differs from the reference in more than the whitespace-only lines of a style block"
    )


@pytest.mark.parametrize("name", sorted(_REFERENCE_DIGESTS))
def test_no_style_block_holds_two_consecutive_blank_lines(name):
    runs = []
    for number, body in enumerate(_style_bodies(_read(name)), start=1):
        # The first and last items are the text beside the tags on their own lines.
        interior = body[1:-1] if len(body) > 2 else []
        blank = 0
        for line in interior:
            blank = blank + 1 if not line.strip() else 0
            if blank == 2:
                runs.append(f"style block {number}: {line!r} ends a run of blank lines")
    assert runs == []


# (template, text that opens the comment, text the line after the comment must start with or None)
_COMMENTS_DIRECTLY_ABOVE = [
    (
        "history.html",
        "The shared panel (.hero-section) and heading (.section-header) come from layout.css",
        ".hp-panel",
    ),
    (
        "performance.html",
        "The shared panel (.hero-section) and heading (.section-header) come from layout.css",
        ".hp-panel",
    ),
    ("settings.html", "The shared primitives (.hero-section, .section-header, .sym-card", None),
    (
        "ai_advisor.html",
        "Non-clickable cards: the shared card is button-like",
        ".sym-card.proposal-card",
    ),
]


@pytest.mark.parametrize(
    ("name", "opening", "rule"),
    _COMMENTS_DIRECTLY_ABOVE,
    ids=[f"{name}:{opening[:24]}" for name, opening, _rule in _COMMENTS_DIRECTLY_ABOVE],
)
def test_a_comment_sits_directly_above_what_follows_it(name, opening, rule):
    lines = _read(name).split("\n")
    start = next((i for i, line in enumerate(lines) if opening in line), None)
    assert start is not None, f"{name} no longer holds the comment that opens {opening!r}"
    end = next(i for i in range(start, len(lines)) if "*/" in lines[i])

    following = lines[end + 1]

    assert following.strip(), f"{name}: a blank line follows the comment ending on line {end + 1}"
    if rule is not None:
        assert following.strip().startswith(rule), (
            f"{name}: the comment must sit directly above {rule!r}, not {following.strip()!r}"
        )
