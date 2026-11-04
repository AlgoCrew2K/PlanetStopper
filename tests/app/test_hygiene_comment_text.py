"""Three comments in app.py state what holds in the code beside them.

* The comment above the close-snapshot sort says why the account lists are ordered in place.
  The view a snapshot holds is handed out as deep copies (``get_or_build`` returns a copy either
  way), so the sort touches the caller's own lists; the comment must not say the held view is
  shared with ``GET /`` or that a copy would change what a page reads.
* The comment above the per-symphony today/cumulative/drawdown attach loop of ``get_state`` must
  not give a template as its reason: ``GET /api/state`` renders no template.
* The comment above ``_FROZEN_SYM_DEFAULTS`` must say where the defaults are read, by the name of
  the function that reads them, and give no template as a reason.

The comments are found by the statement each precedes, never by a line number, and read as text.
"""

from __future__ import annotations

import pathlib
import re

import pytest

APP_PY = pathlib.Path(__file__).resolve().parents[2] / "app.py"
LINES = APP_PY.read_text(encoding="utf-8").splitlines()


def _only(pattern: str) -> int:
    """Index of the one source line matching `pattern`."""
    hits = [i for i, line in enumerate(LINES) if re.search(pattern, line)]
    assert len(hits) == 1, f"expected one line matching {pattern!r}, found {len(hits)}"
    return hits[0]


def _comment_above(statement_index: int, max_code_lines_between: int = 0) -> str:
    """The comment block directly above a statement (a few code lines may sit between)."""
    index = statement_index - 1
    skipped = 0
    while index >= 0 and not LINES[index].lstrip().startswith("#"):
        skipped += 1
        assert skipped <= max_code_lines_between, "no comment block above the statement"
        index -= 1
    block = []
    while index >= 0 and LINES[index].lstrip().startswith("#"):
        block.append(LINES[index].lstrip().lstrip("#").strip())
        index -= 1
    assert block, "no comment block above the statement"
    return " ".join(reversed(block))


class TestCloseSnapshotSortComment:
    @pytest.fixture(scope="class")
    def comment(self) -> str:
        sort_line = _only(r"_syms\.sort\(key=lambda s: \(s\.get\(\"name\"\)")
        return _comment_above(sort_line, max_code_lines_between=4).lower()

    def test_it_does_not_claim_the_held_view_is_shared_with_the_dashboard_page(self, comment):
        assert "get /" not in comment
        assert "shared" not in comment

    def test_it_does_not_say_a_copy_would_change_what_a_page_reads(self, comment):
        assert not re.search(r"\bcopy\b.*\b(change|page)\b|\bpage\b.*\bcopy\b", comment), (
            "the comment says a sorted copy would change what a page reads; the hold hands out "
            "deep copies, so that is not true"
        )
        assert "page reads" not in comment

    def test_it_states_that_the_sort_is_in_place_on_the_snapshots_own_lists(self, comment):
        assert "in place" in comment
        assert "get_or_build" in comment or "deep cop" in comment, (
            "the comment must give the real reason: the snapshot's lists are the caller's own "
            "copies (get_or_build returns deep copies)"
        )


class TestTemplateReasonsAreGone:
    def test_the_attach_loop_comment_names_no_template(self):
        anchor = _only(r"^\s*_today_et = figures_day\s*$")
        comment = _comment_above(anchor)
        assert "template" not in comment.lower(), (
            f"GET /api/state renders no template, so the comment must not give one: {comment!r}"
        )
        assert "per-symphony" in comment.lower(), (
            "the comment must still say what the loop attaches"
        )

    def test_the_frozen_defaults_comment_names_no_template_and_names_its_reader(self):
        anchor = _only(r"^_FROZEN_SYM_DEFAULTS\b")
        comment = _comment_above(anchor)
        assert "template" not in comment.lower(), (
            f"the comment must not give a template as the reason: {comment!r}"
        )
        assert "_normalize_frozen_symphonies" in comment, (
            "the comment must say where the defaults are read: _normalize_frozen_symphonies"
        )

    def test_the_named_reader_really_reads_the_defaults(self):
        """The comment's claim holds: that function's body is where the constant is read."""
        start = _only(r"^def _normalize_frozen_symphonies\b")
        end = start + 1
        while end < len(LINES) and not re.match(r"^(def |class |@|[A-Za-z_])", LINES[end]):
            end += 1
        body = "\n".join(LINES[start:end])
        assert "_FROZEN_SYM_DEFAULTS" in body
