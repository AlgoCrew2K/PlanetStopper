"""LOGIC-L-1 — RED: planning docs must not misattribute ``_ACCEPTABLE_DISCIPLINES`` to autotuner.

vision-audit LOW finding (doc-only).  The dispatch brief for an earlier
cycle referenced ``autotuner._ACCEPTABLE_DISCIPLINES``.  No such symbol
exists in ``autotuner.py``; the canonical name there is
``NN1_HONEST_DISCIPLINES`` (``autotuner.py:101``).

``_ACCEPTABLE_DISCIPLINES`` IS a real symbol — but it lives at
``advisors/spec_critic.py:75``, as the producer's local default-deny set
for Indicator 2.  The two sets serve related (NN1-style) provenance roles
but are NOT the same:

  * ``autotuner.NN1_HONEST_DISCIPLINES`` — frozenset gating ``freeze_discipline``
    values that may appear in ``spec_facets`` without raising NN1 violations.
    Excludes BACKTEST_SELECTION and OOS.
  * ``advisors.spec_critic._ACCEPTABLE_DISCIPLINES`` — frozenset gating the
    advisor's Indicator-2 "recognised discipline" check.  Forward-compat
    default-deny.  Excludes BACKTEST_SELECTION by design (test_logic_m_1).

Conflating them in planning docs creates two failure modes:

  1. A reader who follows the brief lands on ``autotuner.py``, greps for
     ``_ACCEPTABLE_DISCIPLINES``, finds nothing, and either edits the
     wrong file or fabricates the symbol.
  2. A future maintainer reads the brief, edits ``spec_critic.py``'s
     local set thinking it controls autotuner NN1 enforcement, and
     silently changes producer behaviour.

This test pins source text across the planning-doc surfaces named in the
LOGIC-L-1 dispatch brief: ``feature-plans/``, ``docs/audit/``, and the
top-level ``README.md`` + ``DECISIONS.md``.  No file may contain a
substring that attributes ``_ACCEPTABLE_DISCIPLINES`` to ``autotuner``.

Today the planning docs are clean — every committed reference to
``_ACCEPTABLE_DISCIPLINES`` is correctly bound to ``spec_critic``.  This
test is therefore a regression guard against the drift creeping in via a
future copy-paste from an old dispatch brief.

Tier 1 — pure source-text scan.  No runtime imports beyond pathlib + re.
"""

from __future__ import annotations

import pathlib
import re

import pytest


_REPO_ROOT = pathlib.Path(__file__).parents[2]

# Planning-doc surfaces named in the LOGIC-L-1 brief, plus README/DECISIONS
# as the top-level planning surfaces every contributor touches.
_PLANNING_DOC_DIRS = (
    _REPO_ROOT / "feature-plans",
    _REPO_ROOT / "docs" / "audit",
)
_PLANNING_DOC_FILES = (
    _REPO_ROOT / "README.md",
    _REPO_ROOT / "DECISIONS.md",
)

# Anti-patterns: text that, in a planning doc, would misattribute the
# spec_critic-local symbol to autotuner.  Each pattern is a regex applied
# case-sensitively against the file's UTF-8 source.
#
# Pattern 1 — explicit ``autotuner._ACCEPTABLE_DISCIPLINES`` dotted reference.
#   This is the dispatch-brief drift verbatim.
# Pattern 2 — "_ACCEPTABLE_DISCIPLINES" appearing in proximity to "autotuner"
#   within ~80 chars (a sentence-scale window) on the same line — captures
#   prose drift like "in autotuner.py the _ACCEPTABLE_DISCIPLINES set ..."
#   without false-positiving across paragraphs.
_DRIFT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "autotuner._ACCEPTABLE_DISCIPLINES dotted reference",
        re.compile(r"autotuner[\w./]*\._ACCEPTABLE_DISCIPLINES"),
    ),
    (
        "autotuner near _ACCEPTABLE_DISCIPLINES on one line",
        re.compile(
            r"^(?=.*\bautotuner(?:\.py)?\b)(?=.*_ACCEPTABLE_DISCIPLINES).{0,400}$",
            re.MULTILINE,
        ),
    ),
)


def _iter_planning_docs() -> list[pathlib.Path]:
    """Enumerate every ``*.md`` planning doc the brief names as in-scope.

    Returns an explicit list (not a generator) so the test parametrisation
    can produce a stable, sorted set of test IDs.
    """
    paths: list[pathlib.Path] = []
    for d in _PLANNING_DOC_DIRS:
        if d.is_dir():
            paths.extend(sorted(d.rglob("*.md")))
    for f in _PLANNING_DOC_FILES:
        if f.is_file():
            paths.append(f)
    return paths


@pytest.fixture(scope="module")
def planning_docs() -> list[pathlib.Path]:
    docs = _iter_planning_docs()
    # Guard: if planning-doc surfaces vanish, the test silently passes by
    # parametrising over zero files — make that condition fail loudly.
    assert docs, (
        "LOGIC-L-1 source-text scan found zero planning docs under "
        f"{[str(p) for p in _PLANNING_DOC_DIRS]} or "
        f"{[str(p) for p in _PLANNING_DOC_FILES]}. "
        "The planning-doc surfaces named in the LOGIC-L-1 brief are missing — "
        "either the repo layout changed or the test's surface list is stale."
    )
    return docs


def test_planning_docs_have_no_autotuner_acceptable_disciplines_drift(
    planning_docs: list[pathlib.Path],
) -> None:
    """Source-text RED — no planning doc may misattribute ``_ACCEPTABLE_DISCIPLINES``
    to ``autotuner``.

    Every committed reference to ``_ACCEPTABLE_DISCIPLINES`` must be tied
    to ``advisors/spec_critic.py`` (its true home).  Any planning doc that
    pairs the symbol with ``autotuner`` is dispatch-brief drift and must
    use the correct name ``NN1_HONEST_DISCIPLINES`` instead.
    """
    violations: list[str] = []
    for doc in planning_docs:
        text = doc.read_text(encoding="utf-8")
        for label, pattern in _DRIFT_PATTERNS:
            for match in pattern.finditer(text):
                # Report 1-indexed line number so the violation is clickable.
                line_no = text.count("\n", 0, match.start()) + 1
                snippet = match.group(0).strip()
                if len(snippet) > 160:
                    snippet = snippet[:157] + "..."
                rel = doc.relative_to(_REPO_ROOT)
                violations.append(
                    f"  {rel}:{line_no} — {label}\n    > {snippet}"
                )

    assert not violations, (
        "LOGIC-L-1 doc drift — planning docs misattribute "
        "`_ACCEPTABLE_DISCIPLINES` to autotuner. The canonical autotuner "
        "symbol is `NN1_HONEST_DISCIPLINES` (autotuner.py:101). "
        "`_ACCEPTABLE_DISCIPLINES` is the spec_critic-local set "
        "(advisors/spec_critic.py:75). Replace the reference with the "
        "correct symbol name.\n\nViolations:\n" + "\n".join(violations)
    )


def test_autotuner_does_not_expose_acceptable_disciplines() -> None:
    """Anchor sanity — the autotuner truly has no ``_ACCEPTABLE_DISCIPLINES``.

    If a future commit introduces ``autotuner._ACCEPTABLE_DISCIPLINES``
    (e.g. as an alias), the planning-doc rule above becomes incorrect.
    Pin the negative-existence assertion so the regression is impossible
    to land silently — either the symbol stays absent, or this test fails
    and the LOGIC-L-1 contract is re-examined explicitly.
    """
    import autotuner  # local import — module is heavy; avoid import-time cost

    assert not hasattr(autotuner, "_ACCEPTABLE_DISCIPLINES"), (
        "autotuner module now exposes `_ACCEPTABLE_DISCIPLINES` — LOGIC-L-1 "
        "negative-existence anchor broken. If this symbol was introduced "
        "deliberately, retire test_logic_l_1_brief_symbol_drift and update "
        "every planning doc that referenced `NN1_HONEST_DISCIPLINES` to "
        "name the new authoritative set."
    )
    # And the positive anchor: the canonical name still exists.
    assert hasattr(autotuner, "NN1_HONEST_DISCIPLINES"), (
        "autotuner.NN1_HONEST_DISCIPLINES is missing — the canonical name "
        "is gone. Update the LOGIC-L-1 contract before deleting it."
    )


def test_spec_critic_acceptable_disciplines_still_local() -> None:
    """Anchor sanity — ``_ACCEPTABLE_DISCIPLINES`` still lives on spec_critic.

    The LOGIC-L-1 rule depends on the symbol being unambiguously the
    spec_critic-local set.  If that ownership moves (e.g. spec_critic
    starts importing ``NN1_HONEST_DISCIPLINES`` directly and the local
    set is deleted), the disambiguation argument changes.
    """
    from advisors import spec_critic

    assert hasattr(spec_critic, "_ACCEPTABLE_DISCIPLINES"), (
        "advisors.spec_critic._ACCEPTABLE_DISCIPLINES is missing — the "
        "LOGIC-L-1 disambiguation argument assumes this is the symbol's "
        "true home. If the local set was retired, retire the LOGIC-L-1 "
        "rule too."
    )
    assert isinstance(spec_critic._ACCEPTABLE_DISCIPLINES, frozenset), (
        "advisors.spec_critic._ACCEPTABLE_DISCIPLINES must remain a "
        "frozenset (immutable producer config)."
    )
