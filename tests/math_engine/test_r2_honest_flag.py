"""
R2 honest-flag tests for the VWAP System-A HWM arming gate.

Scope: the M3 R2 provenance-comment closure cited Leung-Zhang (2019) +
Peskir (1998) maximality principle, and added the sentence:

    "the regime-switch construction is a discretization of this boundary
     into inactive/active regions"

risk-m3's re-read flagged this sentence as honest-but-not-quite-theorem-
grade. Peskir (1998) characterizes the optimal trailing-stop boundary as
the MAXIMAL SOLUTION TO A NONLINEAR ODE. Discretizing that continuous
boundary into piecewise inactive/active regions is a reasonable
interpretive extension under NN1, but it is NOT a formally proven theorem
inside Peskir 1998. The M3 cycle closed R2 as "honest (not wrong),
remains the best available THEORY anchor under NN1" -- this cycle adds
the operator-visible caveat that surfaces the interpretive nature so a
future reader does not over-read the closure as a derivation.

Resolution chosen: PATH B (doc-only, source-comment caveat). Path A
(swap Peskir for Sortino-Price 1994) was rejected because it doubles
cycle scope and introduces a real behavioral change that the M3 cycle
explicitly rejected ("concentrating all M3 behavioral risk on R1").

What this cycle does NOT touch:
 - Runtime arithmetic in compute_vwap_breakdown_update (BYTE-IDENTICAL,
   preserved by M3; the existing test_r2_provenance_closure suite +
   the 24-fixture vwap_breakdown suite both continue to pass post-cycle).
 - spec_bundle facet `vwap_system_a_hwm_gate_v2` intended_direction (the
   interpretive nature is captured at the source-comment surface, not in
   facet metadata; spec_bundle tests remain unchanged).
 - Leung-Zhang / Peskir / maximality-principle / research-note / THEORY
   citations already present in the comment block (preserved verbatim;
   the caveat is additive).

What this cycle DOES require:
 - The R2 in-source comment block (around the VWAP System-A gate, where
   M3 added the Leung-Zhang + Peskir citation) MUST contain an
   operator-visible interpretive-nature caveat. The caveat MUST include
   the substrings 'interpretive extension' AND 'NOT a formally proven
   theorem in Peskir 1998'.

Per the cycle brief (r2-honest-flag), this test SHOULD FAIL in RED --
the post-M3 source comment cites Peskir without the interpretive caveat.
impl-r2flag closes it by ADDING (not replacing) the caveat sentence.
"""

from __future__ import annotations

import pathlib

import math_engine

# Path to the math_engine source on disk -- the tests read the source file
# directly to assert provenance-comment textual content. There is no
# runtime arithmetic change in this cycle so no behavioral test is added
# (the M3 byte-identical preservation surface in
# test_m3_r2_provenance_closure.py + the 24-fixture vwap_breakdown suite
# both continue to gate runtime preservation).
MATH_ENGINE_SRC_PATH = pathlib.Path(math_engine.__file__)


def _read_math_engine_source() -> str:
    return MATH_ENGINE_SRC_PATH.read_text(encoding="utf-8")


def _extract_r2_comment_region(source: str) -> str:
    """
    Return the source region containing the R2 provenance comment.

    Mirrors the anchor used in test_m3_r2_provenance_closure.py: the R2
    gate `safe_hwm >= vwap_cross_hwm_pct` appears THREE times in the file
    (docstring branch description, in-body provenance comment, runtime
    `if` line). The caveat must land in the in-body comment block
    immediately above the runtime gate.

    Anchor: the LAST occurrence of the gate pattern (the runtime `if`
    line), then extract the ~30 lines preceding it. That window captures
    the multi-paragraph provenance comment while excluding the docstring.
    """
    gate_idx = source.rfind("safe_hwm >= vwap_cross_hwm_pct")
    assert gate_idx != -1, (
        "Could not locate the R2 gate `safe_hwm >= vwap_cross_hwm_pct` in "
        "math_engine.py. Either compute_vwap_breakdown_update was refactored "
        "out of recognition or the gate condition itself was rewritten. "
        "If intentional, update this test's anchor."
    )
    pre_lines = source[:gate_idx].splitlines()
    # 30 lines is slightly wider than the closure-test window (25) so that
    # an additive caveat sentence placed near the top of the comment block
    # is still captured. The docstring sits well above this window.
    return "\n".join(pre_lines[-30:])


# --- R2 honest-flag: operator-visible interpretive-nature caveat ------------


def test_r2_comment_contains_interpretive_extension_caveat() -> None:
    """
    risk-m3 honest-flag closure: the R2 in-source comment must contain an
    operator-visible caveat that the regime-switch DISCRETIZATION of the
    Peskir 1998 continuous boundary is an INTERPRETIVE EXTENSION -- not
    a formally derived consequence of Peskir 1998.

    The exact phrase 'interpretive extension' is required so an operator
    grepping the source for the caveat finds it deterministically. A
    softer paraphrase ('a reasonable extension', 'a practitioner choice',
    etc.) does NOT satisfy this assertion -- the cycle brief mandated the
    specific substring.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "interpretive extension" in region, (
        "R2 source-comment block is missing the operator-visible caveat "
        "substring 'interpretive extension'. risk-m3 flagged the post-M3 "
        "Peskir-discretization sentence as honest-but-not-theorem-grade; "
        "the cycle r2-honest-flag closes that by adding a caveat sentence "
        "that uses the phrase 'interpretive extension'. Comment region "
        "scanned:\n"
        f"{region}"
    )


def test_r2_comment_explicitly_disclaims_peskir_1998_proof() -> None:
    """
    risk-m3 honest-flag closure -- second pillar. The caveat must also
    explicitly disclaim that the discretization is a proven theorem
    INSIDE Peskir 1998 itself, naming the paper by name so a reader who
    fetches Peskir 1998 from a citation index understands what is and is
    not in that paper.

    The required substring is 'NOT a formally proven theorem in Peskir
    1998' (case-sensitive on 'NOT' so the negation reads loudly in the
    source). The cycle brief mandated this exact phrasing because a
    softer wording ('not directly proven', 'not explicit in Peskir')
    invites future reviewers to repeat risk-m3's flag.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "NOT a formally proven theorem in Peskir 1998" in region, (
        "R2 source-comment block is missing the explicit Peskir-1998 "
        "disclaimer substring 'NOT a formally proven theorem in Peskir "
        "1998'. The cycle brief r2-honest-flag mandated this exact "
        "phrasing so the disclaimer reads loudly to a future reader and "
        "the honest-flag does not need to be re-raised. Comment region "
        "scanned:\n"
        f"{region}"
    )


# --- Preservation: M3-cycle citations remain in place ----------------------


def test_r2_comment_still_cites_leung_zhang_after_caveat() -> None:
    """
    Preservation guard. The honest-flag closure ADDS a caveat; it must
    NOT replace or strip the M3-cycle citation of Leung & Zhang (2019).
    If a future contributor mistakes Path B for Path A and rewrites the
    comment to swap derivations, this catches it.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    for required in ("Leung", "Zhang", "2019"):
        assert required in region, (
            f"R2 source-comment block is missing the M3-cycle Leung-Zhang "
            f"(2019) citation token {required!r}. The honest-flag closure "
            f"is doc-only and additive -- the M3 citations must remain. "
            f"Comment region scanned:\n{region}"
        )


def test_r2_comment_still_cites_peskir_maximality_principle_after_caveat() -> None:
    """
    Preservation guard -- second pillar. Peskir (1998) + the
    'maximality principle' phrase must remain in the comment. The
    honest-flag caveat clarifies the INTERPRETIVE relationship between
    Peskir's continuous boundary and the runtime gate; it does not
    revoke the citation.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "Peskir" in region, (
        "R2 source-comment block is missing the Peskir citation after the "
        "honest-flag closure. The caveat clarifies the interpretive nature "
        "of the discretization; the citation itself must remain so the "
        "comment retains its anchor. Comment region scanned:\n"
        f"{region}"
    )
    assert "maximality principle" in region.lower(), (
        "R2 source-comment block is missing the 'maximality principle' "
        "phrase after the honest-flag closure. The caveat is additive; "
        "the M3 framing must remain. Comment region scanned:\n"
        f"{region}"
    )


def test_r2_comment_still_declares_freeze_discipline_theory_after_caveat() -> None:
    """
    Preservation guard -- NN1 binding. The freeze_discipline = THEORY
    declaration must remain after the honest-flag closure. The whole
    point of the closure is that the gate REMAINS the best available
    THEORY anchor under NN1 -- if a contributor accidentally downgrades
    the declaration to BACKTEST_SELECTION or removes it entirely, this
    catches it.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "THEORY" in region, (
        "R2 source-comment block is missing the freeze_discipline = THEORY "
        "declaration after the honest-flag closure. The closure is "
        "interpretive caveat only -- the NN1 binding must remain. Comment "
        "region scanned:\n"
        f"{region}"
    )


def test_r2_comment_still_references_m3_research_note_after_caveat() -> None:
    """
    Preservation guard -- research-note linkage. The literature-pass
    research note reference must remain so a future reader can trace
    the full derivation. The honest-flag caveat is layered on top of
    the M3 closure, not in place of it.
    """
    source = _read_math_engine_source()
    region = _extract_r2_comment_region(source)

    assert "docs/research/m3-provenance/literature-pass.md" in region, (
        "R2 source-comment block is missing the M3 research-note reference "
        "after the honest-flag closure. The caveat is additive; the M3 "
        "linkage must remain. Comment region scanned:\n"
        f"{region}"
    )
