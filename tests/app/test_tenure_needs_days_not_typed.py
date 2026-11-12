"""The number of aligned days a redundancy comparison needs is never typed next to the tenure line.

A tenure card says how many aligned days the review's redundancy comparison needs ("N are needed").
That number is the larger of two floors the review itself uses, so a card that typed it would keep
printing a count the review no longer uses once either floor moved. This module scans the sources
that print or build the line, and pins the review's own assignment of the floor, so a change to its
expression is a change someone has to look at here.

* The value (the oracle ``tests/advisors/_tenure_review_builders.days_needed_for_structural_gate``,
  written from the recommender's stated stress fraction and minimum) appears as a literal in no
  template, no script and not in ``app.py``.
* The floor the page prints is read from the review at request time: the slot resolver names the
  review's two names and the filter takes the number as an argument.
* ``advisors/tenure_review.py`` assigns ``floor`` from exactly ``max(_structural_needs_days(),
  retirement_recommender.MIN_OBS_FLOOR)``; a different expression there would leave the page's
  resolver describing a floor the review does not use.

The source files are the repository's own; the value is computed here from the oracle, never typed.
"""

from __future__ import annotations

import ast
import pathlib
import re

from tests.advisors import _tenure_review_builders as b

REPO = pathlib.Path(__file__).resolve().parents[2]
NEEDED = b.days_needed_for_structural_gate()
LITERAL = re.compile(rf"(?<![\w.]){NEEDED}(?![\w.])")


def _code_without_comments(path: pathlib.Path) -> str:
    """The file's text with Jinja comments, HTML comments and line comments removed."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("#", "//", "*", "/*"))
    )


def test_the_value_is_a_number_the_scan_can_tell_from_other_numbers():
    assert NEEDED > 100, "premise: the oracle gives the comparison's floor, not a small count"
    assert LITERAL.search(f"{NEEDED} are needed") and LITERAL.search(f"({NEEDED})")
    assert not LITERAL.search(f"{NEEDED}0") and not LITERAL.search(f"1{NEEDED}")
    assert not LITERAL.search(f"{NEEDED}.5")


def test_no_template_and_no_script_types_the_number_of_days_needed():
    problems = []
    for path in [REPO / "templates" / "ai_advisor.html", *sorted((REPO / "static").glob("*.js"))]:
        for number, line in enumerate(_code_without_comments(path).splitlines(), start=1):
            if LITERAL.search(line):
                problems.append(f"{path.relative_to(REPO)}: {line.strip()[:100]}")
    assert not problems, "the floor is typed as a literal:\n" + "\n".join(problems)


def test_app_py_types_no_literal_of_the_number_of_days_needed():
    source = _code_without_comments(REPO / "app.py")
    hits = [line.strip()[:100] for line in source.splitlines() if LITERAL.search(line)]
    assert not hits, "app.py types the floor as a literal:\n" + "\n".join(hits)


def test_the_page_reads_the_floor_from_the_review_through_its_two_names():
    source = (REPO / "app.py").read_text(encoding="utf-8")
    assert '"redundancy_needs_days"' in source, "the page-copy slot of the floor is not defined"
    assert "tenure_redundancy_missing" in source, "the missing-line filter is not defined"
    tree = ast.parse(source)
    resolver = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_copy_redundancy_needs_days"
        ),
        None,
    )
    assert resolver is not None, "no resolver beside the other page-copy resolvers"
    attributes = {n.attr for n in ast.walk(resolver) if isinstance(n, ast.Attribute)}
    names = {n.id for n in ast.walk(resolver) if isinstance(n, ast.Name)}
    assert "_structural_needs_days" in attributes | names, (
        "the resolver does not read the review's own search"
    )
    assert "MIN_OBS_FLOOR" in attributes | names, "the resolver does not read the recommender floor"
    numbers = [n.value for n in ast.walk(resolver) if isinstance(n, ast.Constant)]
    assert NEEDED not in numbers, "the resolver types the floor"


def test_the_review_assigns_its_floor_from_exactly_the_expression_the_page_mirrors():
    source = (REPO / "advisors" / "tenure_review.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assignments = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "floor"
    ]
    assert len(assignments) == 1, f"{len(assignments)} assignments to floor in the review"
    expected = ast.dump(
        ast.parse(
            "max(_structural_needs_days(), retirement_recommender.MIN_OBS_FLOOR)", mode="eval"
        ).body
    )
    assert ast.dump(assignments[0].value) == expected, (
        "the review's floor expression changed: the page's resolver must follow it"
    )
