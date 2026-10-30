"""The rule that turns a spec bundle's facets into the optimizer's objective.

The rule used to live inline in ``run_autotuner``; the Advisor's autotune card needs the same
answer for a stored run, so the rule is one function in ``database`` that the optimizer and the
read accessor both call. This file pins three things:

* the function reproduces, case by case, what the inline rule selected before it moved (the
  golden is recorded by executing the old block, see ``tests/fixtures/autotuner/PROVENANCE.md``);
* ``run_autotuner`` takes its objective from that function and holds no second copy of the rule;
* a facet case the rule does not distinguish cannot slip through: the golden covers every branch.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

import autotuner
import database

_GOLDEN = pathlib.Path(__file__).resolve().parents[1] / "fixtures" / "autotuner"
_CASES = json.loads((_GOLDEN / "objective_selection_golden.json").read_text(encoding="utf-8"))[
    "cases"
]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_function_reproduces_the_selection_the_inline_rule_made(case):
    assert database.objective_kind_for_facets(dict(case["facets"])) == case["objective_kind"], (
        f"facets {case['facets']!r}: the moved rule must select what the inline rule selected"
    )


def test_golden_exercises_both_known_objectives_and_each_fall_through():
    selected = {c["objective_kind"] for c in _CASES}
    assert {"crra_eu", "sortino_loss_aversion"} <= selected, (
        "the golden must record both known objectives, or a rule that always returned one of "
        "them would pass"
    )
    names = {c["name"] for c in _CASES}
    assert any("falls_through" in n for n in names), "the empty-kind fall-through is a branch"
    assert "no_facets" in names, "an empty facet map is a branch"


def test_function_does_not_mutate_its_input():
    facets = {"utility_family": "CRRA", "gamma": "2.0"}
    before = dict(facets)
    database.objective_kind_for_facets(facets)
    assert facets == before


class _StopAtObjectiveKind(Exception):
    """Raised by the spy so the run ends the moment the optimizer asks for its objective."""


def test_run_autotuner_asks_the_function_for_its_objective(monkeypatch):
    bundle_id = database.get_or_create_phase1_theory_bundle_id()
    bundle_hash = database.get_spec_bundle_by_id(bundle_id)["bundle_hash"]
    expected_facets = {
        f["facet_name"]: f["facet_value"] for f in database.get_spec_facets_for_bundle(bundle_hash)
    }
    seen: list[dict] = []

    def spy(facets_by_name):
        seen.append(dict(facets_by_name))
        raise _StopAtObjectiveKind

    monkeypatch.setattr(database, "objective_kind_for_facets", spy)

    with pytest.raises(_StopAtObjectiveKind):
        autotuner.run_autotuner({}, "2026-10-30", {}, spec_bundle_id=bundle_id)

    assert seen == [expected_facets], (
        "run_autotuner must pass the registered bundle's facets, by name, to "
        "database.objective_kind_for_facets"
    )


def test_run_autotuner_keeps_no_second_copy_of_the_rule():
    """A copy of the rule inside the optimizer would drift from the one the card reads."""
    tree = ast.parse(pathlib.Path(autotuner.__file__).read_text(encoding="utf-8"))
    run = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_autotuner"
    )
    literals = {
        n.value for n in ast.walk(run) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    }
    assert "sortino_loss_aversion" not in literals, (
        "run_autotuner still names the fall-through objective; that literal belongs to "
        "database.objective_kind_for_facets"
    )
    assert "utility_family" not in literals, (
        "run_autotuner still reads the utility_family facet itself; the rule is the "
        "function's, not the optimizer's"
    )
