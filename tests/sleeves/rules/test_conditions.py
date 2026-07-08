"""
RED tests — sleeves/rules/conditions.py: condition-tree evaluation (AC-4).

CONTRACT this file specifies for the GREEN implementer (s2-rules-impl):

    sleeves/rules/conditions.py

    @dataclass(frozen=True)
    class EvalResult:
        fireable: bool          # final AND-of-everything decision
        reason: str | None      # populated ONLY when an unavailable sense
                                  # short-circuited evaluation (fail-safe);
                                  # None when the tree legitimately evaluated
                                  # to False with complete data (that's just
                                  # "condition not met", not a reason to log).

    def evaluate_condition(node: dict, sensed: dict[str, "senses.SenseResult"]) -> EvalResult: ...

Node shapes (schema-validated upstream by schema.py; this module trusts shape):
    Leaf: {"op": "compare", "sense": <key>, "comparator": <op>, "value": <num|str|bool>}
    Bool: {"op": "AND" | "OR", "children": [<node>, ...]}
          {"op": "NOT", "child": <node>}

THE FAIL-SAFE RULE (pinned exactly, this is the load-bearing AC-4 contract):
    If ANY leaf anywhere in the whole tree references a sense-key whose
    SenseResult.available is False, the ENTIRE evaluate_condition call
    returns fireable=False, reason="sense_unavailable:<key>" for the FIRST
    such leaf encountered in a deterministic left-to-right, depth-first
    traversal (children visited in list order; NOT's single child visited
    like a one-element AND). This holds REGARDLESS of the boolean shape —
    an OR with one TRUE available branch and one unavailable branch is
    STILL not-fireable. Never "smart short-circuit" past an unavailable
    operand; the fail-safe rule is deliberately more conservative than
    Python's own `or`/`and` short-circuit semantics.

When every leaf's sense IS available: standard boolean evaluation applies
(AND = all children true, OR = any child true, NOT = negation of the single
child), and reason is always None (a legitimate False needs no "reason" —
there was nothing to fail-safe against).

Comparator semantics (values are whatever the sense returned; comparators
mirror Python's own operators):
    ">"  -> value_sensed >  value_target
    ">=" -> value_sensed >= value_target
    "<"  -> value_sensed <  value_target
    "<=" -> value_sensed <= value_target
    "==" -> value_sensed == value_target
    "!=" -> value_sensed != value_target
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytest.importorskip(
    "sleeves.rules.conditions", reason="RED phase — sleeves.rules.conditions not implemented yet"
)
pytest.importorskip(
    "sleeves.rules.senses", reason="RED phase — sleeves.rules.senses not implemented yet"
)

import sleeves.rules.conditions as conditions  # noqa: E402
import sleeves.rules.senses as senses  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _available(value):
    return senses.SenseResult(value=value, available=True, reason=None)


def _unavailable(reason="unavailable_for_test"):
    return senses.SenseResult(value=None, available=False, reason=reason)


def _leaf(sense: str, comparator: str, value) -> dict:
    return {"op": "compare", "sense": sense, "comparator": comparator, "value": value}


# ---------------------------------------------------------------------------
# 1. Leaf comparator semantics
# ---------------------------------------------------------------------------


class TestLeafComparators:
    @pytest.mark.parametrize(
        "comparator,sensed_value,target,expected",
        [
            (">", 105.0, 100.0, True),
            (">", 95.0, 100.0, False),
            (">=", 100.0, 100.0, True),
            ("<", 95.0, 100.0, True),
            ("<=", 100.0, 100.0, True),
            ("==", "SHADOW", "SHADOW", True),
            ("==", "SHADOW", "PAPER", False),
            ("!=", "SHADOW", "PAPER", True),
        ],
    )
    def test_comparator_semantics(self, comparator, sensed_value, target, expected):
        node = _leaf("sma_20", comparator, target)
        sensed = {"sma_20": _available(sensed_value)}
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is expected
        assert result.reason is None


# ---------------------------------------------------------------------------
# 2. AND/OR/NOT boolean combination (complete data — no unavailable operands)
# ---------------------------------------------------------------------------


class TestBooleanCombination:
    def test_and_is_true_only_when_all_children_true(self):
        sensed = {"a": _available(10.0), "b": _available(20.0)}
        node = {
            "op": "AND",
            "children": [_leaf("a", ">", 5.0), _leaf("b", ">", 15.0)],
        }
        assert conditions.evaluate_condition(node, sensed).fireable is True

    def test_and_is_false_when_one_child_false(self):
        sensed = {"a": _available(10.0), "b": _available(20.0)}
        node = {
            "op": "AND",
            "children": [_leaf("a", ">", 5.0), _leaf("b", ">", 25.0)],  # b's leaf is false
        }
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is False
        assert result.reason is None  # legitimate false, not a fail-safe reason

    def test_or_is_true_when_any_child_true(self):
        sensed = {"a": _available(10.0), "b": _available(20.0)}
        node = {"op": "OR", "children": [_leaf("a", ">", 999.0), _leaf("b", ">", 15.0)]}
        assert conditions.evaluate_condition(node, sensed).fireable is True

    def test_or_is_false_when_all_children_false(self):
        sensed = {"a": _available(10.0), "b": _available(20.0)}
        node = {"op": "OR", "children": [_leaf("a", ">", 999.0), _leaf("b", ">", 999.0)]}
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is False
        assert result.reason is None

    def test_not_negates_its_single_child(self):
        sensed = {"a": _available(10.0)}
        true_leaf = {"op": "NOT", "child": _leaf("a", ">", 999.0)}  # inner is False
        false_leaf = {"op": "NOT", "child": _leaf("a", ">", 5.0)}  # inner is True
        assert conditions.evaluate_condition(true_leaf, sensed).fireable is True
        assert conditions.evaluate_condition(false_leaf, sensed).fireable is False

    def test_arbitrary_nesting_and_or_not(self):
        # (a > 5 AND NOT(b > 999)) OR (c == "X")
        sensed = {"a": _available(10.0), "b": _available(1.0), "c": _available("Y")}
        node = {
            "op": "OR",
            "children": [
                {
                    "op": "AND",
                    "children": [
                        _leaf("a", ">", 5.0),
                        {"op": "NOT", "child": _leaf("b", ">", 999.0)},
                    ],
                },
                _leaf("c", "==", "X"),
            ],
        }
        # a>5 True, NOT(b>999) True -> AND True -> OR short-circuits true regardless of c
        assert conditions.evaluate_condition(node, sensed).fireable is True


# ---------------------------------------------------------------------------
# 3. THE fail-safe rule — unavailable operands never silently ignored
# ---------------------------------------------------------------------------


class TestFailSafeUnavailableOperand:
    def test_single_unavailable_leaf_is_not_fireable(self):
        node = _leaf("sma_20", ">", 100.0)
        sensed = {"sma_20": _unavailable("insufficient_history")}
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is False
        assert result.reason is not None
        assert "sma_20" in result.reason

    def test_and_with_one_unavailable_child_is_not_fireable_even_if_other_child_true(self):
        sensed = {"a": _available(10.0), "b": _unavailable()}
        node = {"op": "AND", "children": [_leaf("a", ">", 5.0), _leaf("b", ">", 1.0)]}
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is False
        assert result.reason is not None

    def test_or_with_one_available_true_branch_and_one_unavailable_branch_is_STILL_not_fireable(
        self,
    ):
        # THE critical fail-safe case: naive short-circuit `or` logic would
        # fire here because the first branch is true. Fail-safe forbids that.
        sensed = {"a": _available(10.0), "b": _unavailable()}
        node = {"op": "OR", "children": [_leaf("a", ">", 5.0), _leaf("b", ">", 1.0)]}
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is False, (
            "an OR branch being true must NEVER mask an unavailable sibling operand — "
            "this is the fail-safe rule's whole point (AC-4)."
        )
        assert result.reason is not None

    def test_not_wrapping_an_unavailable_leaf_is_not_fireable(self):
        sensed = {"a": _unavailable()}
        node = {"op": "NOT", "child": _leaf("a", ">", 1.0)}
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is False
        assert result.reason is not None

    def test_deeply_nested_unavailable_leaf_is_still_caught(self):
        sensed = {"a": _available(1.0), "b": _available(2.0), "c": _unavailable("stale")}
        node = {
            "op": "AND",
            "children": [
                _leaf("a", ">", 0.0),
                {
                    "op": "OR",
                    "children": [
                        _leaf("b", ">", 0.0),
                        {"op": "NOT", "child": _leaf("c", ">", 0.0)},
                    ],
                },
            ],
        }
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is False
        assert "c" in result.reason

    def test_leftmost_unavailable_leaf_reported_when_multiple_are_unavailable(self):
        # Distinctive multi-char key names (not "a"/"b") -- a single-letter
        # key risks colliding with an unrelated substring inside the reason
        # message's own fixed vocabulary (e.g. "b" is a substring of the word
        # "unavailable" itself, which would make a naive "'b' not in reason"
        # assertion fail for a reason that has nothing to do with the test).
        sensed = {
            "sense_alpha": _unavailable("reason_alpha"),
            "sense_beta": _unavailable("reason_beta"),
        }
        node = {
            "op": "AND",
            "children": [_leaf("sense_alpha", ">", 0.0), _leaf("sense_beta", ">", 0.0)],
        }
        result = conditions.evaluate_condition(node, sensed)
        assert result.fireable is False
        assert "sense_alpha" in result.reason
        assert "sense_beta" not in result.reason


# ---------------------------------------------------------------------------
# 4. Property-based invariant (hypothesis): fail-safe holds for ANY tree shape
# ---------------------------------------------------------------------------


def _flat_and_all(keys: list[str]) -> dict:
    leaves = [{"op": "compare", "sense": k, "comparator": ">", "value": 0.0} for k in keys]
    if len(leaves) == 1:
        return leaves[0]
    return {"op": "AND", "children": leaves}


def _tree_strategy(keys: list[str], depth: int):
    """Recursive AND/OR/NOT tree strategy over a fixed pool of leaf keys.

    Every key in `keys` is GUARANTEED to be referenced exactly once somewhere
    in every tree this strategy can produce — this is load-bearing for the
    invariant test below, which asserts "any unavailable key -> not fireable"
    over the FULL `availability_flags` list. A strategy shape that could drop
    a branch (e.g. NOT wrapping only one of two children, discarding the
    other) would silently produce trees that don't reference every key,
    invalidating that assertion for those draws. NOT here always wraps the
    FULL (both-children-inclusive) node, never a partial branch, and running
    out of recursion `depth` degrades to a flat AND over all remaining keys
    (never a bare single leaf that would drop the rest).
    """
    if len(keys) <= 1:
        return st.just({"op": "compare", "sense": keys[0], "comparator": ">", "value": 0.0})
    if depth <= 0:
        return st.just(_flat_and_all(keys))

    mid = len(keys) // 2
    left_keys, right_keys = keys[:mid], keys[mid:]
    left = _tree_strategy(left_keys, depth - 1)
    right = _tree_strategy(right_keys, depth - 1)
    base = st.one_of(
        st.builds(lambda lft, rgt: {"op": "AND", "children": [lft, rgt]}, left, right),
        st.builds(lambda lft, rgt: {"op": "OR", "children": [lft, rgt]}, left, right),
    )
    # NOT wraps the WHOLE combined node (both branches), never a single side —
    # see the docstring above for why that matters.
    return st.one_of(base, st.builds(lambda n: {"op": "NOT", "child": n}, base))


@given(
    availability_flags=st.lists(st.booleans(), min_size=2, max_size=5),
    data=st.data(),
)
@settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
def test_fail_safe_invariant_holds_for_any_tree_shape(availability_flags, data):
    """INVARIANT: fireable is False with a non-None reason iff at least one
    leaf's sense is unavailable; reason is always None when every leaf's
    sense is available (regardless of the tree's boolean shape or the
    legitimate true/false outcome)."""
    keys = [f"k{i}" for i in range(len(availability_flags))]
    sensed = {
        key: (_available(float(i + 1)) if flag else _unavailable(f"na_{key}"))
        for i, (key, flag) in enumerate(zip(keys, availability_flags))
    }
    # Draw the tree INSIDE the test body via st.data() (the correct hypothesis
    # idiom) rather than calling .example() on a strategy, which hypothesis
    # forbids from within a running @given test.
    tree = data.draw(_tree_strategy(keys, depth=3))
    result = conditions.evaluate_condition(tree, sensed)

    any_unavailable = not all(availability_flags)
    if any_unavailable:
        assert result.fireable is False
        assert result.reason is not None
    else:
        assert result.reason is None
