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


class TestFormatDollarSavedRoundsLikeTheBrowser:
    """The History page rounds with Number.prototype.toFixed (ties away from zero over the
    exact binary value); the Discord and Sleeves text must agree on every tie, so the
    magnitude follows the same rule instead of the f-string's half-to-even.

    The expected digits come from node's own toFixed, the reference rule, not from this
    module or from analytics.js_to_fixed.
    """

    # Exact-binary two-place ties, the near-zero pair, a thousands case and zero.
    VALUES = [0.125, -0.125, 0.375, 0.625, -0.625, 0.875, 0.004, -0.004, 1234.5, 1234567.125, 0.0]

    @staticmethod
    def _node_magnitudes(values: list[float]) -> list[str]:
        import json
        import shutil
        import subprocess

        node = shutil.which("node")
        assert node, "node is not installed; the rounding parity test needs it"
        script = "const v = JSON.parse(process.argv[1]); console.log(JSON.stringify(v.map(x => Math.abs(x).toFixed(2))));"
        out = subprocess.run(
            [node, "-e", script, json.dumps(values)], capture_output=True, text=True, timeout=30
        )
        assert out.returncode == 0, out.stderr
        return json.loads(out.stdout)

    def test_the_magnitude_digits_equal_the_browsers_to_fixed_over_the_tie_family(self):
        expected = self._node_magnitudes(self.VALUES)
        for value, digits in zip(self.VALUES, expected, strict=True):
            text = analytics.format_dollar_saved(value)
            magnitude = text.removeprefix("$").rsplit(" ", 1)[0].replace(",", "")
            assert magnitude == digits, (
                f"{value!r} must round as toFixed(2) does ({digits}); got {text!r}"
            )

    def test_the_word_follows_the_sign_of_the_unrounded_value(self):
        for value in self.VALUES:
            word = analytics.format_dollar_saved(value).rsplit(" ", 1)[1]
            assert word == ("saved" if value >= 0 else "lost"), f"{value!r} read {word!r}"

    def test_a_negative_value_that_rounds_to_zero_still_reads_lost_with_no_minus(self):
        text = analytics.format_dollar_saved(-0.004)
        assert text == "$0.00 lost"

    def test_custom_words_keep_the_same_rounding(self):
        text = analytics.format_dollar_saved(0.125, positive_word="gain", negative_word="loss")
        assert text == "$0.13 gain"

    def test_the_magnitude_is_rounded_through_the_shared_to_fixed_helper(self, monkeypatch):
        calls: list[tuple] = []
        real = analytics.js_to_fixed

        def spy(value, places):
            calls.append((value, places))
            return real(value, places)

        monkeypatch.setattr(analytics, "js_to_fixed", spy)
        analytics.format_dollar_saved(-12.5)
        assert calls == [(12.5, 2)], "format_dollar_saved must call analytics.js_to_fixed(abs(value), 2)"
