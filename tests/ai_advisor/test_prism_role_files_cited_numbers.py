"""
RED tests for the AC-2 council-prompt contract change (DE-PRISM-NUMERIC-VERIFY-001).

AC-2: the synthesizer's raw_response schema gains a structured
cited_numbers: list[{indicator, value, lens, source_hint?}] array, and the synthesizer +
5 analyst role files (.claude/agents/prism-*.md) instruct that every numeric indicator
stated in prose MUST also appear as a cited_numbers tuple (so the verifier sees the same
numbers the operator reads).

These are prose-content static-scan tests against the .claude/agents/*.md role files —
this is established practice in this codebase, not novel: tests/ai_advisor/
test_prism_scheduling.py already asserts prism-synthesizer.md content (agentId
addressing, the initial_read wait-barrier Hard Rule). Every assertion here is scoped to
a bounded character window around a genuine co-occurrence of the field name AND an
instruction marker — a bare mention of "cited_numbers" in isolation (e.g. leaking in from
an unrelated JSON example) does not satisfy these, mirroring the "hollow regex" discipline
that test_prism_scheduling.py's F-2 tests document explicitly.

Expected RED on HEAD: none of the 6 role files mention "cited_numbers" at all yet.
"""

from __future__ import annotations

import os
import re

import pytest

_ANALYST_FILES = [
    "prism-technicals-analyst.md",
    "prism-sentiment-analyst.md",
    "prism-derivatives-analyst.md",
    "prism-macro-analyst.md",
    "prism-fundamentals-analyst.md",
]

_SYNTHESIZER_FILE = "prism-synthesizer.md"

# Instruction markers that indicate a MANDATE (not just a passing mention).
_INSTRUCTION_MARKERS = ("must", "MUST", "every", "each", "required")

# Hedge/negation phrases that, if co-located with an instruction marker, invalidate
# an otherwise-matching window (PM sufficiency review, Probe 5): a naive co-occurrence
# check on bare "cited_numbers" + "must"/"required"/"every" would be satisfied by a
# hypothetical hedged edit like "cited_numbers exists but is not required" — which
# means the OPPOSITE of a mandate. This guards against that specific false-positive
# without inventing a full NLP negation detector.
_NEGATION_MARKERS = ("not required", "optional", "no need to", "not necessary", "need not")

# Window (chars) within which "cited_numbers" and an instruction marker must co-occur —
# large enough to span one sentence/bullet, small enough to reject an accidental
# co-occurrence from unrelated file sections (mirrors the 300-char window used by
# test_prism_scheduling.py's F-2 Hard-Rules tests).
_COOCCURRENCE_WINDOW = 400


def _agents_dir() -> str:
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    worktree_root = os.path.dirname(os.path.dirname(tests_dir))
    return os.path.join(worktree_root, ".claude", "agents")


def _read_agent_file(filename: str) -> str:
    filepath = os.path.join(_agents_dir(), filename)
    with open(filepath, encoding="utf-8") as fh:
        return fh.read()


def _find_cooccurrence_window(
    text: str,
    marker_a: str,
    marker_b_options: tuple[str, ...],
    window: int,
    negation_markers: tuple[str, ...] = (),
) -> str | None:
    """Return the first genuine (non-hedged) co-occurrence window text, or None.

    A window "counts" only when marker_a and some marker_b_options entry co-occur
    AND none of negation_markers appears in that same window — a hedged/negated
    mention (e.g. "cited_numbers ... not required") does not satisfy this, even
    though the bare marker words are present.
    """
    text_lower = text.lower()
    marker_a_lower = marker_a.lower()
    positions_a = [m.start() for m in re.finditer(re.escape(marker_a_lower), text_lower)]
    for pos_a in positions_a:
        window_text = text_lower[max(0, pos_a - window) : pos_a + window]
        if any(b.lower() in window_text for b in marker_b_options):
            if negation_markers and any(neg.lower() in window_text for neg in negation_markers):
                continue  # hedged/negated — does not count as a genuine mandate
            return window_text
    return None


def _has_cooccurrence(
    text: str,
    marker_a: str,
    marker_b_options: tuple[str, ...],
    window: int,
    negation_markers: tuple[str, ...] = (),
) -> bool:
    """True iff a genuine (non-hedged) co-occurrence window exists — see
    _find_cooccurrence_window."""
    return (
        _find_cooccurrence_window(text, marker_a, marker_b_options, window, negation_markers)
        is not None
    )


# ---------------------------------------------------------------------------
# Synthesizer: raw_response schema gains cited_numbers (AC-2)
# ---------------------------------------------------------------------------


class TestSynthesizerCitedNumbersSchema:
    def test_synthesizer_file_exists(self):
        filepath = os.path.join(_agents_dir(), _SYNTHESIZER_FILE)
        assert os.path.exists(filepath), f"{_SYNTHESIZER_FILE} not found at {filepath}"

    def test_synthesizer_raw_response_section_references_cited_numbers(self):
        """AC-2: the '### 9. Write the MARKET_PRISM observation row' section (the
        raw_response schema block) must reference 'cited_numbers'.

        RED intent: the current file's raw_response example has no cited_numbers key.
        """
        content = _read_agent_file(_SYNTHESIZER_FILE)
        # Scope to the step-9 section only (raw_response schema), same technique as
        # test_prism_scheduling.py's Hard-Rules section extraction — prevents a
        # coincidental match elsewhere in the file from satisfying this.
        parts = content.split("### 9.", maxsplit=1)
        assert len(parts) == 2, (
            "prism-synthesizer.md must have a '### 9.' raw_response section heading "
            "(step 9 currently writes the MARKET_PRISM observation row)."
        )
        section = parts[1].split("### 10", maxsplit=1)[0]
        assert "cited_numbers" in section, (
            "prism-synthesizer.md's step-9 raw_response schema block must include a "
            "'cited_numbers' field (AC-2) so the numeric verifier has structured "
            f"tuples to check. Section text (first 800 chars): {section[:800]!r}"
        )

    def test_synthesizer_instructs_every_prose_number_needs_a_tuple(self):
        """AC-2: the file must instruct — not just schema-declare — that every numeric
        indicator stated in prose also appears as a cited_numbers tuple. Requires
        'cited_numbers' to co-occur with a mandate marker within a bounded window,
        so a bare JSON-example mention does not satisfy this. A hedged/negated mention
        (e.g. "cited_numbers is not required") does NOT satisfy this either (Probe 5
        hardening — see _NEGATION_MARKERS)."""
        content = _read_agent_file(_SYNTHESIZER_FILE)
        found = _has_cooccurrence(
            content,
            "cited_numbers",
            _INSTRUCTION_MARKERS,
            _COOCCURRENCE_WINDOW,
            negation_markers=_NEGATION_MARKERS,
        )
        assert found, (
            "prism-synthesizer.md must instruct that EVERY numeric indicator stated in "
            "prose (summary/sentiment_rationale) MUST also appear as a cited_numbers "
            "tuple — a schema field alone (with no mandate), or a hedged/negated "
            "mention, is insufficient; the verifier only sees what's in cited_numbers, "
            "so an unenforced field would let numbers slip through unverified. "
            "Add e.g.: 'Every numeric indicator you state in prose MUST also appear as "
            "a {indicator, value, lens} tuple in cited_numbers.'"
        )


# ---------------------------------------------------------------------------
# Analyst files: each must instruct emitting cited_number tuples (AC-2)
# ---------------------------------------------------------------------------


class TestAnalystFilesCitedNumbersInstruction:
    @pytest.mark.parametrize("filename", _ANALYST_FILES)
    def test_analyst_file_exists(self, filename):
        filepath = os.path.join(_agents_dir(), filename)
        assert os.path.exists(filepath), f"{filename} not found at {filepath}"

    @pytest.mark.parametrize("filename", _ANALYST_FILES)
    def test_analyst_file_instructs_emitting_cited_number_tuples(self, filename):
        """AC-2: each analyst file must instruct emitting a {indicator, value, lens}
        tuple for every numeric indicator stated in prose to the synthesizer.

        Requires 'cited_numbers' (or the literal tuple shape 'indicator'+'value'+'lens'
        co-occurring) alongside a mandate marker within a bounded window — a stray
        mention of any one of these words elsewhere in the file does not satisfy this.
        A hedged/negated mention does NOT satisfy this either (Probe 5 hardening).
        """
        content = _read_agent_file(filename)

        has_cited_numbers_mandate = _has_cooccurrence(
            content,
            "cited_numbers",
            _INSTRUCTION_MARKERS,
            _COOCCURRENCE_WINDOW,
            negation_markers=_NEGATION_MARKERS,
        )

        assert has_cited_numbers_mandate, (
            f"{filename} must instruct the analyst to emit a {{indicator, value, lens}} "
            "tuple (cited_numbers) for every numeric indicator it states in prose to "
            "the synthesizer — required so the post-council numeric verifier "
            "(DE-PRISM-NUMERIC-VERIFY-001) has structured claims to check against the "
            "authoritative source payloads (a hedged/negated mention does not count). "
            "Add e.g.: 'For every numeric indicator you state, also report it as a "
            "{indicator, value, lens} tuple so the synthesizer can include it in "
            "cited_numbers.'"
        )

    @pytest.mark.parametrize("filename", _ANALYST_FILES)
    def test_analyst_file_tuple_shape_mentions_indicator_value_lens(self, filename):
        """AC-2: the instruction must name the actual tuple shape (indicator/value/lens)
        WITHIN the same mandate window as the cited_numbers instruction — not just
        somewhere else in the file (Probe 5 hardening).

        RED-safety note: "value" and "lens" are common words that already appear
        elsewhere in every analyst file for unrelated reasons (confirmed: e.g. "the
        lens", "this value") — a whole-file presence check would be satisfied
        trivially regardless of whether the cited_numbers instruction actually
        documents the tuple shape. Scoping to the matched mandate window closes that.
        """
        content = _read_agent_file(filename)
        window_text = _find_cooccurrence_window(
            content,
            "cited_numbers",
            _INSTRUCTION_MARKERS,
            _COOCCURRENCE_WINDOW,
            negation_markers=_NEGATION_MARKERS,
        )
        assert window_text is not None, (
            f"{filename}: no genuine cited_numbers mandate window found — see "
            "test_analyst_file_instructs_emitting_cited_number_tuples for the primary "
            "assertion; this test depends on that window existing."
        )
        for field in ("indicator", "value", "lens"):
            assert field in window_text, (
                f"{filename} must mention '{field}' (part of the {{indicator, value, "
                "lens}} cited_numbers tuple shape) WITHIN the same instruction window "
                "as the cited_numbers mandate — so the analyst knows the concrete "
                f"shape to emit, not just that the field exists. Got no occurrence of "
                f"{field!r} in the matched window: {window_text!r}"
            )
