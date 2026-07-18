"""
R3-c / MA-11 AC-7 — metadata honesty for MAX_SQUEEZE_FLOOR in app.py's
``_ALGO_PARAM_META``.

Before this cycle the UI metadata LIED: it labelled a stop-DISTANCE floor with
unit "×" and kind "mult" (a multiplier), while the help text already described
a distance ("tightest the stop distance can shrink"). Wiring the knob makes it
a real post-squeeze clamp on the stop DISTANCE (percentage points), so the
metadata must read as a distance to match the comparable pct-kind params.

Team string ruling (EXACT pins): kind == "pct", unit == "%" (the sibling
pct-kind entries pair with "%"; "pp" would be a novel unit string — the help
text's "distance" wording carries the disambiguation). The help must keep a
"distance" word and must NOT call the knob a "multiplier".

RED at HEAD: kind == "mult", unit == "×".
"""

from __future__ import annotations

import app

_META = app._ALGO_PARAM_META["MAX_SQUEEZE_FLOOR"]


def test_kind_is_pct_not_mult() -> None:
    """AC-7: distance semantics -> kind 'pct', matching the comparable
    percentage-distance params (e.g. TRIGGER_THRESHOLD_PCT)."""
    assert _META["kind"] == "pct", (
        f"MAX_SQUEEZE_FLOOR is a stop-DISTANCE floor; kind must be 'pct' "
        f"(the multiplier lie 'mult' is wrong), got {_META['kind']!r}."
    )


def test_unit_is_percent_not_multiplier_glyph() -> None:
    """AC-7: unit '%' (EXACT), never the multiplier glyph '×'."""
    assert _META["unit"] == "%", (
        f"MAX_SQUEEZE_FLOOR unit must be '%' (a distance), not the multiplier "
        f"glyph '×', got {_META['unit']!r}."
    )


def test_help_is_distance_worded_not_multiplier() -> None:
    """AC-7: the help text keeps a 'distance' word and never calls the knob a
    'multiplier' (which would re-introduce the semantic lie)."""
    help_text = _META["help"].lower()
    assert "distance" in help_text, (
        f"help text must describe the knob as a stop DISTANCE floor, got: {_META['help']!r}"
    )
    assert "multiplier" not in help_text, (
        f"help text must NOT call MAX_SQUEEZE_FLOOR a multiplier, got: {_META['help']!r}"
    )
