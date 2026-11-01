"""The detail panel's Vol (20d) hint says what the number is: a daily standard deviation, in percent.

``math_engine.calculate_20d_vol`` returns the population standard deviation of the weighted daily
returns of the last 20 trading days, in percentage points. It is not annualised, yet the hint under the
row said "annualised" while the same panel showed a 36.9% annual volatility elsewhere.

Real call path: the page ``GET /`` renders (tests/dashboard/_index_js_harness.py builds it from the real
route), read as a tree. The truth of the new hint is pinned against the real function on one input whose
answer is known without running it: a single holding of weight 1 whose daily return alternates +1% and
-1% over 20 days has a population standard deviation of exactly 1 percentage point; an annualised
figure would be near 16.

Contract pinned
* The hint under Vol (20d) says daily, says standard deviation, and says percent (the word or the sign).
* The hint does not say annualised or annualized, or "annual" at all.
* The function the hint describes returns the daily percentage-point figure on that input.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from math_engine import LOOKBACK_DAYS, calculate_20d_vol
from tests.dashboard import _index_js_harness as hx


def _hint() -> str:
    tree = hx.parse_html(hx.get_world("default")["html"])
    rows = hx.find_all(tree, lambda n: "math-row" in n["a"].get("class", "").split())
    for row in rows:
        labels = hx.find_all(row, lambda n: "math-row-label" in n["a"].get("class", "").split())
        if labels and " ".join(hx.tree_text(labels[0]).split()) == "Vol (20d)":
            hints = hx.find_all(row, lambda n: "math-row-hint" in n["a"].get("class", "").split())
            assert hints, "the Vol (20d) row has no hint"
            return " ".join(hx.tree_text(hints[0]).split())
    raise AssertionError("the render holds no Vol (20d) row")


def test_the_hint_says_daily_standard_deviation_in_percent():
    hint = _hint()
    assert re.search(r"\bdaily\b", hint, re.I), hint
    assert re.search(r"standard deviation|std", hint, re.I), hint
    assert re.search(r"percent|%|percentage", hint, re.I), hint


def test_the_hint_does_not_call_the_figure_annual():
    hint = _hint()
    assert not re.search(r"annual", hint, re.I), hint


def test_the_function_returns_the_daily_percentage_point_standard_deviation():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(LOOKBACK_DAYS)]
    history = {
        d.isoformat(): {"XYZ": {"daily_ret": 0.01 if i % 2 == 0 else -0.01}}
        for i, d in enumerate(days)
    }
    value = calculate_20d_vol([{"ticker": "XYZ", "allocation": 1.0}], history)
    assert abs(value - 1.0) < 1e-9, (
        f"calculate_20d_vol returned {value}: not the daily standard deviation in percent"
    )
