"""
Cluster 4 — AC-9 (audit M-7): EMBARGO_DAYS is sized to — or documented against —
the empirically estimated serial-dependence horizon of the per-day guard-alpha
series. If that horizon is <=1 day the current value is vindicated with a
sourced comment.

RED tests, written before the implementation.

Audit M-7: `EMBARGO_DAYS = 1` (autotuner.py) is roughly consistent with López de
Prado's ~1% lower-bound guidance and is therefore defensible — it is NOT a wrong
value. The defect is that 1 is a FLOOR, not a CONSIDERED value: the current
source comment reads "Default: 1 trading day" with no reference to the
autocorrelation horizon the embargo exists to suppress.

AC-9 remediation (per the Cluster 4 plan): size EMBARGO_DAYS to the empirically
estimated serial-dependence horizon of the per-day guard-alpha series, OR — if
that horizon is <=1 day — keep the value and VINDICATE it with a sourced comment
that names the horizon analysis. Either way the constant must stop being an
unexamined floor.

These tests assert the DOCUMENTATION contract: the EMBARGO_DAYS comment block
must reference the serial-dependence / autocorrelation horizon and tie the chosen
value to it. They do NOT pin the numeric value of EMBARGO_DAYS — the value is a
methodology output owned by risk-engine-specialist; a numeric pin would hardcode
a producer-chosen number (feedback_no_hardcoded_test_values).

Reference: López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4
(Embargo).
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


def _import_autotuner():
    import autotuner

    return autotuner


def _embargo_comment_block() -> str:
    """Return the source text around the EMBARGO_DAYS assignment.

    Captures the assignment line plus the contiguous run of comment lines
    immediately above it (the rationale block) and the two lines below.
    """
    lines = _AUTOTUNER_SRC.read_text(encoding="utf-8").splitlines()
    assign_idx = next(
        (i for i, ln in enumerate(lines) if re.match(r"\s*EMBARGO_DAYS\s*=", ln)),
        None,
    )
    assert assign_idx is not None, "EMBARGO_DAYS assignment not found in autotuner.py."

    # Walk upward over the contiguous comment block.
    start = assign_idx
    while start > 0 and lines[start - 1].strip().startswith("#"):
        start -= 1

    end = min(len(lines), assign_idx + 3)
    return "\n".join(lines[start:end])


# ===========================================================================
# AC-9 — EMBARGO_DAYS exists and is an integer
# ===========================================================================


def test_embargo_days_is_a_positive_integer():
    """
    AC-9 baseline: EMBARGO_DAYS must remain a positive integer trading-day count.

    AC-9 may resize it (e.g. to 2-5 days if the horizon analysis warrants) or keep
    it; either way it must stay a positive int. This test does NOT pin the value.
    """
    autotuner = _import_autotuner()

    assert hasattr(autotuner, "EMBARGO_DAYS"), "autotuner.py must define EMBARGO_DAYS."
    value = autotuner.EMBARGO_DAYS
    assert isinstance(value, int) and not isinstance(value, bool), (
        f"EMBARGO_DAYS must be an int trading-day count; got {type(value).__name__} ({value!r})."
    )
    assert value >= 1, (
        f"EMBARGO_DAYS must be >= 1 trading day; got {value!r}. "
        f"An embargo of 0 provides no autocorrelation-leakage protection."
    )


# ===========================================================================
# AC-9 — the comment ties the value to the serial-dependence horizon
# ===========================================================================


def test_embargo_comment_ties_value_to_an_estimated_horizon():
    """
    AC-9: the EMBARGO_DAYS rationale comment must tie the chosen value to an
    ESTIMATED / MEASURED serial-dependence horizon of the per-day guard-alpha
    series — not merely mention the concept of autocorrelation in passing.

    The pre-AC-9 comment already says "autocorrelation leakage from serial
    dependence" generically; that generic mention is exactly the unexamined-floor
    state M-7 flags. AC-9 requires evidence the horizon was actually ESTIMATED and
    the value JUSTIFIED against that estimate.

    Two-part heuristic — the comment block must contain BOTH:
      (1) a horizon CONCEPT term (serial dependence / autocorrelation / ACF), AND
      (2) an ESTIMATION/JUSTIFICATION term proving the horizon was examined and
          the value tied to it (measured / estimated / ACF decay / horizon of N
          days / vindicat...).

    A bare generic mention of "autocorrelation" without (2) fails — that is the
    pre-AC-9 state.
    """
    block = _embargo_comment_block()
    block_lc = block.lower()

    concept_terms = [
        "serial dependence",
        "serial-dependence",
        "autocorrelation",
        "acf",
        "serial correlation",
    ]
    estimation_terms = [
        "measured",
        "estimated",
        "estimate",
        "empirical",
        "empirically",
        "vindicat",  # "the 1-day embargo is vindicated"
        "horizon of",  # "...horizon of 2 days"
        "decays",
        "decay into",
        "lag at which",
        "sized to",
        "sized against",
    ]

    has_concept = any(t in block_lc for t in concept_terms)
    has_estimation = any(t in block_lc for t in estimation_terms)

    assert has_concept and has_estimation, (
        "The EMBARGO_DAYS comment does not tie the value to an ESTIMATED "
        "serial-dependence horizon. AC-9 (audit M-7) requires the comment to "
        "(1) name the autocorrelation horizon AND (2) show it was measured/"
        "estimated and the chosen value justified against it — a generic mention "
        "of 'autocorrelation' is the pre-AC-9 unexamined-floor state.\n"
        f"  concept term present:    {has_concept}\n"
        f"  estimation term present: {has_estimation}\n"
        f"Comment block found:\n{block}"
    )


def test_embargo_comment_does_not_call_the_value_a_bare_default():
    """
    AC-9: the EMBARGO_DAYS comment must no longer characterise the value as a bare
    "Default: 1 trading day" — that phrasing is exactly the unexamined-floor
    framing M-7 flags.

    After AC-9 the comment states a CONSIDERED value: either "sized to the
    measured horizon of N days" or "horizon measured at <=1 day, so the 1-day
    embargo is vindicated". Either way the word "default" alone is insufficient
    and the bare "Default: 1 trading day" phrasing must be gone.
    """
    block = _embargo_comment_block()

    assert "Default: 1 trading day" not in block, (
        "The EMBARGO_DAYS comment still reads 'Default: 1 trading day' — the "
        "unexamined-floor framing audit M-7 flags. AC-9 requires the comment to "
        "present a CONSIDERED value tied to the serial-dependence horizon "
        "(either sized to it, or explicitly vindicated against it)."
    )


def test_embargo_comment_keeps_the_lopez_de_prado_citation():
    """
    AC-9 regression guard: re-documenting EMBARGO_DAYS must not drop the existing
    López de Prado 2018 Ch. 7 citation. The embargo methodology source must
    survive the AC-9 comment rewrite.
    """
    block = _embargo_comment_block()

    has_ldp = bool(re.search(r"L[óo]pez de Prado", block) or "Lopez de Prado" in block)
    assert has_ldp and "2018" in block, (
        "The EMBARGO_DAYS comment must keep the López de Prado 2018 citation "
        "after the AC-9 rewrite. The embargo methodology source must not be lost.\n"
        f"Comment block found:\n{block}"
    )
