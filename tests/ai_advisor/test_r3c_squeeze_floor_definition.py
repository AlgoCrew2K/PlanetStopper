"""
R3-c / MA-11 AC-7 — advisor definition honesty for MAX_SQUEEZE_FLOOR in
ai_advisor.py's ``_PARAM_DEFINITIONS``.

The old definition read "Floor on the squeeze MULTIPLIER applied to the active
stop." Wiring the knob makes it a floor on the post-squeeze stop DISTANCE (pp),
so the advisor's human-facing definition must say distance, not multiplier.

Unchanged (pinned so the reword doesn't drift them): risk_polarity stays
"raising loosens risk" (a higher distance floor => wider stop => looser risk —
still correct under distance semantics); the suggestible range stays 0.05–0.50.

RED at HEAD: the definition contains "multiplier" and lacks "distance".
"""

from __future__ import annotations

import ai_advisor

_KEY = ai_advisor._UNTUNED_SUGGESTIBLE_KEY  # "MAX_SQUEEZE_FLOOR"
_DEF = ai_advisor._PARAM_DEFINITIONS[_KEY]
_RANGE = ai_advisor._PARAM_VALID_RANGES[_KEY]


def test_definition_is_distance_worded() -> None:
    """AC-7: the definition describes a post-squeeze stop DISTANCE floor."""
    text = _DEF["definition"].lower()
    assert "distance" in text, (
        f"MAX_SQUEEZE_FLOOR definition must describe a stop DISTANCE floor, "
        f"got: {_DEF['definition']!r}"
    )


def test_definition_is_not_multiplier_worded() -> None:
    """AC-7: the definition must NOT call the knob a squeeze multiplier."""
    text = _DEF["definition"].lower()
    assert "multiplier" not in text, (
        f"MAX_SQUEEZE_FLOOR definition must not call it a multiplier, got: {_DEF['definition']!r}"
    )


def test_risk_polarity_unchanged() -> None:
    """AC-7 scope pin: risk polarity stays 'raising loosens risk' (a higher
    distance floor widens the stop => looser). The reword must not touch it."""
    assert _DEF["risk_polarity"] == "raising loosens risk", (
        f"risk_polarity must remain 'raising loosens risk', got {_DEF['risk_polarity']!r}."
    )


def test_suggestible_range_unchanged() -> None:
    """AC-7 scope pin: the 0.05–0.50 suggestible range is kept numerically
    (re-tuning is R3-d's job, not R3-c's)."""
    assert _RANGE["low"] == 0.05 and _RANGE["high"] == 0.50, (
        f"MAX_SQUEEZE_FLOOR range must stay 0.05–0.50, got {_RANGE}."
    )
