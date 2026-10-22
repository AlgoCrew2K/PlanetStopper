"""``analytics.js_to_fixed`` rounds exactly as the browser's ``Number.prototype.toFixed``.

The dashboard writes some text on the server and rewrites it in the browser.  Python's
own formatting (``round``, ``%.Nf``, f-strings) rounds exact binary ties half to even,
the browser rounds them away from zero, so first-paint text would change on the first
poll.  One Python function, ``analytics.js_to_fixed``, carries the browser's rule.

Truth source: node evaluates ``toFixed`` for every input; nothing below types an
expected string.  Inputs are derived values chosen for the cases the rule distinguishes
(exact ties of each sign at each place count, values just either side of a tie, values
that print as a tie but are not one in binary, negative values that round to zero,
integers and large magnitudes).
"""

from __future__ import annotations

import subprocess

import pytest

import analytics
from tests.dashboard._first_paint_dom import js_to_fixed as node_to_fixed

# Exact binary ties at 0, 1 and 2 places, both signs.
_TIES = [0.5, 1.5, 2.5, -0.5, -1.5, -2.5, 0.25, 0.75, 1.25, -0.25, -0.75, 0.125, -0.125, 0.375]
# Decimal literals that look like ties and are not (their binary value is just above
# or below), so a round-half-up on the repr text would differ from the exact rule.
_LOOKALIKES = [0.005, -0.005, 0.015, 1.005, 2.675, 8.345, 0.045, -0.045, 1.115]
# Just either side of a tie, near zero, and values that round to a signed zero.
_EDGES = [0.0, 0.04, -0.04, 0.049999, -0.049999, 0.0001, -0.0001, 1e-7, -1e-7, 0.095, 0.995]
# Integers, and magnitudes well above the double's integer-exact range of fractions.
_BIG = [3, -3, 100, 94.25, 47.125, 123456789.125, 1e15, 1e20]

_VALUES = _TIES + _LOOKALIKES + _EDGES + _BIG
_PLACES = [0, 1, 2, 3]


@pytest.fixture(scope="module")
def node_expected() -> dict:
    return {places: node_to_fixed(_VALUES, places) for places in _PLACES}


@pytest.mark.parametrize("places", _PLACES)
def test_matches_the_browsers_rounding_for_every_input_at_each_place_count(node_expected, places):
    mismatches = [
        (v, analytics.js_to_fixed(v, places), expected)
        for v, expected in zip(_VALUES, node_expected[places], strict=True)
        if analytics.js_to_fixed(v, places) != expected
    ]
    assert not mismatches, f"(value, ours, browser) at {places} places: {mismatches}"


@pytest.mark.parametrize("value", [0.125, -0.125, 0.005, -0.005])
def test_two_place_ties_round_away_from_zero_not_half_to_even(value):
    """The named cases: a half-to-even implementation prints 0.12 for 0.125 where the
    browser prints 0.13."""
    expected = node_to_fixed([value], 2)[0]
    assert analytics.js_to_fixed(value, 2) == expected


def test_a_literal_negative_zero_prints_without_a_sign_as_the_browser_does():
    negative_zero = -0.0
    expected = node_to_fixed([negative_zero], 2)[0]
    assert expected == "0.00", "premise: the browser prints a literal negative zero unsigned"
    assert analytics.js_to_fixed(negative_zero, 2) == expected


def test_a_small_negative_value_keeps_its_sign_through_rounding_to_zero():
    value = -0.04
    assert analytics.js_to_fixed(value, 1) == node_to_fixed([value], 1)[0]


def test_returns_a_string_with_exactly_the_requested_places():
    assert isinstance(analytics.js_to_fixed(1.0, 2), str)
    assert analytics.js_to_fixed(1.0, 0) == node_to_fixed([1.0], 0)[0]
    assert analytics.js_to_fixed(7, 3) == node_to_fixed([7], 3)[0]


def test_every_rounding_of_shared_text_goes_through_the_one_function():
    """One Python implementation exists: no second mirror of the browser's rule in
    production code (a copy drifts)."""
    tracked = subprocess.run(
        ["git", "ls-files", "*.py"], capture_output=True, text=True, timeout=60, check=True
    ).stdout.split()
    production = [p for p in tracked if not p.startswith(("tests/", ".claude/"))]
    holders = []
    for path in production:
        with open(path, encoding="utf-8", errors="replace") as handle:
            if "ROUND_HALF_UP" in handle.read():
                holders.append(path)
    assert holders == ["analytics.py"], (
        f"ROUND_HALF_UP must appear in analytics.py only (the one implementation); found {holders}"
    )
