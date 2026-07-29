"""
RED tests — analytics.format_dollar_saved (guard-alpha $-saved display coherence).

THE BUG: every guard-alpha dollar surface (dashboard panel, History hero/by-
reason cards, Discord embeds) formats its own ad-hoc sign handling — several
of them emit a naked minus character under an unconditional "saved" label
("-$50.00 saved"), which reads as a positive claim on a losing window. The
operator-locked convention (team-lead ruling, gas-review's plan-approval
round): render the ABS magnitude with NO sign character; color + a WORD
convey direction — positive/zero -> the positive word ("saved" by default),
negative -> the negative word ("lost" by default).

CONTRACT PINNED HERE:
    analytics.format_dollar_saved(value: float, *, positive_word="saved",
                                  negative_word="lost") -> str

Parameterized so a single shared implementation can serve BOTH the guard-alpha
surfaces (saved/lost, the default) and the Managed Sleeves EOD digest (which
reports generic per-rule realized P&L, not guard-alpha savings specifically,
and uses gain/loss instead -- reporting.py:253-254, DE-SLEEVES-DIGEST).

Fails RED today: analytics.format_dollar_saved does not exist.

All expected strings are built from small numbers constructed in this test
file (not producer output) -- this is a pure-function unit test, not a
producer-value assertion (feedback_no_hardcoded_test_values governs producer
DOLLAR AMOUNTS, not literal numbers a test itself picks to exercise a format
function).
"""

from __future__ import annotations

import pytest

import analytics


class TestFormatDollarSavedDefaultWords:
    """Default positive_word="saved" / negative_word="lost" -- the guard-alpha
    web surfaces (dashboard panel, History hero, by-reason cards) never pass
    the keyword-only words, so the defaults ARE their contract."""

    def test_positive_value_renders_saved_no_sign(self):
        result = analytics.format_dollar_saved(123.45)
        assert result == "$123.45 saved", (
            f"positive value must render magnitude + 'saved', no sign char; got {result!r}"
        )

    def test_negative_value_renders_lost_no_sign(self):
        result = analytics.format_dollar_saved(-123.45)
        assert result == "$123.45 lost", (
            f"negative value must render ABS magnitude + 'lost' -- NEVER a naked minus "
            f"under a 'saved'-shaped label; got {result!r}"
        )
        assert "-" not in result, (
            f"result must carry NO sign character anywhere -- direction is conveyed by "
            f"the word alone; got {result!r}"
        )

    def test_zero_renders_saved_not_lost(self):
        """Zero is the boundary case the operator ruling calls out explicitly:
        green + 'saved' + $0.00 -- never 'lost' at exactly zero."""
        result = analytics.format_dollar_saved(0.0)
        assert result == "$0.00 saved", f"zero must render as 'saved' ($0.00), got {result!r}"

    def test_negative_result_never_contains_positive_word(self):
        result = analytics.format_dollar_saved(-50.0)
        assert "saved" not in result, (
            f"a losing figure must never carry the positive word anywhere in the "
            f"rendered string; got {result!r}"
        )

    def test_positive_result_never_contains_negative_word(self):
        result = analytics.format_dollar_saved(50.0)
        assert "lost" not in result, (
            f"a winning figure must never carry the negative word anywhere in the "
            f"rendered string; got {result!r}"
        )

    @pytest.mark.parametrize("value", [-0.001, -1e-9])
    def test_negative_result_never_contains_naked_minus(self, value):
        """Extends the boundary case to values that round-display near zero --
        a tiny negative must still say 'lost' with no sign character, not
        collapse to a misleading '$0.00 saved'-looking string with a stray '-'."""
        result = analytics.format_dollar_saved(value)
        assert result.startswith("$"), f"result must start with the bare '$' sigil, got {result!r}"
        assert "-" not in result, f"no sign character anywhere, got {result!r}"


class TestFormatDollarSavedParameterizedWords:
    """The Managed Sleeves EOD digest (reporting.py:253-254) reports generic
    per-rule realized P&L -- not guard-alpha savings -- and uses gain/loss
    per the copy ruling. Same abs+no-sign+word shape, different word pair."""

    def test_positive_value_with_custom_words_uses_positive_word(self):
        result = analytics.format_dollar_saved(75.0, positive_word="gain", negative_word="loss")
        assert result == "$75.00 gain", (
            f"custom positive_word must be used verbatim, got {result!r}"
        )

    def test_negative_value_with_custom_words_uses_negative_word_no_sign(self):
        result = analytics.format_dollar_saved(-75.0, positive_word="gain", negative_word="loss")
        assert result == "$75.00 loss", (
            f"custom negative_word must be used verbatim with ABS magnitude, no sign "
            f"char; got {result!r}"
        )
        assert "-" not in result

    def test_zero_with_custom_words_uses_positive_word(self):
        result = analytics.format_dollar_saved(0.0, positive_word="gain", negative_word="loss")
        assert result == "$0.00 gain", f"zero must use the positive word, got {result!r}"

    def test_words_are_keyword_only(self):
        """positive_word/negative_word must be keyword-only (per the pinned
        signature `format_dollar_saved(value, *, positive_word=, negative_word=)`)
        -- a positional third argument must raise, not silently reinterpret."""
        with pytest.raises(TypeError):
            analytics.format_dollar_saved(10.0, "gain", "loss")  # type: ignore[misc]


class TestFormatDollarSavedMagnitudeFormatting:
    def test_large_value_uses_thousands_separator(self):
        """Consistent with the existing dollar-formatting idiom elsewhere in
        this codebase (history.js's toLocaleString, reporting.py's :,.2f) --
        the Python side should format large magnitudes the same way."""
        result = analytics.format_dollar_saved(1234567.89)
        assert result == "$1,234,567.89 saved", f"got {result!r}"

    def test_result_is_a_plain_string(self):
        result = analytics.format_dollar_saved(42.0)
        assert isinstance(result, str)
