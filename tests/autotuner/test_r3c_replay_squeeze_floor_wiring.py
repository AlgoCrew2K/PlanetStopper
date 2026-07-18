"""
R3-c / MA-11 AC-4 — replay wiring: autotuner's ``compute_active_trailing_stop``
call (inside ``_replay_exit_tick``) passes
``squeeze_floor=p.get("MAX_SQUEEZE_FLOOR", _replay_squeeze_floor_default())``,
where the default is sourced from the SAME module attribute production defaults
from (``alpha_bot_execution.MAX_SQUEEZE_FLOOR``) — the R1/F6 idiom, never a
replay-local literal. If the two sides drew defaults from different sources, a
key-absent legacy params dict would silently optimize a different exit rule
than production trades.

Two guards:
  * AST — the replay seam call passes a ``squeeze_floor=`` keyword sourced from
    ``p.get("MAX_SQUEEZE_FLOOR", ...)``.
  * RUNTIME — ``autotuner._replay_squeeze_floor_default()`` returns the live
    ``alpha_bot_execution.MAX_SQUEEZE_FLOOR`` (monkeypatch-proof: reads the attr
    at call time, not a frozen import-time copy).

RED at HEAD: no ``squeeze_floor`` keyword; no ``_replay_squeeze_floor_default``
helper.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import alpha_bot_execution
import autotuner

_SRC = pathlib.Path(autotuner.__file__)


def _seam_calls(tree: ast.AST) -> list[ast.Call]:
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


def test_replay_call_exists_exactly_once() -> None:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    calls = _seam_calls(tree)
    assert len(calls) == 1, (
        f"expected exactly one compute_active_trailing_stop call in autotuner.py, "
        f"found {len(calls)} (lines {[c.lineno for c in calls]})."
    )


def test_replay_passes_squeeze_floor_via_params_get() -> None:
    """AC-4: the replay seam call binds squeeze_floor to
    p.get("MAX_SQUEEZE_FLOOR", <default>). RED at HEAD: no such keyword.
    """
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    kw = _squeeze_floor_kw(_seam_calls(tree)[0])
    assert kw is not None, (
        "autotuner.py:~1246 compute_active_trailing_stop call must pass a "
        "`squeeze_floor=` keyword (AC-4 replay parity)."
    )
    val = kw.value
    assert isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute), (
        "squeeze_floor must be sourced from a p.get(...) call (per-symphony "
        f"resolution), got: {ast.dump(val)}"
    )
    assert val.func.attr == "get" and isinstance(val.func.value, ast.Name), (
        f"squeeze_floor must come from `<params>.get(...)`, got: {ast.dump(val.func)}"
    )
    assert val.args and isinstance(val.args[0], ast.Constant) and (
        val.args[0].value == "MAX_SQUEEZE_FLOOR"
    ), "the p.get first arg must be the literal 'MAX_SQUEEZE_FLOOR'."
    assert len(val.args) >= 2, (
        "p.get('MAX_SQUEEZE_FLOOR', <default>) must supply a default (the module "
        "attr), never a bare 1-arg get that returns None on a legacy params dict."
    )


def test_replay_squeeze_floor_default_is_the_production_module_attr() -> None:
    """AC-4 (R1/F6 idiom): the replay default IS the alpha_bot_execution module
    attribute production defaults from — not a replay-local mirror. Runtime,
    monkeypatch-proof (reads the attr live).

    RED at HEAD: autotuner._replay_squeeze_floor_default does not exist.
    """
    assert hasattr(autotuner, "_replay_squeeze_floor_default"), (
        "autotuner must expose _replay_squeeze_floor_default() sourcing the "
        "replay squeeze-floor default from alpha_bot_execution.MAX_SQUEEZE_FLOOR."
    )
    assert autotuner._replay_squeeze_floor_default() == alpha_bot_execution.MAX_SQUEEZE_FLOOR


def test_replay_squeeze_floor_default_tracks_the_module_attr_live(monkeypatch) -> None:
    """AC-4: the default must read the attr at call time, so an operator's
    MAX_SQUEEZE_FLOOR env override reaches the replay identically to production.
    A frozen import-time copy would drift.

    RED at HEAD: helper does not exist (AttributeError).
    """
    sentinel = 0.4242  # arbitrary, distinct from the 0.20 default
    monkeypatch.setattr(alpha_bot_execution, "MAX_SQUEEZE_FLOOR", sentinel, raising=False)
    assert autotuner._replay_squeeze_floor_default() == pytest.approx(sentinel), (
        "the replay default must reflect the LIVE alpha_bot_execution.MAX_SQUEEZE_FLOOR "
        "(read at call time), not a value frozen at import."
    )
