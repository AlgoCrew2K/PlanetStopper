"""sleeves/rules/schema.py -- JSON rule-doc validation (AC-4, AC-20).

Validates an operator-authored rule doc (`when`/`if`/`then`/`limits`) against
a size/depth/enum-closed schema and derives the rule's structural class
(DEFENSIVE vs ENTRY) from its `then` action set. No field here is ever
eval'd/exec'd -- every operator, sense, and action-type token is checked
against a closed enum; a string value (e.g. a rule name) is inert data.

Full contract pinned in tests/sleeves/rules/test_schema_validation.py's
module docstring.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

RULE_CLASS_DEFENSIVE = "DEFENSIVE"
RULE_CLASS_ENTRY = "ENTRY"

# AC-4 / plan Security Considerations: "rule/sleeve/envelope JSON strictly
# schema'd (size 32KB, depth 8, enum-closed)".
MAX_RULE_DOC_BYTES = 32768
MAX_CONDITION_DEPTH = 8

_COMPARATORS = frozenset({">", ">=", "<", "<=", "==", "!="})

# Exact-match sense keys (sleeve/portfolio/time state -- no numeric suffix).
_EXACT_SENSES = frozenset(
    {
        "time_of_day",
        "day_of_week",
        "sleeve_status",
        "sleeve_cash_usd",
        "sleeve_equity_usd",
        "position_qty",
    }
)
# Daily-bar indicator family: "<indicator>_<window>", e.g. "sma_20", "rsi_14".
_INDICATOR_SENSE_RE = re.compile(r"^(sma|ema|rsi|momentum|realized_vol|drawdown_from_high)_(\d+)$")
# Cached FRED series: "fred_<SERIES_ID>", e.g. "fred_VIXCLS".
_FRED_SENSE_RE = re.compile(r"^fred_[A-Z0-9_]+$")

_ACTION_TYPES = frozenset({"buy", "sell", "go_to_cash", "set_stop", "notify"})
_SIZING_MODES = frozenset({"risk_pct", "pct_of_sleeve", "dollars", "shares"})
_NOTIFY_FIELD_WHITELIST = frozenset(
    {"symbol", "action", "qty", "price", "reason", "rule_name", "sleeve_name"}
)

# AC-20: DEFENSIVE = every action type is reduce-only / non-position-opening.
_DEFENSIVE_ACTION_TYPES = frozenset({"sell", "go_to_cash", "set_stop", "notify"})


@dataclass(frozen=True)
class FieldError:
    field: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[FieldError, ...]
    rule_class: str | None


def _is_valid_sense_key(sense: object) -> bool:
    if not isinstance(sense, str):
        return False
    if sense in _EXACT_SENSES:
        return True
    if _INDICATOR_SENSE_RE.match(sense):
        return True
    return bool(_FRED_SENSE_RE.match(sense))


def _validate_condition(node: object, path: str) -> tuple[list[FieldError], int | None]:
    """Recursively validate a condition (sub)tree.

    Returns (errors, depth) where depth is the max leaf-to-root depth of this
    subtree (leaf = depth 1, each AND/OR/NOT ancestor adds 1) or None if the
    subtree is structurally invalid (depth is then meaningless).
    """
    if not isinstance(node, dict):
        return [FieldError(path, "condition node must be an object")], None

    op = node.get("op")

    if op == "compare":
        errors: list[FieldError] = []
        if not _is_valid_sense_key(node.get("sense")):
            errors.append(
                FieldError(
                    f"{path}.sense", f"unknown or malformed sense key: {node.get('sense')!r}"
                )
            )
        if node.get("comparator") not in _COMPARATORS:
            errors.append(
                FieldError(f"{path}.comparator", f"unknown comparator: {node.get('comparator')!r}")
            )
        if "value" not in node:
            errors.append(FieldError(f"{path}.value", "compare node missing 'value'"))
        return errors, (1 if not errors else None)

    if op in ("AND", "OR"):
        children = node.get("children")
        if not isinstance(children, list) or not children:
            return [
                FieldError(f"{path}.children", f"{op} requires a non-empty children list")
            ], None
        errors = []
        child_depths = []
        for i, child in enumerate(children):
            child_errors, child_depth = _validate_condition(child, f"{path}.children.{i}")
            errors.extend(child_errors)
            child_depths.append(child_depth)
        if errors or any(d is None for d in child_depths):
            return errors, None
        return errors, 1 + max(child_depths)

    if op == "NOT":
        child_errors, child_depth = _validate_condition(node.get("child"), f"{path}.child")
        if child_errors or child_depth is None:
            return child_errors, None
        return child_errors, 1 + child_depth

    return [FieldError(f"{path}.op", f"unknown condition op: {op!r}")], None


def _validate_action(action: object, path: str) -> list[FieldError]:
    if not isinstance(action, dict):
        return [FieldError(path, "action must be an object")]

    action_type = action.get("type")
    if action_type not in _ACTION_TYPES:
        return [FieldError(f"{path}.type", f"unknown action type: {action_type!r}")]

    errors: list[FieldError] = []
    if action_type in ("buy", "sell"):
        sizing = action.get("sizing")
        if not isinstance(sizing, dict) or sizing.get("mode") not in _SIZING_MODES:
            errors.append(
                FieldError(f"{path}.sizing.mode", "buy/sell requires a known sizing.mode")
            )
    elif action_type == "set_stop":
        has_trail_pct = "trail_percent" in action
        has_trail_price = "trail_price" in action
        if has_trail_pct == has_trail_price:  # both present, or neither
            errors.append(
                FieldError(path, "set_stop requires exactly one of trail_percent/trail_price")
            )
    elif action_type == "notify":
        fields = action.get("fields")
        if not isinstance(fields, dict):
            errors.append(FieldError(f"{path}.fields", "notify requires a 'fields' object"))
        else:
            unlisted = set(fields.keys()) - _NOTIFY_FIELD_WHITELIST
            if unlisted:
                errors.append(
                    FieldError(
                        f"{path}.fields", f"non-whitelisted notify field(s): {sorted(unlisted)}"
                    )
                )
    # go_to_cash: no extra fields required.
    return errors


def derive_rule_class(action_types: list[str]) -> str | None:
    """Structurally derives a rule's class from its `then` action-type list.

    DEFENSIVE = every action is reduce-only (sell/go_to_cash/set_stop/notify).
    ENTRY = the set contains "buy" and every OTHER action (if any) is "notify"
    (the one action type that's compatible with either class, since it's a
    passive side-effect rather than a position-changing action). Any other
    mix (e.g. buy+sell, buy+go_to_cash) -- or an empty action list -- derives
    no single valid class (AC-20: "a rule cannot claim DEFENSIVE while
    holding entry actions").
    """
    types = set(action_types)
    if not types:
        return None
    if "buy" in types:
        if types - {"buy", "notify"}:
            return None
        return RULE_CLASS_ENTRY
    if types <= _DEFENSIVE_ACTION_TYPES:
        return RULE_CLASS_DEFENSIVE
    return None


def validate_rule_doc(doc: dict) -> ValidationResult:
    errors: list[FieldError] = []

    doc_bytes = len(json.dumps(doc).encode("utf-8"))
    if doc_bytes > MAX_RULE_DOC_BYTES:
        errors.append(
            FieldError(
                "__doc__",
                f"rule doc size {doc_bytes} bytes exceeds the {MAX_RULE_DOC_BYTES}-byte cap",
            )
        )

    cond_errors, cond_depth = _validate_condition(doc.get("if"), "if")
    errors.extend(cond_errors)
    if cond_depth is not None and cond_depth > MAX_CONDITION_DEPTH:
        errors.append(
            FieldError(
                "if", f"condition tree depth {cond_depth} exceeds max depth {MAX_CONDITION_DEPTH}"
            )
        )

    then = doc.get("then")
    if not isinstance(then, list) or not then:
        errors.append(FieldError("then", "then must be a non-empty list of actions"))
        action_types: list[str] = []
    else:
        for i, action in enumerate(then):
            errors.extend(_validate_action(action, f"then.{i}"))
        action_types = [a.get("type") if isinstance(a, dict) else None for a in then]

    derived_class = derive_rule_class(action_types)
    if derived_class is None:
        errors.append(FieldError("then", "action set does not derive a single valid rule class"))

    declared_class = doc.get("class")
    if declared_class is not None:
        if declared_class not in (RULE_CLASS_DEFENSIVE, RULE_CLASS_ENTRY):
            errors.append(FieldError("class", f"unknown declared class: {declared_class!r}"))
        elif declared_class != derived_class:
            errors.append(
                FieldError("class", f"declared {declared_class!r} != derived {derived_class!r}")
            )

    valid = not errors
    return ValidationResult(
        valid=valid, errors=tuple(errors), rule_class=derived_class if valid else None
    )
