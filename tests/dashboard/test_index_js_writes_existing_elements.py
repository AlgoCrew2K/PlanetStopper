"""The dashboard script looks up only elements the dashboard page contains.

A renderer that writes to an element no template has fails silently on every poll: the status
strip, the market dot and the hero ticker looked up ids and a class that no template defines,
so their output went nowhere while the server computed the fields they read. This scan reads
`static/index.js` and the templates `GET /` renders (`index.html` and what it includes) and
fails on a lookup whose target is in neither the page nor created by the script itself.

Scope of the scan: literal arguments of `getElementById` and `querySelector[All]`. For each
selector it checks every `#id`, `.class` and `[data-testid|data-field="value"]` it names.
A name the script builds at run time (a selector that is concatenated) has no literal to check.
The poll interval comment is checked here too, against the constant it describes.
"""

from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "static" / "index.js"
_TEMPLATES = _ROOT / "templates"


def _script() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _page_source() -> str:
    """index.html and every template it includes, recursively."""
    seen: set[str] = set()
    parts: list[str] = []
    pending = ["index.html"]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        text = (_TEMPLATES / name).read_text(encoding="utf-8")
        parts.append(text)
        pending += re.findall(r"""{%\s*include\s+['"]([^'"]+)['"]""", text)
    return "\n".join(parts)


_LOOKUP_CALL = re.compile(
    r"""(?:getElementById|querySelector(?:All)?)\(\s*(['"])(.*?)\1\s*\)""", re.S
)
_ID_LOOKUP = re.compile(r"""getElementById\(\s*['"]([^'"]+)['"]\s*\)""")
_SELECTOR_LOOKUP = re.compile(r"""querySelector(?:All)?\(\s*(['"])(.*?)\1""")


def _script_outside_lookups(script: str) -> str:
    """The script with its literal lookup calls removed: what is left can create elements."""
    return _LOOKUP_CALL.sub("", script)


def _created_by_script(token: str, script: str) -> bool:
    """True when the token appears inside a string the script builds, not as a lookup."""
    rest = _script_outside_lookups(script)
    return (
        re.search(rf"""['"][^'"\n]*(?<![\w-]){re.escape(token)}(?![\w-])[^'"\n]*['"]""", rest)
        is not None
    )


def _missing_lookups(script: str, page: str) -> list[str]:
    ids = set(re.findall(r"""\bid\s*=\s*["']([^"'{}]+)["']""", page))
    classes: set[str] = set()
    for value in re.findall(r"""class\s*=\s*["']([^"']*)["']""", page):
        classes.update(value.split())
    missing: list[str] = []

    for element_id in sorted(set(_ID_LOOKUP.findall(script))):
        if element_id not in ids and not _created_by_script(element_id, script):
            missing.append(f"#{element_id} (getElementById)")

    for _quote, selector in sorted(set(_SELECTOR_LOOKUP.findall(script))):
        for element_id in re.findall(r"""#([A-Za-z_][\w-]*)""", selector):
            if element_id not in ids and not _created_by_script(element_id, script):
                missing.append(f"#{element_id} (in {selector!r})")
        for name in re.findall(r"""\.([A-Za-z_][\w-]*)""", selector):
            if name not in classes and not _created_by_script(name, script):
                missing.append(f".{name} (in {selector!r})")
        for kind, value in re.findall(r"""\[data-(testid|field)="([^"]+)"\]""", selector):
            attribute = f'data-{kind}="{value}"'
            if attribute not in page and not _created_by_script(value, script):
                missing.append(f"[{attribute}] (in {selector!r})")
    return missing


class TestEveryLookupTargetsAnElementThePageHas:
    def test_no_lookup_targets_a_missing_element(self):
        missing = _missing_lookups(_script(), _page_source())
        assert not missing, (
            "static/index.js looks up elements neither the dashboard page nor the script "
            f"creates, so whatever it writes there goes nowhere: {missing}"
        )

    def test_the_scan_sees_the_lookups_it_is_meant_to_check(self):
        """Guard against a pattern that matches nothing: the script has many literal lookups."""
        script = _script()
        assert len(set(_ID_LOOKUP.findall(script))) >= 30
        assert len(set(_SELECTOR_LOOKUP.findall(script))) >= 30

    def test_the_scan_flags_lookups_of_elements_no_template_has(self):
        """A scan that cannot fail proves nothing: plant one lookup of each kind and see them
        caught, and see a name the script creates itself pass."""
        planted = (
            _script()
            + "\n;document.getElementById('planted-id-no-template-has');"
            + "\ndocument.querySelector('.planted-class-no-template-has');"
            + "\ndocument.querySelector('[data-testid=\"planted-testid-no-template-has\"]');"
            + "\ndocument.getElementById('planted-created-id');"
            + "\nvar html = '<span id=\"planted-created-id\"></span>';\n"
        )

        missing = " ".join(_missing_lookups(planted, _page_source()))

        assert "planted-id-no-template-has" in missing
        assert "planted-class-no-template-has" in missing
        assert "planted-testid-no-template-has" in missing
        assert "planted-created-id" not in missing


class TestDeadWritersAreGone:
    def test_status_strip_and_market_dot_renderers_are_removed(self):
        script = _script()
        assert "updateStatusStrip" not in script
        assert "updateMarketDot" not in script

    def test_hero_ticker_writer_is_removed(self):
        assert "hero-ticker" not in _script()

    def test_the_status_strip_ids_are_not_looked_up(self):
        script = _script()
        for element_id in (
            "market-label",
            "strip-chip-trailing",
            "strip-chip-tp",
            "strip-chip-vwap",
        ):
            assert element_id not in script, element_id

    def test_the_live_renderers_that_share_the_list_remain(self):
        """Removing the dead entries leaves the writers the page uses."""
        script = _script()
        for name in ("updateVerdicts", "updateMiniStats", "updateCards", "updateSectionMeta"):
            assert name in script, f"{name} was removed with the dead writers"


# ---------------------------------------------------------------------------
# The comment beside the poll interval states the interval
# ---------------------------------------------------------------------------


class TestPollIntervalCommentStatesTheInterval:
    def _interval_seconds(self) -> float:
        match = re.search(r"""POLL_INTERVAL_MS\s*=\s*(\d+)""", _script())
        assert match, "POLL_INTERVAL_MS is not defined"
        return int(match.group(1)) / 1000

    def _comment_above(self, call: str) -> str:
        lines = _script().splitlines()
        index = next(i for i, line in enumerate(lines) if call in line)
        block = []
        for line in reversed(lines[max(0, index - 4) : index]):
            stripped = line.strip()
            if stripped.startswith("//"):
                block.append(stripped)
        return " ".join(reversed(block))

    def test_the_comment_above_the_poll_timer_states_the_constants_seconds(self):
        seconds = self._interval_seconds()
        comment = self._comment_above("setInterval(loadState, POLL_INTERVAL_MS)")
        stated = {float(n) for n in re.findall(r"""(\d+(?:\.\d+)?)\s*s\b""", comment)}
        assert stated == {seconds}, (
            f"the comment above the poll timer states {sorted(stated)} s but "
            f"POLL_INTERVAL_MS is {seconds:g} s: {comment!r}"
        )

    def test_no_comment_calls_the_poll_floor_fifteen_seconds(self):
        seconds = self._interval_seconds()
        assert seconds != 15
        for line in _script().splitlines():
            if "//" in line and "POLL_INTERVAL_MS" in line:
                stated = {float(n) for n in re.findall(r"""(\d+(?:\.\d+)?)\s*s\b""", line)}
                assert stated <= {seconds}, line.strip()
