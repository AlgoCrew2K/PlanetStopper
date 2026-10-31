"""Reference rules for the Settings page's override counting, written from the rule
the page must follow, not from its code: a stored per-symphony value counts as an
override only when it differs from the global value; a key with no global counts when
the symphony stores a value for it; two values are equal when both read as the same
decimal number, else when they are the same text.
"""

from __future__ import annotations

import re

_DECIMAL = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)")


def finite_decimal(value):
    """The float for a full decimal literal (surrounding spaces allowed), else None."""
    text = str(value).strip()
    return float(text) if _DECIMAL.fullmatch(text) else None


def values_equal(stored, global_value) -> bool:
    a, b = finite_decimal(stored), finite_decimal(global_value)
    if a is not None and b is not None:
        return a == b
    return str(stored) == str(global_value)


def reference_override_count(params: dict, globals_: dict) -> int:
    count = 0
    for key, stored in params.items():
        global_value = globals_.get(key)
        if global_value is None or not values_equal(stored, global_value):
            count += 1
    return count


def overrides_label(count: int) -> str:
    return "1 override" if count == 1 else f"{count} overrides"
