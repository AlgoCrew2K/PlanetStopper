"""
R3-c / MA-11 AC-3 — production wiring: alpha_bot_execution.py's
``compute_active_trailing_stop`` call passes ``squeeze_floor=acc_MAX_SQUEEZE_FLOOR``.

Why a source/AST assertion: the production exit computation lives deep inside
the minute-scheduler main loop (alpha_bot_execution.py:1467), which is not
unit-drivable without the full daemon. The AC-4 parity battery covers the
in-file production *reference* ⇄ replay, but only a source-level pin proves the
REAL production call site now READS the previously-dead ``acc_MAX_SQUEEZE_FLOOR``
(assigned at :1236, zero readers before this cycle). This closes the gap the
test-local reference cannot.

RED at HEAD: the call passes no ``squeeze_floor`` keyword -> this test fails.
GREEN once the keyword is wired to the ``acc_MAX_SQUEEZE_FLOOR`` Name.
"""

from __future__ import annotations

import ast
import pathlib

import alpha_bot_execution

_SRC = pathlib.Path(alpha_bot_execution.__file__)


def _seam_calls(tree: ast.AST) -> list[ast.Call]:
    """Every call to (something).compute_active_trailing_stop in the module."""
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "compute_active_trailing_stop":
                out.append(node)
    return out


def _squeeze_floor_kw(call: ast.Call) -> ast.keyword | None:
    for kw in call.keywords:
        if kw.arg == "squeeze_floor":
            return kw
    return None


def test_production_call_exists_exactly_once() -> None:
    """Sanity: the production exit path calls the seam exactly once, so the
    wiring assertion below is unambiguous."""
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    calls = _seam_calls(tree)
    assert len(calls) == 1, (
        f"expected exactly one compute_active_trailing_stop call in "
        f"alpha_bot_execution.py, found {len(calls)} (lines "
        f"{[c.lineno for c in calls]})."
    )


def test_production_passes_squeeze_floor_from_acc_param() -> None:
    """AC-3: the production seam call binds squeeze_floor to the per-symphony
    ``acc_MAX_SQUEEZE_FLOOR`` Name — the :1236 assignment's first-ever reader.

    RED at HEAD: no squeeze_floor keyword on the call.
    """
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    call = _seam_calls(tree)[0]
    kw = _squeeze_floor_kw(call)
    assert kw is not None, (
        "alpha_bot_execution.py:~1467 compute_active_trailing_stop call must "
        "pass a `squeeze_floor=` keyword (AC-3) — MAX_SQUEEZE_FLOOR is still a "
        "dead knob until this call reads acc_MAX_SQUEEZE_FLOOR."
    )
    assert isinstance(kw.value, ast.Name) and kw.value.id == "acc_MAX_SQUEEZE_FLOOR", (
        "squeeze_floor must be the per-symphony acc_MAX_SQUEEZE_FLOOR resolved at "
        "line ~1236 (acc_params.get('MAX_SQUEEZE_FLOOR', MAX_SQUEEZE_FLOOR)), not "
        f"a literal or a different name. Got: {ast.dump(kw.value)}"
    )
