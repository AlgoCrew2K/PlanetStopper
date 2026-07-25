"""
RED -- incumbent-score friction tripwire (exit-friction-realized-savings,
scoped in by ga2-opt's PM-approved review plan, found missing in GREEN
review and routed back as a Red/Green/Revise gap per the Toxic Pair
protocol).

CONTEXT: autotuner.py's PHASE-3 acceptance gate
(_acceptance_gate.evaluate_acceptance_gate, ~autotuner.py:3049) currently
passes `incumbent_stability_score=1.0` and `incumbent_prior_anchor_score=1.0`
as hardcoded placeholders (a pre-existing pattern, untouched by this cycle's
diff -- verified via `git diff <fork-point> HEAD -- autotuner.py`, which
shows only the four friction-accounting sites, nothing near line 3062).

THE FORWARD-LOOKING RISK this tripwire guards against: today, with both
sides hardcoded to 1.0, there is no friction-mismatch bug -- a constant
compared to a constant cannot disagree on cost basis. But a FUTURE cycle
that wires real incumbent-performance data from the DB into these two
fields would be comparing a HISTORICAL score (computed by whatever replay
code existed when that history was recorded -- possibly gross-of-friction,
pre-dating SIM_EXIT_FRICTION_PCT) against a CANDIDATE score computed by
TODAY's friction-aware replay. Nothing currently stops that mismatched
comparison from shipping silently.

THIS FILE pins two things:
  1. The placeholders are STILL literal 1.0 constants today (a standing
     regression guard -- legitimately passes now and must keep passing
     until a deliberate, friction-aware wiring change lands. The moment
     someone replaces the literal with a variable/DB read, this test fails
     LOUDLY, forcing them to visit this file's rationale before shipping).
  2. A source comment exists at the call site referencing
     SIM_EXIT_FRICTION_PCT and the friction-mismatch rationale -- so a
     future implementer sees the warning inline, not just in a test file
     they may never open. This is GENUINELY RED today (no such comment
     exists yet) -- implementer's job to add the comment, not the
     test-writer's (see AGENT.md: never write production code).
"""

from __future__ import annotations

import ast
import pathlib

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


def _find_acceptance_gate_call() -> ast.Call:
    tree = ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "evaluate_acceptance_gate"
    ]
    assert len(calls) == 1, (
        f"Expected exactly one evaluate_acceptance_gate(...) call site in "
        f"autotuner.py, found {len(calls)}. This test's line-anchoring logic "
        f"assumes a single call site -- update if a second one is ever added."
    )
    return calls[0]


def _keyword_value(call: ast.Call, name: str) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    return None


def test_incumbent_scores_still_literal_placeholders_at_call_site():
    """Standing regression guard: incumbent_stability_score and
    incumbent_prior_anchor_score must still be literal 1.0 constants at the
    evaluate_acceptance_gate call site.

    This is deliberately a TRIPWIRE, not a design endorsement -- it exists so
    that wiring real incumbent-performance data into these fields is a
    conscious, visible act (this test breaking) rather than a silent drift.
    See module docstring for the friction-mismatch rationale a future
    implementer must address before this test may legitimately change.
    """
    call = _find_acceptance_gate_call()

    for field_name in ("incumbent_stability_score", "incumbent_prior_anchor_score"):
        value_node = _keyword_value(call, field_name)
        assert value_node is not None, (
            f"evaluate_acceptance_gate call site no longer passes "
            f"{field_name!r} at all -- the gate's call signature changed. "
            f"Re-evaluate this tripwire against the new signature."
        )
        assert isinstance(value_node, ast.Constant) and value_node.value == 1.0, (
            f"{field_name} is no longer the literal 1.0 placeholder (found "
            f"{ast.dump(value_node)}). This is the exact forward-looking "
            f"change this tripwire exists to catch: before wiring real "
            f"incumbent-performance data here, confirm it is computed on a "
            f"friction-consistent basis with the candidate score (see "
            f"SIM_EXIT_FRICTION_PCT, autotuner.py) -- a historical score "
            f"computed by pre-friction replay code compared against a "
            f"friction-aware candidate score is a mismatched comparison."
        )


def test_incumbent_placeholder_call_site_has_friction_tripwire_comment():
    """A source comment near the evaluate_acceptance_gate call site must
    reference SIM_EXIT_FRICTION_PCT and explain the friction-mismatch risk of
    wiring real incumbent data into the placeholder fields -- so a future
    implementer hits this warning inline, not only in this test file.

    Fails RED today: no such comment exists yet (this cycle's diff never
    touched this call site). Implementer's job to add the comment -- the
    test-writer does not edit production files.
    """
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")
    lines = src.splitlines()

    call = _find_acceptance_gate_call()
    call_lineno = call.lineno  # 1-indexed

    # Search a generous window of comment lines immediately preceding the
    # call (the existing placeholder-rationale comment block sits a handful
    # of lines above the call in the current source; a wide window tolerates
    # future reformatting without becoming a false negative).
    window_start = max(0, call_lineno - 25)
    window = "\n".join(lines[window_start : call_lineno - 1])

    assert "SIM_EXIT_FRICTION_PCT" in window, (
        "No comment referencing SIM_EXIT_FRICTION_PCT found near the "
        "evaluate_acceptance_gate call site. A future implementer wiring "
        "real incumbent_stability_score/incumbent_prior_anchor_score data "
        "into this call needs an inline warning about the friction-basis "
        "mismatch risk (see this test file's module docstring), not just a "
        "test they may never open."
    )
    assert "incumbent" in window.lower(), (
        "The SIM_EXIT_FRICTION_PCT reference near the call site must "
        "specifically address the INCUMBENT scores (the forward-looking "
        "risk), not just mention the constant in passing."
    )
