"""
RED tests — CVaR-divergence REJECT wall is preserved by the wire-up fix.

SCOPE — item 4 of the vision-audit Critical Rec #1 dispatch brief
-----------------------------------------------------------------
The CVaR-divergence detector was REJECTED by the decision-science council
(§B.9) because "validate a detector not an estimate" only RELOCATES the
data wall — the detector's validation requires ~5-15 independent regime-
shift events, and those events correlate with exactly the tail days the
CVaR estimate already exhausts. The detector does not escape the wall;
it hides it behind a different question.

Project memory: ``project_cvar_divergence_validation_wall``.
DECISIONS.md: §DE-S3-005 ("CVaR-divergence REJECT").

The wire-up fix landing in this cycle MUST NOT re-litigate the detector.
Tests in this file guard the source surface against any reappearance of:
  * divergence arithmetic in alpha_bot_execution.py
  * a "signed divergence" column / field / kwarg
  * a Phase-2 deferred-divergence comment
"""

from __future__ import annotations

import pathlib
import re

_WORKTREE_ROOT = pathlib.Path(__file__).parents[2]
_ALPHA_BOT_PATH = _WORKTREE_ROOT / "alpha_bot_execution.py"
_MATH_ENGINE_PATH = _WORKTREE_ROOT / "math_engine.py"
_DIVERGENCE_EXPLAINER_PATH = _WORKTREE_ROOT / "advisors" / "divergence_explainer.py"


# ---------------------------------------------------------------------------
# 1. Source scan — no divergence arithmetic on the engine path
# ---------------------------------------------------------------------------


# The legitimate references — the rejection memo itself — are inside docstrings
# and module-level constants; any RUNTIME divergence arithmetic (a subtraction
# of cvar_5pct - cvar_5pct_long, a column write of "divergence", etc.) is the
# defect we guard against.

_FORBIDDEN_DIVERGENCE_TOKENS = (
    "cvar_divergence",
    "signed_divergence",
    "cvar_diff",
    "cvar_delta",
    "window_divergence",
    "divergence_pct",
)


def test_alpha_bot_execution_contains_no_divergence_tokens():
    """``alpha_bot_execution.py`` is the engine path; it must contain ZERO
    references to a CVaR-divergence quantity. The rejection memo lives
    in README + DECISIONS + memory, not in runtime code.
    """
    source = _ALPHA_BOT_PATH.read_text(encoding="utf-8")
    offending = []
    for token in _FORBIDDEN_DIVERGENCE_TOKENS:
        if token in source.lower():
            offending.append(token)
    assert not offending, (
        f"alpha_bot_execution.py contains forbidden CVaR-divergence token(s): "
        f"{offending!r}. The detector is permanently REJECTED "
        "(DECISIONS.md §DE-S3-005; project memory "
        "[[project_cvar_divergence_validation_wall]])."
    )


def test_math_engine_contains_no_divergence_tokens():
    """``math_engine.py`` exposes the kNN CVaR estimator. It must contain
    no divergence quantity, computed or partial.
    """
    source = _MATH_ENGINE_PATH.read_text(encoding="utf-8")
    offending = []
    for token in _FORBIDDEN_DIVERGENCE_TOKENS:
        if token in source.lower():
            offending.append(token)
    assert not offending, (
        f"math_engine.py contains forbidden CVaR-divergence token(s): {offending!r}. "
        "No divergence arithmetic belongs in the estimator."
    )


def test_alpha_bot_execution_does_not_subtract_two_cvar_fields():
    """A subtraction pattern ``cvar_5pct - cvar_5pct_long`` (or the reverse)
    in alpha_bot_execution.py is the most likely reincarnation of the
    rejected detector.
    """
    source = _ALPHA_BOT_PATH.read_text(encoding="utf-8")
    sub_pattern = re.compile(
        r"cvar_5pct(?:_long)?\s*[-]\s*cvar_5pct(?:_long)?",
        re.IGNORECASE,
    )
    matches = sub_pattern.findall(source)
    assert not matches, (
        f"alpha_bot_execution.py contains a CVaR-subtraction pattern: "
        f"{matches!r}. This reads as the rejected signed-divergence detector."
    )


# ---------------------------------------------------------------------------
# 2. Divergence Explainer's forbidden-keys constant is intact (anchor)
# ---------------------------------------------------------------------------


def test_divergence_explainer_forbidden_keys_doc_still_lists_canonical_names():
    """The Divergence Explainer module docstring enumerates the forbidden
    raw_response keys. The CVaR wire-up fix must not have edited that list
    (no relaxation, no removal). This is the canonical anchor that other
    code reviews cite.
    """
    source = _DIVERGENCE_EXPLAINER_PATH.read_text(encoding="utf-8")
    canonical_phrases = [
        "Forbidden raw_response keys",
        "divergence",
        "signed_divergence",
        "cvar_diff",
        "cvar_delta",
        "window_divergence",
        "divergence_pct",
    ]
    missing = [p for p in canonical_phrases if p not in source]
    assert not missing, (
        f"advisors/divergence_explainer.py is missing canonical forbidden-key "
        f"anchors: {missing!r}. The CVaR wire-up fix must not erode the "
        "Divergence Explainer's rejection contract (DECISIONS.md §DE-S3-005)."
    )
