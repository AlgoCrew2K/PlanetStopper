"""
RED test — Cluster 6 AC-5: port_selector._tie_epsilon docstring must describe
its input as an L1 score, not a "dollar amount".

Audit provenance:
  risk-math__2026-05-21.md LOW "_tie_epsilon docstring says 'dollar amount' but
  the score is an L1 deviation".

THE FINDING. `_tie_epsilon` computes 1% of `min(|score_a|, |score_b|)`. Its
docstring frames this as "1% of the smaller (non-zero) dollar amount." But the
score passed in is `_compute_l1_score` output — a sum of per-ticker under/over
shoot deviations, NOT a portfolio dollar value. The docstring mislabels the
quantity. This is documentation-only — the tie-break behavior is correct and
deterministic — but a future reader who trusts the docstring would
mis-reason about the epsilon's scale.

This test pins the corrected docstring. It is a behavior-preserving fix: only
the docstring text changes; `_tie_epsilon`'s numeric output is unchanged. A
companion behavior assertion confirms the epsilon arithmetic itself is NOT
altered while the docstring is corrected.

No magic numbers: the behavior assertion derives the expected epsilon from the
module's own `_TIE_EPSILON_FRACTION` constant.
"""

from __future__ import annotations

import inspect

import pytest

import port_selector
from port_selector import _tie_epsilon


class TestTieEpsilonDocstringDescribesL1Score:
    """The docstring must call the input an L1 score / deviation, not a dollar
    amount — the value comes from _compute_l1_score, not a dollar quantity."""

    def test_docstring_does_not_claim_dollar_amount(self):
        doc = inspect.getdoc(_tie_epsilon) or ""
        assert "dollar" not in doc.lower(), (
            "_tie_epsilon docstring must not call its input a 'dollar amount' — "
            "the score is an L1 deviation sum from _compute_l1_score, not a "
            "portfolio dollar value"
        )

    def test_docstring_describes_input_as_l1_score(self):
        doc = inspect.getdoc(_tie_epsilon) or ""
        lowered = doc.lower()
        assert "l1" in lowered or "score" in lowered or "deviation" in lowered, (
            "_tie_epsilon docstring must describe its input as an L1 score / "
            "deviation so a reader does not mis-reason about the epsilon scale"
        )


class TestTieEpsilonBehaviorUnchanged:
    """Anti-regression: the docstring correction must NOT change the numeric
    behavior of _tie_epsilon. These assertions derive the expected output from
    the module's own constant — no captured literals."""

    def test_epsilon_is_fraction_of_smaller_magnitude(self):
        """eps = _TIE_EPSILON_FRACTION * min(|a|, |b|) for two non-zero scores."""
        fraction = port_selector._TIE_EPSILON_FRACTION
        score_a, score_b = 50000.0, 30000.0  # arbitrary L1 scores
        expected = fraction * min(abs(score_a), abs(score_b))
        assert _tie_epsilon(score_a, score_b) == pytest.approx(expected, rel=1e-12)

    def test_epsilon_falls_back_to_absolute_when_both_zero(self):
        """Two perfect-match L1 scores (0.0) fall back to the absolute 0.01
        epsilon — the documented fallback. Behavior must be unchanged."""
        result = _tie_epsilon(0.0, 0.0)
        # The fallback constant is a literal in the source; assert it is a
        # small positive epsilon rather than pinning the exact value here.
        assert result > 0.0
        assert result < 1.0, "the both-zero fallback must be a small epsilon"
