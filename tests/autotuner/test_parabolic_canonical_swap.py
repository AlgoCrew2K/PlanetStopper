"""
RED test — task #23.

Asserts that autotuner.py's inline parabolic-arm block:

    velocity = ret - prev_return
    prev_return = ret
    para_threshold = p.get("PARABOLIC_VELOCITY_THRESHOLD", 2.0)
    if velocity >= para_threshold:
        para_armed = True

has been replaced by a call to the canonical helper
``math_engine.compute_para_arm_decision``. This is a static AST-only check
— it does NOT execute run_autotuner, synthetic_history, or any network/IO code.

Pre-swap (RED): autotuner.py contains the inline block above and never calls
``math_engine.compute_para_arm_decision``. The test below FAILS.

Post-swap (GREEN): the inline block is gone, the canonical helper is invoked
with all four required kwargs (current_return, prev_return, para_threshold,
currently_armed), and this test passes.
"""

from __future__ import annotations

import ast
import pathlib

# Required kwargs for the canonical helper (per cycle 3 / math_engine.py signature):
#   current_return, prev_return, para_threshold, currently_armed
REQUIRED_KWARGS = frozenset(
    {
        "current_return",
        "prev_return",
        "para_threshold",
        "currently_armed",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _autotuner_path() -> pathlib.Path:
    """Resolve the autotuner.py path from the repo root."""
    # tests/autotuner/<this_file>  ->  repo_root / autotuner.py
    return pathlib.Path(__file__).resolve().parents[2] / "autotuner.py"


def _parse_autotuner() -> ast.Module:
    src = _autotuner_path().read_text(encoding="utf-8")
    return ast.parse(src, filename=str(_autotuner_path()))


def _is_compute_para_arm_call(node: ast.AST) -> bool:
    """Return True iff ``node`` is a Call to math_engine.compute_para_arm_decision."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    # Dotted form: math_engine.compute_para_arm_decision(...)
    if (
        isinstance(func, ast.Attribute)
        and func.attr == "compute_para_arm_decision"
        and isinstance(func.value, ast.Name)
        and func.value.id == "math_engine"
    ):
        return True
    # Bare from-import form: compute_para_arm_decision(...)
    if isinstance(func, ast.Name) and func.id == "compute_para_arm_decision":
        return True
    return False


def _find_canonical_calls(tree: ast.Module) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if _is_compute_para_arm_call(n)]


# ---------------------------------------------------------------------------
# Test — math_engine.compute_para_arm_decision is called with all 4 required kwargs
# ---------------------------------------------------------------------------


def test_autotuner_calls_compute_para_arm_decision() -> None:
    """
    autotuner.py must contain at least one call to
    math_engine.compute_para_arm_decision with all four required kwargs:
    current_return, prev_return, para_threshold, currently_armed.

    This replaces the inline block at autotuner.py:163-167 (task #23).
    The inline block computes ``velocity = ret - prev_return`` and
    conditionally sets ``para_armed = True`` — behaviorally identical to
    the canonical helper's (velocity, should_arm) return contract.

    FAILS pre-swap (no canonical call exists).
    PASSES post-swap (inline removed, canonical wired in with all 4 kwargs).
    """
    tree = _parse_autotuner()
    calls = _find_canonical_calls(tree)

    assert len(calls) >= 1, (
        "Expected autotuner.py to call math_engine.compute_para_arm_decision "
        "(canonical parabolic-arm decision helper), but found zero such calls. "
        "The inline parabolic-arm block at lines ~163-167 must be replaced with "
        "a call to the canonical helper."
    )

    # Every call to the canonical helper must pass all 4 required kwargs.
    missing_by_call: list[tuple[int, set[str]]] = []
    for call in calls:
        kw_names = {kw.arg for kw in call.keywords if kw.arg is not None}
        missing = REQUIRED_KWARGS - kw_names
        if missing:
            missing_by_call.append((getattr(call, "lineno", -1), missing))

    assert not missing_by_call, (
        "Call(s) to math_engine.compute_para_arm_decision are missing required "
        "kwargs. All four kwargs must be passed by name:\n"
        + "\n".join(
            f"  - line {lineno}: missing {sorted(missing)}"
            for lineno, missing in missing_by_call
        )
        + f"\nFull required kwarg set: {sorted(REQUIRED_KWARGS)}."
    )
