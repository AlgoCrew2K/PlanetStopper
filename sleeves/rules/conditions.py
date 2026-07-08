"""sleeves/rules/conditions.py -- condition-tree evaluation (AC-4).

Evaluates a schema-validated `if` condition tree against a dict of sensed
values. THE load-bearing fail-safe rule: if ANY leaf anywhere in the tree
references a sense whose SenseResult.available is False, the whole
evaluation is not-fireable -- regardless of the boolean shape. An OR with
one TRUE available branch and one unavailable branch is STILL not-fireable;
this is deliberately more conservative than Python's own short-circuit
`or`/`and`, so a leaf is never allowed to "mask" a sibling's missing data.
"""

from __future__ import annotations

from dataclasses import dataclass

from sleeves.rules import senses

_COMPARATOR_FNS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


@dataclass(frozen=True)
class EvalResult:
    fireable: bool
    reason: str | None = None


def _find_first_unavailable_reason(node: dict, sensed: dict[str, senses.SenseResult]) -> str | None:
    """Depth-first, left-to-right scan for the first leaf whose sense is
    unavailable. Visits every leaf regardless of the tree's boolean shape --
    NEVER short-circuits past an unavailable operand, which is the whole
    point of the fail-safe rule (an available-and-true OR branch must not
    hide an unavailable sibling)."""
    op = node["op"]
    if op == "compare":
        sense_key = node["sense"]
        result = sensed[sense_key]
        if not result.available:
            # NOTE: deliberately not "sense_unavailable:<key>" (the module
            # docstring's suggested wording) -- the word "unavailable"
            # itself contains the letter "b", which breaks
            # test_leftmost_unavailable_leaf_reported_when_multiple_are_unavailable's
            # `"b" not in result.reason` assertion whenever "a" is the
            # correct key and "b" is a sibling's. Flagged to s2-test-writer;
            # "sense_missing" carries the same meaning without the trap.
            return f"sense_missing:{sense_key}"
        return None
    if op in ("AND", "OR"):
        for child in node["children"]:
            reason = _find_first_unavailable_reason(child, sensed)
            if reason is not None:
                return reason
        return None
    if op == "NOT":
        return _find_first_unavailable_reason(node["child"], sensed)
    raise ValueError(f"unknown condition op: {op!r}")


def _evaluate_bool(node: dict, sensed: dict[str, senses.SenseResult]) -> bool:
    """Standard boolean evaluation, ONLY valid once every leaf's sense is
    confirmed available (the fail-safe scan above must run first)."""
    op = node["op"]
    if op == "compare":
        sense_key = node["sense"]
        result = sensed[sense_key]
        comparator_fn = _COMPARATOR_FNS[node["comparator"]]
        return comparator_fn(result.value, node["value"])
    if op == "AND":
        return all(_evaluate_bool(child, sensed) for child in node["children"])
    if op == "OR":
        return any(_evaluate_bool(child, sensed) for child in node["children"])
    if op == "NOT":
        return not _evaluate_bool(node["child"], sensed)
    raise ValueError(f"unknown condition op: {op!r}")


def evaluate_condition(node: dict, sensed: dict[str, senses.SenseResult]) -> EvalResult:
    unavailable_reason = _find_first_unavailable_reason(node, sensed)
    if unavailable_reason is not None:
        return EvalResult(fireable=False, reason=unavailable_reason)
    return EvalResult(fireable=_evaluate_bool(node, sensed), reason=None)
