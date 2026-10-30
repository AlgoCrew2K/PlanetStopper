"""Read the dashboard's shared primitives off the real ``GET /`` response.

Used by the committed baseline script
(``tests/fixtures/dashboard/primitives_baseline/capture_baseline.py``) and by
``tests/ui/test_dashboard_primitives_shared.py``, so the base capture and the head check go
through one implementation.

What is read, and from where:

* ``body_structure(html)``: the tag and class tokens of every element of the ``<body>`` the
  route returned, in document order. No text, no ids, no attribute values: what the server
  chooses to render, not the data it renders.
* ``resolved_primitive_rules(html)``: for every selector that names one of the dashboard
  primitive classes, the declarations that win when the sheets the page loads are read in
  document order (``<link rel="stylesheet">`` files under ``static/`` and inline
  ``<style>`` blocks, as they appear in the markup), keyed by the enclosing at-rule and the
  selector text. A later declaration of a property replaces an earlier one unless the
  earlier one is ``!important``.
* ``ordered_primitive_rules(css)``: the rules of one stylesheet that name a primitive, in
  source order, with their declarations in source order.

Not imported by any production module.
"""

from __future__ import annotations

import pathlib
import re
from html.parser import HTMLParser
from unittest.mock import patch

REPO = pathlib.Path(__file__).resolve().parents[2]
STATIC = REPO / "static"
PRIMITIVES_BASELINE_DIR = REPO / "tests" / "fixtures" / "dashboard" / "primitives_baseline"

# The primitive classes the shared definition owns.
PRIMITIVE_CLASSES = (
    "hero-section",
    "legend-as-of",
    "mini-stats",
    "mini-stat",
    "mini-stat-value",
    "mini-stat-label",
    "section-header",
    "sym-card",
    "card-name",
    "status-pill",
)

_CLASS_RE = re.compile(r"\.(" + "|".join(re.escape(c) for c in PRIMITIVE_CLASSES) + r")(?![\w-])")
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source"}


# ---------------------------------------------------------------------------
# Markup
# ---------------------------------------------------------------------------


class _Doc(HTMLParser):
    """Collects body elements and the stylesheets in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_body = False
        self.structure: list[list] = []
        self.sheets: list[tuple[str, str]] = []  # ("link", href) or ("style", css)
        self._style: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "body":
            self.in_body = True
        if tag == "link" and "stylesheet" in (attr.get("rel") or "").lower():
            self.sheets.append(("link", attr.get("href") or ""))
        if tag == "style":
            self._style = []
        if self.in_body and tag != "body":
            classes = sorted((attr.get("class") or "").split())
            self.structure.append([tag, classes])

    def handle_endtag(self, tag):
        if tag == "style" and self._style is not None:
            self.sheets.append(("style", "".join(self._style)))
            self._style = None

    def handle_data(self, data):
        if self._style is not None:
            self._style.append(data)


def _parse(html: str) -> _Doc:
    doc = _Doc()
    doc.feed(html)
    doc.close()
    return doc


def body_structure(html: str) -> list[list]:
    """``[[tag, [class, ...]], ...]`` for every element inside ``<body>``, in document order."""
    return _parse(html).structure


def tags_with_class(structure: list[list], class_name: str) -> int:
    return sum(1 for _tag, classes in structure if class_name in classes)


# ---------------------------------------------------------------------------
# Stylesheets
# ---------------------------------------------------------------------------


def _split_selectors(head: str) -> list[str]:
    out, depth, cur = [], 0, []
    for ch in head:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur).strip())
    return [" ".join(s.split()) for s in out if s.strip()]


def _declarations(body: str) -> list[tuple[str, str]]:
    decls: list[tuple[str, str]] = []
    depth = 0
    cur: list[str] = []
    parts: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == ";" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    for part in parts:
        if ":" not in part:
            continue
        prop, _, value = part.partition(":")
        prop = prop.strip().lower()
        if prop:
            decls.append((prop, " ".join(value.split())))
    return decls


def iter_rules(css: str):
    """Yield ``(at_context, [selectors], [(prop, value), ...])`` for every style rule.

    ``at_context`` is the chain of enclosing at-rule preludes joined by `` | `` (empty at the
    top level). Rules inside ``@keyframes`` and ``@font-face`` are not style rules and are
    skipped.
    """
    css = _COMMENT_RE.sub("", css)

    def walk(text: str, context: str):
        i, n = 0, len(text)
        while i < n:
            brace = text.find("{", i)
            if brace == -1:
                return
            head = " ".join(text[i:brace].split())
            depth, j = 1, brace + 1
            while j < n and depth:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                j += 1
            inner = text[brace + 1 : j - 1]
            if head.startswith("@"):
                name = head.split()[0].lower()
                if name in ("@media", "@supports", "@layer", "@container"):
                    yield from walk(inner, f"{context} | {head}" if context else head)
            elif head:
                yield context, _split_selectors(head), _declarations(inner)
            i = j

    yield from walk(css, "")


def names_primitive(selector: str) -> bool:
    return _CLASS_RE.search(selector) is not None


def ordered_primitive_rules(css: str) -> list[dict]:
    """The rules of ``css`` that name a primitive class, in source order."""
    rules = []
    for context, selectors, decls in iter_rules(css):
        if any(names_primitive(s) for s in selectors):
            rules.append(
                {
                    "context": context,
                    "selectors": selectors,
                    "declarations": [[p, v] for p, v in decls],
                }
            )
    return rules


def _sheet_text(kind: str, payload: str) -> str | None:
    if kind == "style":
        return payload
    href = payload.split("?", 1)[0]
    if href.startswith("/static/"):
        path = STATIC / href[len("/static/") :]
        return path.read_text(encoding="utf-8") if path.is_file() else None
    return None  # an external sheet is not the repository's to read


def document_sheets(html: str) -> list[tuple[str, str]]:
    """``("link", href)`` and ``("style", css)`` entries in document order."""
    return list(_parse(html).sheets)


def linked_static_sheets(html: str) -> list[str]:
    """The ``/static/`` stylesheet paths the markup links, in document order."""
    return [p.split("?", 1)[0] for kind, p in _parse(html).sheets if kind == "link" and p]


def resolved_primitive_rules(html: str) -> dict[str, dict[str, str]]:
    """``{"<context>||<selector>": {property: value}}`` as the page's sheets resolve them."""
    resolved: dict[str, dict[str, tuple[str, bool]]] = {}
    for kind, payload in _parse(html).sheets:
        css = _sheet_text(kind, payload)
        if css is None:
            continue
        for context, selectors, decls in iter_rules(css):
            for selector in selectors:
                if not names_primitive(selector):
                    continue
                slot = resolved.setdefault(f"{context}||{selector}", {})
                for prop, value in decls:
                    important = value.rstrip().lower().endswith("!important")
                    old = slot.get(prop)
                    if old is not None and old[1] and not important:
                        continue
                    slot[prop] = (value, important)
    return {key: {p: v for p, (v, _imp) in props.items()} for key, props in resolved.items()}


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------


def render_dashboard_html() -> str:
    """``GET /`` against the real route with the captured bot state seeded through the writer.

    The state is every record of ``tests/fixtures/prod_droplet/bot_state_positions.json``,
    verbatim (live names and ids as captured; triggered cards and standby cards both occur).
    The market is pinned open, and the network is refused. The caller owns the database: under
    pytest the autouse fixture isolates it; a script initialises its own scratch database first.
    """
    import json

    import requests

    import app as app_module
    import database

    positions = json.loads(
        (REPO / "tests" / "fixtures" / "prod_droplet" / "bot_state_positions.json").read_text(
            encoding="utf-8"
        )
    )
    database.save_state(positions)

    def refuse(self, method, url, *a, **k):
        raise requests.ConnectionError(f"network refused in capture: {method} {url}")

    app_module.app.config["TESTING"] = True
    with (
        patch.object(requests.sessions.Session, "request", refuse),
        patch.object(app_module, "get_market_state", lambda *_a, **_k: "open"),
        patch.object(app_module, "_auth_check_enabled", False),
        patch.object(app_module, "ENV_FILE_PATH", str(PRIMITIVES_BASELINE_DIR / "absent.env")),
    ):
        client = app_module.app.test_client()
        response = client.get("/")
        assert response.status_code == 200, f"GET / answered {response.status_code}"
        return response.data.decode("utf-8")
