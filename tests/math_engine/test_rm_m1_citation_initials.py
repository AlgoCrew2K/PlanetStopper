"""
AC-10 (RM-M1) — Fu & Zhang citation initials correction.

The methodology-literature audit caught that math_engine.py:273 cites
"Fu, M.C. & Zhang, H." for the trailing-stop construction reference. The
actual paper authors are "Fu, Y.B. & Zhang, Z.G." — the "M.C. Fu" cited
is a different well-known IS researcher (risk of conflation; a
maintainer following the citation would arrive at the wrong paper).

Plan AC-10 mandates the one-line fix. Pin the exact post-fix string.

Provenance: the corrected initials come from the audit at
docs/research/math-audit-2/methodology-literature__2026-05-22.md (the
binding report). The test does NOT verify the paper exists or the
initials are real-world correct — that is the audit's domain — only
that the docstring matches the corrected form the audit prescribes.
"""

from __future__ import annotations

import inspect

import math_engine


class TestFuZhangCitationInitials:
    """The compute_breakeven_update docstring must cite Fu & Zhang with
    the corrected initials."""

    def test_breakeven_update_docstring_uses_corrected_initials(self):
        """The corrected form is 'Fu, Y.B. & Zhang, Z.G.'."""
        doc = inspect.getdoc(math_engine.compute_breakeven_update) or ""
        assert "Fu, Y.B." in doc, (
            "AC-10 / RM-M1 VIOLATED: compute_breakeven_update docstring "
            "must cite 'Fu, Y.B.' (not 'Fu, M.C.' — that is a different, "
            "well-known IS researcher; the actual trailing-stop paper "
            "authors are Y.B. Fu and Z.G. Zhang per Semantic Scholar "
            f"verification). Current docstring: {doc!r}"
        )
        assert "Zhang, Z.G." in doc, (
            "AC-10 / RM-M1 VIOLATED: compute_breakeven_update docstring "
            "must cite 'Zhang, Z.G.' (not 'Zhang, H.'). Current "
            f"docstring: {doc!r}"
        )
        # And the incorrect form must be gone.
        assert "Fu, M.C." not in doc, (
            "AC-10 / RM-M1 REGRESSION: 'Fu, M.C.' (the M.C. Fu / IS "
            "researcher conflation) is still present in the docstring. "
            f"Current docstring: {doc!r}"
        )
        assert "Zhang, H." not in doc, (
            "AC-10 / RM-M1 REGRESSION: 'Zhang, H.' (the incorrect "
            f"initials) is still present in the docstring: {doc!r}"
        )
