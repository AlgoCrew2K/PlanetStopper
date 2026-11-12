"""No test asserts a rule that was withdrawn: an empty-region bound, a figure-row span, a height bound.

An earlier change met "no empty rectangle of 240 by 80 px at 1440" by moving tables and text to fill
space, and that bound is withdrawn: a table or line may end before its panel's right edge, and
columns sit at the width their content needs. Its companions are withdrawn with it: figure rows that
span 90 percent of their container, and a bound on how much taller a page grows at 375 px. A test of
any of them would fail a page that is right, so the tests are removed, not loosened, and this scan
keeps them removed.

The scan reads every test module's syntax tree (no import, no run) and reports:

* a name that is one of the withdrawn measures (``EMPTY_REGION_JS``, ``FIGURE_SPAN_JS``,
  ``HEIGHT_FACTS_JS``), the key their records used (``largest_empty``, ``heights_375``), or the
  selector set the empty-region search ran over (``PANEL_SELECTOR``), as a definition, a reference,
  an attribute, a keyword or a quoted key;
* a bound of 240 by 80: a pair of integer constants 240 and 80 in one tuple, list or call, or a
  constant of 240 assigned to a name that says it is an empty-region width;
* a test or helper whose name says it asserts an empty region, an empty rectangle or a figure-row
  span.

The scan itself is shown to report each of these on planted source, so a clean tree means something.
"""

from __future__ import annotations

import ast
import pathlib

TESTS = pathlib.Path(__file__).resolve().parents[1]
THIS_FILE = pathlib.Path(__file__).resolve()

WITHDRAWN_NAMES = frozenset(
    {
        "EMPTY_REGION_JS",
        "FIGURE_SPAN_JS",
        "HEIGHT_FACTS_JS",
        "PANEL_SELECTOR",
        "largest_empty",
        "heights_375",
    }
)
WITHDRAWN_NAME_PARTS = ("empty_region", "empty_rectangle", "figure_span")
EMPTY_WIDTH_NAMES = ("EMPTY_MIN_WIDTH", "EMPTY_MIN_W", "MIN_EMPTY_W")


def _is_int(node: ast.AST, value: int) -> bool:
    return isinstance(node, ast.Constant) and type(node.value) is int and node.value == value


def _holds_240_by_80(elements: list[ast.expr]) -> bool:
    return any(_is_int(e, 240) for e in elements) and any(_is_int(e, 80) for e in elements)


def scan_source(source: str, label: str = "<source>") -> list[str]:
    """Every withdrawn construct in ``source``, as ``label:line: what``."""
    tree = ast.parse(source)
    found: list[str] = []

    def hit(node: ast.AST, what: str) -> None:
        found.append(f"{label}:{getattr(node, 'lineno', 0)}: {what}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in WITHDRAWN_NAMES:
            hit(node, f"names the withdrawn measure {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in WITHDRAWN_NAMES:
            hit(node, f"reads the withdrawn measure {node.attr}")
        elif isinstance(node, ast.keyword) and node.arg in WITHDRAWN_NAMES:
            hit(node, f"passes the withdrawn measure {node.arg}")
        elif isinstance(node, ast.alias) and node.name in WITHDRAWN_NAMES:
            hit(node, f"imports the withdrawn measure {node.name}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for name in WITHDRAWN_NAMES:
                if name in node.value:
                    hit(node, f"quotes the withdrawn measure {name}")
                    break
        elif isinstance(node, (ast.Tuple, ast.List)) and _holds_240_by_80(node.elts):
            hit(node, "holds a bound of 240 by 80")
        elif isinstance(node, ast.Call) and _holds_240_by_80(node.args):
            hit(node, "passes a bound of 240 by 80")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lowered = node.name.lower()
            if any(part in lowered for part in WITHDRAWN_NAME_PARTS):
                hit(node, f"defines {node.name}, which asserts a withdrawn rule")
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and _is_int(node.value, 240)
                    and any(part in target.id for part in EMPTY_WIDTH_NAMES)
                ):
                    hit(node, f"{target.id} is an empty-region width of 240")
    return found


def scan_tree() -> list[str]:
    problems: list[str] = []
    for path in sorted(TESTS.rglob("*.py")):
        if path.resolve() == THIS_FILE:
            continue
        label = path.relative_to(TESTS.parent).as_posix()
        problems += scan_source(path.read_text(encoding="utf-8"), label)
    return problems


def test_no_test_asserts_an_empty_region_a_figure_span_or_a_height_bound():
    problems = scan_tree()
    assert not problems, "withdrawn rules are asserted again:\n" + "\n".join(problems)


def test_the_scan_reads_the_whole_test_tree_and_not_an_empty_one():
    modules = list(TESTS.rglob("*.py"))
    assert len(modules) > 500, f"the scan sees only {len(modules)} test modules"
    assert (TESTS / "ui" / "_page_explainers.py") in modules


PLANTED = {
    "a measure name": "import x\nx.page.evaluate(harness.EMPTY_REGION_JS, [])",
    "the span measure": "FIGURE_SPAN_JS = 'x'",
    "the height measure": "def f(h):\n    return h.HEIGHT_FACTS_JS",
    "a record key": "def f(r):\n    return r['hosts']['heights_375']",
    "the search result": "found = [o for o in r if o['largest_empty']]",
    "the panel selector": "S = harness.PANEL_SELECTOR",
    "a quoted measure": "JS = 'const largest_empty = best;'",
    "an imported measure": "from m import EMPTY_REGION_JS",
    "a tuple bound": "MIN_W, MIN_H = 240, 80",
    "a list bound": "args = ['.panel', 240, 80]",
    "a call bound": "check('.panel', 240, 80)",
    "a width constant": "EMPTY_MIN_WIDTH_PX = 240",
    "an empty-region test": "def test_no_panel_holds_an_empty_rectangle():\n    pass",
    "a figure-span test": "def test_every_row_of_figures_spreads_across_its_container_figure_span():\n    pass",
}


def test_the_scan_reports_each_withdrawn_construct_on_planted_source():
    for what, source in PLANTED.items():
        assert scan_source(source), f"the scan misses {what}"


def test_the_scan_does_not_report_ordinary_numbers_and_names():
    clean = (
        "timeout = 240\n"
        "box = (240, 100)\n"
        "ratio = [80, 90]\n"
        "def test_a_table_may_end_before_its_panel_edge():\n    pass\n"
        "MIN_HISTORY_DAYS = 80\n"
        "call(240)\n"
    )
    assert scan_source(clean) == []
