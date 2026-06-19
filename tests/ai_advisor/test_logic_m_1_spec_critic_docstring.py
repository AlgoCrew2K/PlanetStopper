"""LOGIC-M-1 — RED: ``advisors/spec_critic.py`` docstring must match code.

math-reaudit MEDIUM finding (doc-only). The module docstring + inline comments
at ``advisors/spec_critic.py:8-10, 25`` claim BACKTEST_SELECTION is an
acceptable freeze_discipline under indicator I-2, but the source-of-truth set
``_ACCEPTABLE_DISCIPLINES`` (lines 72-79) deliberately excludes
BACKTEST_SELECTION (the autotuner's NN1 spec-freeze rejects it as
overfitting-prone — default-deny defense-in-depth).

These tests pin the *contract between the docstring and the code* — they fail
RED until the production module's docstring + inline comments are corrected to
match the actual ``_ACCEPTABLE_DISCIPLINES`` set. No production behaviour
changes; ``_ACCEPTABLE_DISCIPLINES`` is correct as-is.

Scope:
  - Source-text contract pins (read the .py file as text, assert substrings).
  - Behaviour-shape pin: BACKTEST_SELECTION must continue to trigger BREACH at
    runtime (regression guard against a future "fix" that mutates the set
    instead of the docstring).

Tier 1 — pure-text + pure-function. No DB, no network.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import advisors.spec_critic as spec_critic_mod
from advisors.spec_critic import (
    _ACCEPTABLE_DISCIPLINES,
    compute_spec_critic_observation,
)

# ---------------------------------------------------------------------------
# Source-text helpers
# ---------------------------------------------------------------------------


def _module_source_text() -> str:
    """Return the raw text of advisors/spec_critic.py.

    Read from disk (not __doc__) so we catch drift in *every* comment / docstring
    in the file, not just the module-level docstring.
    """
    path = Path(inspect.getsourcefile(spec_critic_mod))
    return path.read_text(encoding="utf-8")


def _module_docstring() -> str:
    """Return the module-level docstring (advisors/spec_critic.py top-of-file)."""
    return spec_critic_mod.__doc__ or ""


# ---------------------------------------------------------------------------
# Test 1 — _ACCEPTABLE_DISCIPLINES is the authoritative set (anchor sanity).
# ---------------------------------------------------------------------------


def test_acceptable_disciplines_excludes_backtest_selection():
    """Anchor: confirm production set excludes BACKTEST_SELECTION.

    If this ever flips, the docstring-drift fix went the wrong way (mutated the
    set instead of the docstring).  Pin first; everything below relies on this.
    """
    assert "BACKTEST_SELECTION" not in _ACCEPTABLE_DISCIPLINES, (
        "Regression: _ACCEPTABLE_DISCIPLINES must NOT contain BACKTEST_SELECTION. "
        "The autotuner's NN1 spec-freeze rejects it as overfitting-prone. "
        f"Got: {sorted(_ACCEPTABLE_DISCIPLINES)}"
    )


# ---------------------------------------------------------------------------
# Test 2 — Module docstring must NOT claim BACKTEST_SELECTION is acceptable.
# ---------------------------------------------------------------------------


def test_module_docstring_does_not_claim_backtest_selection_acceptable():
    """Pin: docstring must not name BACKTEST_SELECTION inside the I-2 paragraph.

    The pre-fix docstring (lines 8-10) reads:
        "All facets have a recognised freeze_discipline (in NN1_HONEST_DISCIPLINES
         union {BACKTEST_SELECTION}).  Any unrecognised discipline → BREACH"
    which contradicts ``_ACCEPTABLE_DISCIPLINES``.  After fix, BACKTEST_SELECTION
    must not appear in the docstring at all, OR only appear in a *rejection*
    context (explicitly labelled as excluded).
    """
    doc = _module_docstring()

    # Strict form: the docstring must not contain the literal token at all in an
    # "acceptable" framing.  The simplest enforceable contract is: no occurrence
    # of BACKTEST_SELECTION inside the docstring.  If a future docstring needs
    # to *mention* it (to call out the exclusion), the test below carves the
    # narrow exception.
    if "BACKTEST_SELECTION" in doc:
        # Allow ONLY if every occurrence sits in a clearly-negative clause —
        # i.e., the same line/sentence also contains an exclusion keyword.
        lines_with_token = [ln for ln in doc.splitlines() if "BACKTEST_SELECTION" in ln]
        for ln in lines_with_token:
            lowered = ln.lower()
            negated = any(
                kw in lowered for kw in ("not ", "exclud", "reject", "forbidden", "disallow")
            )
            assert negated, (
                "Module docstring mentions BACKTEST_SELECTION without an "
                "exclusion keyword on the same line. Drift: docstring suggests "
                "BACKTEST_SELECTION is acceptable, but _ACCEPTABLE_DISCIPLINES "
                f"excludes it. Offending line: {ln!r}"
            )


# ---------------------------------------------------------------------------
# Test 3 — Module docstring must NOT contain the "union {BACKTEST_SELECTION}"
# phrase that triggered the audit finding.
# ---------------------------------------------------------------------------


def test_module_docstring_no_union_backtest_selection_phrase():
    """The exact drift phrase from the audit must be removed.

    Pre-fix text included "NN1_HONEST_DISCIPLINES union {BACKTEST_SELECTION}"
    or "NN1_HONEST_DISCIPLINES ∪ {BACKTEST_SELECTION}".  Either form is wrong.
    """
    doc = _module_docstring()
    # Normalise whitespace to catch line-wrapped variants.
    flat = re.sub(r"\s+", " ", doc)
    bad_patterns = [
        "union {BACKTEST_SELECTION}",
        "∪ {BACKTEST_SELECTION}",
        "union { BACKTEST_SELECTION }",
        "∪ { BACKTEST_SELECTION }",
    ]
    for phrase in bad_patterns:
        assert phrase not in flat, (
            f"Module docstring still contains drift phrase {phrase!r}. "
            "Audit LOGIC-M-1: this phrase falsely implies BACKTEST_SELECTION is "
            "in the acceptable set."
        )


# ---------------------------------------------------------------------------
# Test 4 — Full source file: inline comments (lines 8-10 + 25 region) cleaned.
# ---------------------------------------------------------------------------


def test_module_source_no_acceptable_backtest_selection_claim():
    """Whole-file scan: no comment/docstring asserts BACKTEST_SELECTION acceptance.

    The audit explicitly cites lines 8-10 (module docstring) and line 25 (which
    sits in the I-2 region as quoted in the docstring).  Sweep the entire .py
    source to catch any comment that still implies acceptance, regardless of
    exact line numbers (line numbers drift with edits).
    """
    src = _module_source_text()
    flat = re.sub(r"\s+", " ", src)

    # Forbidden framings — any string here implies BACKTEST_SELECTION is OK.
    forbidden = [
        "union {BACKTEST_SELECTION}",
        "∪ {BACKTEST_SELECTION}",
        "in NN1_HONEST_DISCIPLINES union {BACKTEST_SELECTION}",
        "in NN1_HONEST_DISCIPLINES ∪ {BACKTEST_SELECTION}",
    ]
    for phrase in forbidden:
        assert phrase not in flat, (
            f"advisors/spec_critic.py source still contains {phrase!r}. "
            "LOGIC-M-1 doc drift — _ACCEPTABLE_DISCIPLINES does NOT include "
            "BACKTEST_SELECTION; comments/docstrings must not claim it does."
        )


# ---------------------------------------------------------------------------
# Test 5 — Behaviour pin: BACKTEST_SELECTION continues to BREACH I-2.
# ---------------------------------------------------------------------------


def _minimal_valid_facets(frozen_iso: str = "2026-05-01T00:00:00Z") -> list[dict]:
    """Return facets that satisfy I-1 (all three required) and pass I-3/I-4.

    Used as the baseline; individual tests mutate one row to probe a single
    indicator.  Discipline is set to THEORY (recognised) by default.
    """
    return [
        {
            "facet_name": "gamma",
            "freeze_discipline": "THEORY",
            "frozen_at": frozen_iso,
        },
        {
            "facet_name": "utility_family",
            "freeze_discipline": "THEORY",
            "frozen_at": frozen_iso,
        },
        {
            "facet_name": "wealth_argument",
            "freeze_discipline": "THEORY",
            "frozen_at": frozen_iso,
        },
    ]


def test_backtest_selection_discipline_triggers_breach_runtime():
    """Behaviour guardrail: a facet with discipline=BACKTEST_SELECTION → BREACH.

    Independent of any future docstring edit — this asserts the runtime
    behaviour matches the *code* (the authoritative `_ACCEPTABLE_DISCIPLINES`
    set).  Protects against a regression that "fixes" the doc-drift by
    *adding* BACKTEST_SELECTION to the set.
    """
    facets = _minimal_valid_facets()
    # Mutate one facet's discipline to the disallowed value.
    facets[0]["freeze_discipline"] = "BACKTEST_SELECTION"

    obs = compute_spec_critic_observation("bundle_logicm1", facets)

    assert obs["verdict"] == "BREACH", (
        "Runtime regression: BACKTEST_SELECTION must trigger I-2 BREACH "
        "(default-deny). Autotuner's NN1 spec-freeze rejects it as "
        f"overfitting-prone. Got verdict={obs['verdict']!r}, raw_response="
        f"{obs.get('raw_response')!r}"
    )
