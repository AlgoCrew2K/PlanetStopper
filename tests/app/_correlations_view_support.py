"""TEST-ONLY support for the Correlations tab tests (rendered from the stored retirement run).

The ``client`` and ``isolated_db`` fixtures and ``get_page`` are the retirement panel tests' own,
re-exported. On top of them: a seeding helper that runs the REAL recommender over the PM's real
Composer captures and persists its summary through the production accessor, and readers for the
rendered panel (a small HTML tree from ``tests._ux_truth_support``).

Derived records (suite fixture rule 4) are built here from one real record each, changing only the
named fields; ``tests/fixtures/retirement/derived/PROVENANCE.md`` lists every one:

* ``with_run_at_removed``, ``with_run_at``: a real producer summary without ``run_at``, or with
  ``run_at`` set to the nightly slot's instant;
* ``with_field``: a real producer summary with one stored field replaced by a malformed value;
* ``with_unmapped_exclusion_reason``: a real producer summary whose one exclusion reason is replaced;
* ``with_pair_correlations``: a real producer summary whose named pairs carry another correlation;
* ``name_override_*``: the live roster with only display names replaced;
* ``tests/fixtures/retirement/derived/summary_*.json``: the real captured row with a status and its
  dependent counts changed.

Not imported by any production module.
"""

from __future__ import annotations

import copy
import json
import pathlib
import re

from tests._ux_truth_support import Node, parse_html
from tests.advisors import _series_captures as captures

# The page fetch and the two fixtures the retirement panel tests already define, reused by name.
from tests.app.test_retirement_panel_render import _page as get_page  # noqa: F401
from tests.app.test_retirement_run_panel_render import client, isolated_db  # noqa: F401

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DERIVED_DIR = REPO_ROOT / "tests" / "fixtures" / "retirement" / "derived"

NEVER_RUN_TEXT = (
    "No realized series yet: the matrix fills after the next retirement run "
    "(nightly at 03:45 UTC, or Run now on the Retirement tab)."
)
MARKUP_SUFFIX = ' <b>bold</b> & Co "q"'
UNMAPPED_REASON = "retired_by_operator"

EMPTY_CAPTURE = "INfCn3eKsu6i4oTTqdUp"


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------


def seed_run(monkeypatch, **run_kwargs) -> dict:
    """Roster, then the real producer over the captures, then its summary persisted. Returns the
    summary exactly as stored (the JSON round trip the database makes)."""
    _recs, summary = captures.run_over_captures(monkeypatch, **run_kwargs)
    captures.persist_summary(summary)
    return json.loads(json.dumps(summary))


def seed_state_only(summary: dict) -> None:
    """Persist ``summary`` as the newest run without touching the roster."""
    captures.persist_summary(summary)


def load_derived_summary(name: str) -> dict:
    return json.loads((DERIVED_DIR / name).read_text(encoding="utf-8"))


def with_run_at_removed(summary: dict) -> dict:
    out = copy.deepcopy(summary)
    del out["run_at"]
    return out


def with_run_at(summary: dict, run_at: str) -> dict:
    out = copy.deepcopy(summary)
    out["run_at"] = run_at
    return out


def with_field(summary: dict, key: str, value) -> dict:
    """The summary with one top-level field replaced (a corrupted stored value)."""
    out = copy.deepcopy(summary)
    out[key] = value
    return out


def with_unmapped_exclusion_reason(summary: dict, reason: str = UNMAPPED_REASON) -> dict:
    out = copy.deepcopy(summary)
    out["symphonies_without_series"][0]["reason"] = reason
    return out


def with_pair_correlations(summary: dict, changes: dict[tuple[str, str], float]) -> dict:
    """The summary with the named pairs' correlation replaced; every other field is verbatim."""
    out = copy.deepcopy(summary)
    remaining = dict(changes)
    for pair in out["pairs"]:
        key = (pair["sym_a"], pair["sym_b"])
        if key in remaining:
            pair["correlation"] = remaining.pop(key)
    assert not remaining, f"pairs not in the run: {sorted(remaining)}"
    return out


def name_override_duplicate(first: str, second: str) -> dict[str, str]:
    """Two live symphonies carrying the first one's real display name."""
    return {second: captures.capture_name(first)}


def name_override_markup(sid: str) -> dict[str, str]:
    """One live symphony whose real display name gains an HTML tag and an ampersand."""
    return {sid: captures.capture_name(sid) + MARKUP_SUFFIX}


def name_override_swap(first: str, second: str) -> dict[str, str]:
    """Two live symphonies exchanging their real display names."""
    return {first: captures.capture_name(second), second: captures.capture_name(first)}


# ---------------------------------------------------------------------------
# Reading the rendered page
# ---------------------------------------------------------------------------


def tree_of(body: str) -> Node:
    return parse_html(body)


def panel_of(body_or_tree) -> Node:
    tree = parse_html(body_or_tree) if isinstance(body_or_tree, str) else body_or_tree
    panel = tree.find(testid="tab-panel-correlations")
    assert panel is not None, "the page has no Correlations panel"
    return panel


def render_panel(client) -> Node:
    return panel_of(get_page(client))


def collapse(text: str) -> str:
    return " ".join(text.split())


def correlation_text(correlation: float | None) -> str:
    """The two-decimal string a cell prints, or the word for an undefined correlation."""
    return "undefined" if correlation is None else f"{correlation:+.2f}"


def stored_pairs(summary: dict) -> dict[tuple[str, str], dict]:
    return {(p["sym_a"], p["sym_b"]): p for p in summary["pairs"]}


def pair_key(first: str, second: str) -> tuple[str, str]:
    return (first, second) if first <= second else (second, first)


def live_names(summary: dict, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Display name of every analysed symphony as the seeded roster carries it."""
    overrides = overrides or {}
    return {
        sid: overrides.get(sid, captures.capture_name(sid)) for sid in summary["series_windows"]
    }


def expected_order(names: dict[str, str]) -> list[str]:
    """Hashes by display name, then hash: the row and column order."""
    return sorted(names, key=lambda sid: (names[sid], sid))


class Grid:
    """The rendered matrix: labels in order and the cell nodes per row."""

    def __init__(self, panel: Node) -> None:
        self.panel = panel
        self.table = panel.find(testid="correlation-matrix-table")
        assert self.table is not None, "the panel has no matrix table"
        self.col_labels = self.table.find_all(testid="corr-col-label")
        self.row_labels = self.table.find_all(testid="corr-row-label")
        self.rows: list[list[Node]] = []
        for label in self.row_labels:
            tr = label.parent
            assert tr is not None and tr.tag == "tr"
            self.rows.append(
                [c for c in tr.children if isinstance(c, Node) and c.tag == "td"],
            )
        self.row_syms = [n.attrs["data-sym"] for n in self.row_labels]
        self.col_syms = [n.attrs["data-sym"] for n in self.col_labels]

    def off_diagonal(self) -> list[Node]:
        return self.table.find_all(testid="corr-cell")

    def diagonal(self) -> list[Node]:
        return self.table.find_all(testid="corr-cell-diagonal")

    def cell_at(self, row_sym: str, col_sym: str) -> Node:
        return self.rows[self.row_syms.index(row_sym)][self.col_syms.index(col_sym)]

    def cells_of_pair(self, first: str, second: str) -> list[Node]:
        a, b = pair_key(first, second)
        return [
            n
            for n in self.off_diagonal()
            if (n.attrs.get("data-sym-a"), n.attrs.get("data-sym-b")) == (a, b)
        ]


def grid_of(client) -> Grid:
    return Grid(render_panel(client))


def fill_pct(cell: Node) -> float | None:
    """The ``--corr-fill-pct`` percentage in a cell's inline style, or None when it has none."""
    found = re.search(r"--corr-fill-pct\s*:\s*([0-9.]+)\s*%", cell.attrs.get("style", ""))
    return float(found.group(1)) if found else None


def oracle_band(printed: str, threshold: float) -> str:
    """The band of a printed correlation, written from the rule: danger at or above the screen
    threshold, warn from 0.40 up to it, diversifying at or below -0.40, neutral between."""
    if printed == "undefined":
        return "undefined"
    value = float(printed)
    if value >= threshold:
        return "danger"
    if value >= 0.40:
        return "warn"
    if value <= -0.40:
        return "diversifying"
    return "neutral"
