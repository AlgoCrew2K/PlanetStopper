"""Shared helpers for the data-truth route and render tests.

Two things live here so the spec files that extend them do not each copy them:

* a small HTML tree built from the standard library parser, so a test reads the text
  and the ``title`` attribute of one rendered element instead of searching raw markup;
* loaders for the captured droplet state (``tests/fixtures/prod_droplet``), so a test
  picks a real symphony record by the property it needs instead of typing a name.

The parser is deliberately minimal: it keeps tags, attributes and text, honours void
elements, and tolerates unclosed tags. It is not a browser; a rendered layout is judged
on a live page, never here.
"""

from __future__ import annotations

import json
import pathlib
import re
from html.parser import HTMLParser

REPO = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"

_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param"}
    | {"source", "track", "wbr"}
)


class Node:
    """One element. ``children`` holds ``Node`` objects and plain text strings."""

    def __init__(self, tag: str, attrs: dict[str, str], parent: Node | None) -> None:
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[Node | str] = []

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    @property
    def title(self) -> str:
        return self.attrs.get("title", "")

    @property
    def text(self) -> str:
        """Visible text of this element and its descendants, whitespace collapsed."""
        parts: list[str] = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text)
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def iter(self):
        yield self
        for child in self.children:
            if isinstance(child, Node):
                yield from child.iter()

    def find_all(
        self,
        tag: str | None = None,
        *,
        testid: str | None = None,
        cls: str | None = None,
        attr: tuple[str, str] | None = None,
    ) -> list[Node]:
        found = []
        for node in self.iter():
            if tag is not None and node.tag != tag:
                continue
            if testid is not None and node.attrs.get("data-testid") != testid:
                continue
            if cls is not None and cls not in node.classes:
                continue
            if attr is not None and node.attrs.get(attr[0]) != attr[1]:
                continue
            found.append(node)
        return found

    def find(self, *args, **kwargs) -> Node | None:
        hits = self.find_all(*args, **kwargs)
        return hits[0] if hits else None

    def ancestor_with_class(self, cls: str) -> Node | None:
        node = self.parent
        while node is not None:
            if cls in node.classes:
                return node
            node = node.parent
        return None

    def titles_in_subtree(self) -> list[str]:
        return [n.title for n in self.iter() if n.title]


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root", {}, None)
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = Node(tag, {k: (v if v is not None else "") for k, v in attrs}, self._stack[-1])
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)


def parse_html(html: str) -> Node:
    builder = _Builder()
    builder.feed(html)
    builder.close()
    return builder.root


# ---------------------------------------------------------------------------
# Captured droplet state
# ---------------------------------------------------------------------------


def load_captured_bot_state() -> dict:
    """The droplet's captured ``bot_state`` (Composer hash -> symphony entry)."""
    path = FIXTURES / "prod_droplet" / "bot_state_positions.json"
    return json.loads(path.read_text(encoding="utf-8"))


def captured_symphonies() -> list[tuple[str, str]]:
    """(Composer hash, display name) for every captured symphony, in file order."""
    return [
        (key, entry["name"])
        for key, entry in load_captured_bot_state().items()
        if isinstance(entry, dict) and "name" in entry
    ]


def pick_symphony(predicate) -> tuple[str, str]:
    """The first captured (hash, name) whose name satisfies ``predicate``."""
    for pair in captured_symphonies():
        if predicate(pair[1]):
            return pair
    raise AssertionError("no captured symphony has the property this test needs")


def two_distinct_symphonies() -> tuple[tuple[str, str], tuple[str, str]]:
    pairs = captured_symphonies()
    return pairs[0], pairs[1]


# ---------------------------------------------------------------------------
# Stored autotune runs and the spec bundles they ran under
# ---------------------------------------------------------------------------


def register_bundle(facets: dict[str, str]) -> str:
    """Store a spec bundle with the given facets and return its hash."""
    import database

    canonical = database.canonicalize_facets_json(facets)
    bundle_hash = database.hash_facets_json(canonical)
    database.insert_spec_bundle(bundle_hash=bundle_hash, facets_json=canonical)
    for name, value in facets.items():
        database.insert_spec_bundle_facet(
            bundle_hash=bundle_hash,
            facet_name=name,
            facet_value=value,
            freeze_discipline="THEORY",
            justification="data-truth test bundle",
        )
    return bundle_hash


def phase1_facets() -> dict[str, str]:
    """The canonical Phase-1 facets the live autotuner registers (CRRA family)."""
    import database

    return {
        "gamma": database.PHASE1_THEORY_GAMMA,
        "utility_family": database.PHASE1_THEORY_UTILITY_FAMILY,
        "wealth_argument": database.PHASE1_THEORY_WEALTH_ARGUMENT_FORMULA,
    }


def save_run(
    symphony_id: str,
    spec_bundle_id: str | None,
    stamp: str = "2026-10-26T04:00:00",
    *,
    baseline_decision: str = "Applied",
    naive_sharpe: float | None = 1.2345678,
    selection_tstat: float | None = 2.3456789,
    frozen_eval_sharpe: float | None = None,
) -> int:
    """Write one autotune run through the real writer and return its row id."""
    import database

    return database.save_autotune_run(
        stamp,
        symphony_id,
        0.5,
        1.0,
        baseline_decision,
        0.4,
        0.3,
        selection_tstat=selection_tstat,
        naive_sharpe=naive_sharpe,
        frozen_eval_sharpe=frozen_eval_sharpe,
        spec_bundle_id=spec_bundle_id,
    )


# A display name that carries markup. Derived record: one captured entry with only ``name``
# replaced (see tests/fixtures/ux_data_truth/PROVENANCE.md).
MARKUP_NAME = "<b>bold</b> & Co"


def state_with_markup_name(symphony_hash: str) -> dict:
    """The captured bot_state with one symphony's display name replaced by ``MARKUP_NAME``."""
    state = load_captured_bot_state()
    state[symphony_hash] = {**state[symphony_hash], "name": MARKUP_NAME}
    return state
