"""One shared z-score definition in ``math_engine`` (hygiene-waves-3-4, AC-3, S-3.1 to S-3.3).

The reference carries three nested ``_z`` closures (in ``run_monte_carlo``,
``compute_portfolio_cvar`` and ``compute_regime_match_quality``), each::

    if std == 0.0: return np.zeros_like(values, dtype=float)
    return (values - mean) / std

The slice replaces them with one module-level ``math_engine._zscore(values, mean, std)``.

* S-3.1 compares the exact output bits (``float.hex``) of the three functions with the outputs the
  reference produced on the same inputs (``tests/fixtures/hygiene_waves_3_4/engine_math``, produced
  by ``produce_ref_records.py``; inputs in ``tests/math_engine/_zscore_inputs.py``). It passes on
  the reference code by design.
* S-3.2 compares ``_zscore`` with a verbatim copy of the reference closure over the std family
  (0.0, -0.0, NaN, positive with NaN/inf/-inf values, an empty array, a 2-D array): elementwise,
  dtype and shape included. Fails until ``_zscore`` exists.
* S-3.3 scans the ``math_engine.py`` AST: exactly one z-score definition, no nested ``_z`` and the
  three functions call it. Fails until the closures are replaced.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import numpy as np
import pytest

import math_engine
from tests.math_engine._zscore_inputs import record_outputs

RECORDS = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "hygiene_waves_3_4"
    / "engine_math"
    / "zscore_ref_outputs.json"
)
FAMILY_FUNCTIONS = ("run_monte_carlo", "compute_portfolio_cvar", "compute_regime_match_quality")


def _reference_z(values, mean, std):
    """The reference closure, copied verbatim from math_engine.py (reference commit)."""
    if std == 0.0:
        return np.zeros_like(values, dtype=float)
    return (values - mean) / std


class TestOutputsAreBitwiseTheReferenceOutputs:
    @pytest.fixture(scope="class")
    def now(self):
        return record_outputs()

    @pytest.fixture(scope="class")
    def ref(self):
        return json.loads(RECORDS.read_text(encoding="utf-8"))

    def test_the_records_cover_every_case_and_function(self, now, ref):
        assert set(ref) == set(now)
        for case in ref:
            assert set(ref[case]) == set(FAMILY_FUNCTIONS)

    @pytest.mark.parametrize("function", FAMILY_FUNCTIONS)
    def test_every_case_output_equals_the_reference_bits(self, now, ref, function):
        for case in ref:
            assert now[case][function] == ref[case][function], f"{function} on {case}"

    def test_the_cases_are_discriminating_not_one_constant(self, ref):
        monte_carlo = {ref[c]["run_monte_carlo"] for c in ref}
        regime = {json.dumps(ref[c]["compute_regime_match_quality"]) for c in ref}
        assert len(monte_carlo) >= 3
        assert len(regime) >= 4


_VALUES = np.array([0.012, -0.004, 0.0031, 0.0, -0.0275, 0.019])
_STD_CASES = [
    ("std_zero", _VALUES, 0.001, 0.0),
    ("std_negative_zero", _VALUES, 0.001, -0.0),
    ("std_nan", _VALUES, 0.001, float("nan")),
    ("std_positive", _VALUES, 0.0007, 0.0123),
    ("values_with_nan", np.array([0.01, np.nan, -0.02, 0.005]), 0.001, 0.02),
    ("values_with_inf", np.array([0.01, np.inf, -0.02, -np.inf]), 0.001, 0.02),
    ("empty_std_positive", np.array([], dtype=float), 0.0, 0.5),
    ("empty_std_zero", np.array([], dtype=float), 0.0, 0.0),
    ("two_dimensional", np.array([[0.01, -0.02, 0.03], [0.0, 0.004, -0.001]]), 0.002, 0.015),
    ("two_dimensional_std_zero", np.array([[0.01, -0.02], [0.0, 0.004]]), 0.002, 0.0),
    ("integer_values_std_zero", np.array([1, 2, 3]), 2.0, 0.0),
]


class TestSharedZScoreEqualsTheReferenceClosure:
    @pytest.mark.parametrize(
        ("name", "values", "mean", "std"), _STD_CASES, ids=[c[0] for c in _STD_CASES]
    )
    def test_zscore_equals_the_reference_closure_elementwise_dtype_and_shape(
        self, name, values, mean, std
    ):
        with np.errstate(all="ignore"):
            want = _reference_z(values, mean, std)
            got = math_engine._zscore(values, mean, std)
        assert isinstance(got, np.ndarray)
        assert got.dtype == want.dtype
        assert got.shape == want.shape
        np.testing.assert_array_equal(got, want)  # NaN positions equal; exact otherwise

    def test_std_zero_and_negative_zero_give_float_zeros_of_the_input_shape(self):
        for std in (0.0, -0.0):
            got = math_engine._zscore(np.array([[1, 2], [3, 4]]), 2.5, std)
            assert got.dtype == np.dtype(float)
            assert got.shape == (2, 2)
            assert not got.any()

    def test_nan_std_propagates_nan_and_does_not_take_the_zero_branch(self):
        with np.errstate(all="ignore"):
            got = math_engine._zscore(_VALUES, 0.001, float("nan"))
        assert np.isnan(got).all()

    def test_input_array_is_not_modified(self):
        values = _VALUES.copy()
        math_engine._zscore(values, 0.001, 0.0123)
        np.testing.assert_array_equal(values, _VALUES)


def _module_tree() -> ast.Module:
    return ast.parse(Path(math_engine.__file__).read_text(encoding="utf-8"))


def _zscore_defs(tree: ast.Module) -> list[ast.FunctionDef]:
    """Every function with the z-score signature (values, mean, std), at any nesting depth."""
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and [a.arg for a in node.args.args] == ["values", "mean", "std"]
    ]


class TestExactlyOneZScoreDefinition:
    def test_exactly_one_zscore_definition_exists_and_it_is_module_level(self):
        tree = _module_tree()
        defs = _zscore_defs(tree)
        assert len(defs) == 1, f"{len(defs)} z-score definitions found"
        assert defs[0] in tree.body, "the single definition must be at module level"
        assert defs[0].name == "_zscore"

    def test_no_nested_function_named_z_remains(self):
        nested = [
            n.name
            for fn in ast.walk(_module_tree())
            if isinstance(fn, ast.FunctionDef)
            for n in ast.walk(fn)
            if isinstance(n, ast.FunctionDef) and n is not fn and n.name == "_z"
        ]
        assert nested == []

    @pytest.mark.parametrize("function", FAMILY_FUNCTIONS)
    def test_each_family_function_calls_the_shared_definition_for_both_features(self, function):
        tree = _module_tree()
        (fn,) = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function]
        calls = [
            n
            for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_zscore"
        ]
        assert len(calls) == 2
