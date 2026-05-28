"""
M3 R2 provenance-closure tests for the VWAP System-A HWM arming gate.

Scope: the M3 re-derivation of R2 (the `safe_hwm >= vwap_cross_hwm_pct` gate
inside math_engine.compute_vwap_breakdown_update) under research-m3's
recommended **provenance-only closure**. Per literature-pass.md §2.3, the
runtime arithmetic is BYTE-IDENTICAL to the pre-M3 code; only the in-source
provenance comment changes (and a corresponding spec_bundle facet -- see
test_m3_spec_bundle_provenance.py).

The closure re-frames the existing scalar gate as the regime boundary of a
two-regime trailing-stop system under the Leung-Zhang (2019) optimal-stopping
formalism / Peskir (1998) maximality principle. Below the gate, the system is
in regime 1 (primary trailing-stop only). Above the gate, regime 2 (primary
stop OR VWAP System-A profit-protection). The scalar gate is the regime
boundary; the threshold value `vwap_cross_hwm_pct` remains an Optuna-searched
parameter inside the BHY haircut surface (no NN1 disturbance).

Why this is the right closure: the original provenance gap was the *gate
shape* (why a scalar threshold and not a continuous curve), not the tuned
*value*. The maximality-principle regime-switch construction is a published
optimal-stopping framing that justifies the shape. The behavioral test
surface is the pre-existing 24-fixture suite in
tests/fixtures/math_engine/vwap_breakdown/*.json -- those fixtures MUST pass
byte-identical post-M3 (verified by the existing
test_vwap_breakdown.py::test_vwap_breakdown_update_matches_derived_expected).
This file adds the TEXTUAL provenance-closure assertion: the in-source
comment cites Leung-Zhang and Peskir, references the research note, and
removes the pre-M3 self-flag "no formal literature provenance" language.

References:
- Leung, T. & Zhang, H. (2019). "Optimal Trading with a Trailing Stop."
  Applied Mathematics & Optimization 80, 669-698.
  DOI: 10.1007/s00245-019-09559-0.
- Peskir, G. (1998). "Optimal Stopping of the Maximum Process: The Maximality
  Principle." Annals of Probability 26(4), 1614-1640.
- Research note: docs/research/m3-provenance/literature-pass.md §2.

Per the M3 plan §72 + team-lead's authorization (cycle fix-m3-provenance),
this test SHOULD FAIL in RED -- the pre-M3 source comment still contains the
practitioner-heuristic flag. impl-m3 closes it by updating the comment block.
"""

from __future__ import annotations

import pathlib

import pytest

import math_engine


# Path to the math_engine source on disk -- the tests read the source file
# directly to assert provenance-comment textual content. Behavior tests live
# in tests/math_engine/test_vwap_breakdown.py and pass byte-identical
# post-M3 by design (provenance-only closure).
MATH_ENGINE_SRC_PATH = pathlib.Path(math_engine.__file__)


def _read_math_engine_source() -> str:
    return MATH_ENGINE_SRC_PATH.read_text(encoding="utf-8")


def _extract_r2_comment_region(source: str) -> str:
    """
    Return the source region containing the R2 provenance comment.

    The R2 gate is `safe_hwm >= vwap_cross_hwm_pct` inside
    compute_vwap_breakdown_update. The pattern appears THREE times in the
    file: (1) inside the function's docstring describing Branch 3, (2) in
    the in-body provenance comment above the actual gate, (3) on the
    runtime `if` line itself. The provenance closure must land in
    occurrence (2) -- the comment block IMMEDIATELY above the runtime gate.

    Anchor: the LAST occurrence (the `if` statement), then extract the ~30
    lines preceding it. That window captures the in-body comment block
    while excluding the docstring (which mentions the pattern textually
    for branch documentation).
    """
    # Find the LAST occurrence -- the runtime `if` statement. The earlier
    # occurrences are in the docstring (branch description) and in the
    # provenance comment; we want the window ending at the gate's `if` so
    # that both the comment AND the runtime line are inside the region,
    # but the docstring is NOT.
    gate_idx = source.rfind("safe_hwm >= vwap_cross_hwm_pct")
    assert gate_idx != -1, (
        "Could not locate the R2 gate `safe_hwm >= vwap_cross_hwm_pct` in "
        "math_engine.py. Either compute_vwap_breakdown_update was refactored "
        "out of recognition or the gate condition itself was rewritten. "
        "If intentional, update this test's anchor."
    )
    # Pull the 25 lines preceding the runtime gate -- enough to capture a
    # multi-paragraph provenance comment without reaching back into the
    # docstring (which sits well above the in-body comment).
    pre_lines = source[:gate_idx].splitlines()
    return "\n".join(pre_lines[-25:])


# --- R2 provenance-comment closure ------------------------------------------


def test_r2_provenance_comment_cites_leung_zhang_2019() -> None:
    """
    M3 provenance-comment closure for R2 (the VWAP System-A HWM gate).
    The in-source comment block ABOVE the gate must cite Leung-Zhang (2019)
    for the optimal-stopping formalism that justifies the regime-switch
    construction.

    This is the load-bearing positive assertion: the closure cites a real
    Tier-1 peer-reviewed paper, not a vague "the maximality principle"
    handwave.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "Leung" in region, (
        "R2 provenance comment must cite Leung (2019) for the optimal-"
        "stopping trailing-stop formalism. Comment region scanned:\n"
        f"{region}"
    )
    assert "Zhang" in region, (
        "R2 provenance comment must cite Zhang (the second author of "
        "Leung & Zhang 2019). Comment region scanned:\n"
        f"{region}"
    )
    assert "2019" in region, (
        "R2 provenance comment must cite the 2019 publication year of "
        "Leung & Zhang. Comment region scanned:\n"
        f"{region}"
    )


def test_r2_provenance_comment_cites_peskir_maximality_principle() -> None:
    """
    M3 provenance-comment closure for R2 -- second pillar.
    The comment must cite Peskir (1998) and the 'maximality principle'
    explicitly. This is the foundational result that characterizes the
    optimal trailing-stop boundary as the maximal solution to a first-order
    nonlinear ODE; the regime-switch construction is a discretization of
    that boundary.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "Peskir" in region, (
        "R2 provenance comment must cite Peskir (1998) for the maximality "
        "principle. Comment region scanned:\n"
        f"{region}"
    )
    assert "maximality principle" in region.lower(), (
        "R2 provenance comment must reference the 'maximality principle' "
        "by name. Comment region scanned:\n"
        f"{region}"
    )


def test_r2_provenance_comment_references_research_note() -> None:
    """
    M3 provenance-comment closure for R2 -- research-note linkage.
    The comment must reference the literature-pass research note so a future
    reader can trace the closure back to the full derivation.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "docs/research/m3-provenance/literature-pass.md" in region, (
        "R2 provenance comment must reference the research note at "
        "docs/research/m3-provenance/literature-pass.md. Comment region "
        "scanned:\n"
        f"{region}"
    )


def test_r2_provenance_comment_declares_freeze_discipline_theory() -> None:
    """
    M3 provenance-comment closure for R2 -- NN1 binding.
    The comment must declare `freeze_discipline = THEORY` to signal the
    NN1 spec-freeze category. THEORY is the right category because the
    regime-switch structure is justified from optimal-stopping theory; the
    threshold VALUE remains an Optuna-searched parameter inside the BHY
    haircut surface (no NN1 disturbance).

    Per literature-pass.md §2.3.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "THEORY" in region, (
        "R2 provenance comment must declare freeze_discipline = THEORY "
        "(NN1 binding). Comment region scanned:\n"
        f"{region}"
    )


def test_r2_provenance_comment_removes_pre_m3_self_flag() -> None:
    """
    M3 provenance-comment closure for R2 -- negative assertion.
    The pre-M3 self-flag 'no formal literature provenance' (or equivalents)
    must be REMOVED. The whole point of the closure is to replace the
    practitioner-flag with the published-derivation citation; leaving the
    self-flag in place defeats the cycle.

    This test catches a half-done closure where impl-m3 added the new
    citations but forgot to remove the old self-flag (a real failure mode
    when editing long comment blocks).
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "no formal literature provenance" not in region, (
        "R2 provenance comment still contains the pre-M3 self-flag "
        "'no formal literature provenance' -- M3 closure requires this "
        "string be REMOVED. The whole point of the M3 redrive is to "
        "replace the practitioner-flag with the derivation citation. "
        "Comment region scanned:\n"
        f"{region}"
    )


# --- R2 behavioral preservation (byte-identical runtime) --------------------


def test_r2_gate_behavior_unchanged_under_provenance_only_closure() -> None:
    """
    M3 R2 is a provenance-only closure: the runtime arithmetic is
    BYTE-IDENTICAL to the pre-M3 implementation. The behavioral test
    surface is the pre-existing 24-fixture suite in
    tests/fixtures/math_engine/vwap_breakdown/*.json -- those fixtures
    must continue to pass post-M3.

    This test is a smoke-level boundary cross-check: at the gate equality
    boundary `safe_hwm == vwap_cross_hwm_pct`, the gate ARMS (>=, not >).
    This is the pre-M3 behavior preserved verbatim. If impl-m3 accidentally
    changes the gate from >= to > (or any other behavioral tweak under
    the provenance-only banner), this catches it.

    The full byte-identical preservation surface is the 24-fixture suite;
    this is the canary.
    """
    # Inputs designed so safe_hwm == vwap_cross_hwm_pct (gate boundary)
    # and current_return < safe_hwm (so the inner profit-protection
    # condition fires).
    result = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=1.0,    # > VWAP_WEIGHT_THRESHOLD (0.5)
        weighted_vwap_diff=-1.0,  # < 0
        safe_hwm=1.0,
        current_return=0.5,
        vwap_cross_hwm_pct=1.0,   # safe_hwm == vwap_cross_hwm_pct (gate ==)
        vwap_bleed_arm_pct=-99.0, # System B fails (current_return > arm)
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=0,
        current_vwap_bleed_ticks=0,
    )
    new_vwap_ticks, new_vwap_bleed_ticks, is_vwap_broken, is_vwap_bleed_broken = result

    # At gate equality, System A arms (>=, not >). new_vwap_ticks goes 0 -> 1;
    # is_vwap_broken stays False (needs 3 ticks to break).
    assert new_vwap_ticks == 1, (
        f"R2 gate at boundary (safe_hwm == vwap_cross_hwm_pct) must ARM "
        f"System A (>= semantics). Expected new_vwap_ticks=1, got "
        f"{new_vwap_ticks}. If impl-m3 changed the gate to strict > under "
        f"the provenance-only banner, that violates the byte-identical "
        f"runtime contract."
    )
    assert is_vwap_broken is False, (
        f"At one tick, is_vwap_broken must still be False (3-tick confirm). "
        f"Got {is_vwap_broken}."
    )
    assert new_vwap_bleed_ticks == 0, (
        f"System B fails when current_return > vwap_bleed_arm_pct; ticks "
        f"reset to 0. Got {new_vwap_bleed_ticks}."
    )
    assert is_vwap_bleed_broken is False, (
        f"System B not broken when it didn't even arm. Got "
        f"{is_vwap_bleed_broken}."
    )


@pytest.mark.parametrize(
    "safe_hwm,vwap_cross_hwm_pct,gate_should_pass",
    [
        # Below gate -> System A inactive (regime 1).
        (0.5, 1.0, False),
        (0.999, 1.0, False),
        # At gate -> System A arms (>= semantics; regime 2 boundary).
        (1.0, 1.0, True),
        # Above gate -> System A arms (regime 2).
        (1.5, 1.0, True),
        (2.0, 1.0, True),
    ],
)
def test_r2_regime_switch_at_gate_boundary(
    safe_hwm: float, vwap_cross_hwm_pct: float, gate_should_pass: bool
) -> None:
    """
    M3 R2 regime-switch semantics: below the gate (safe_hwm < threshold) the
    System-A profit-protection branch is INACTIVE (regime 1, primary stop
    only); at or above the gate it is ACTIVE (regime 2). The regime
    boundary is the >= comparison.

    Behavior under the provenance-only closure is byte-identical to pre-M3.
    The test pins the regime boundary at multiple distances from the gate
    to catch a regression where impl-m3 either (a) changes the comparison
    operator, or (b) introduces a buffer/deadband around the boundary
    that the provenance-only closure should not permit.

    current_return is set below safe_hwm so the inner (return < hwm)
    condition is satisfied -- the gate's arm/no-arm decision is then the
    only thing that matters for new_vwap_ticks.
    """
    current_return = safe_hwm - 0.1  # always below HWM -> inner cond passes
    result = math_engine.compute_vwap_breakdown_update(
        is_triggered=False,
        valid_vwap_weight=1.0,
        weighted_vwap_diff=-1.0,
        safe_hwm=safe_hwm,
        current_return=current_return,
        vwap_cross_hwm_pct=vwap_cross_hwm_pct,
        vwap_bleed_arm_pct=-99.0,
        vwap_bleed_ticks_threshold=3,
        current_vwap_ticks=0,
        current_vwap_bleed_ticks=0,
    )
    new_vwap_ticks = result[0]

    if gate_should_pass:
        assert new_vwap_ticks == 1, (
            f"safe_hwm={safe_hwm}, vwap_cross_hwm_pct={vwap_cross_hwm_pct}: "
            f"gate should ARM System A (regime 2), expected new_vwap_ticks=1, "
            f"got {new_vwap_ticks}."
        )
    else:
        assert new_vwap_ticks == 0, (
            f"safe_hwm={safe_hwm}, vwap_cross_hwm_pct={vwap_cross_hwm_pct}: "
            f"gate should NOT arm System A (regime 1), expected "
            f"new_vwap_ticks=0, got {new_vwap_ticks}."
        )
