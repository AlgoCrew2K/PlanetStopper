"""The helpers of `_state_poll_worlds.py` annotate what a reader needs to call them.

The world builders take pytest's `monkeypatch` and `tmp_path`, and three small helpers returned
nothing a reader could see without reading the body. The module is read as source, never
imported: importing it builds no world, and a check of its text needs no app.

Three things hold for every function and method in the module, nested ones included:

* every parameter named `monkeypatch` or `tmp_path` carries an annotation;
* every function has a return annotation;
* each of the worlds `WORLDS` names (each a builder taking `monkeypatch` and `tmp_path`) is one
  of the functions checked, so the family is read from the module and not from a count typed here.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).with_name("_state_poll_worlds.py")
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
FUNCTIONS = [
    node for node in ast.walk(TREE) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
]
_FIXTURE_PARAMETERS = ("monkeypatch", "tmp_path")


def _parameters(function):
    return [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]


def test_every_monkeypatch_and_tmp_path_parameter_is_annotated():
    missing = [
        f"{function.name}({parameter.arg})"
        for function in FUNCTIONS
        for parameter in _parameters(function)
        if parameter.arg in _FIXTURE_PARAMETERS and parameter.annotation is None
    ]
    assert missing == []


def test_every_function_has_a_return_annotation():
    assert [
        f"{function.name}:{function.lineno}" for function in FUNCTIONS if function.returns is None
    ] == []


def _worlds_dict() -> ast.Dict:
    for node in TREE.body:
        target = node.target if isinstance(node, ast.AnnAssign) else None
        if isinstance(target, ast.Name) and target.id == "WORLDS":
            assert isinstance(node.value, ast.Dict)
            return node.value
    raise AssertionError("WORLDS is not assigned at module level")


def test_every_named_world_builder_is_a_function_the_checks_cover():
    names = [value.id for value in _worlds_dict().values if isinstance(value, ast.Name)]
    assert names, "WORLDS names no builder"
    by_name = {function.name: function for function in FUNCTIONS}
    for name in names:
        builder = by_name[name]
        arguments = {parameter.arg: parameter for parameter in _parameters(builder)}
        assert set(_FIXTURE_PARAMETERS) <= set(arguments), f"{name} does not take both fixtures"
        assert all(arguments[arg].annotation is not None for arg in _FIXTURE_PARAMETERS), name
        assert builder.returns is not None, name
