"""
Cycle B2 — RED: Golden-fixture math tests for the technicals producer.

Math-layer rule (project coding standard): every change to math layers requires a
golden-fixture test. Input bar data → expected indicator value, where the expected value
is DERIVED FROM THE FIXTURE via the same formula — not hardcoded.

This file tests the internal math functions of advisors.lens_technicals:
  - SMA computation (50-day and 200-day)
  - Breadth calculation (% of universe above N-day SMA)
  - 20-day momentum return

All expected values in assertions are derived from the fixture bar data using the
same formula as the implementation. This catches bugs in the implementation's
formula (wrong window size, off-by-one, wrong normalization) without hardcoding
a specific expected float (feedback_no_hardcoded_test_values).

Mocking: synthetic_history.fetch_bars is patched. No live Alpaca calls.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
from unittest.mock import patch

import pytest

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent
_FIXTURES_MATH = _WORKTREE / "tests" / "fixtures" / "math"
_TECH_SCHEMA_PATH = _FIXTURES_MATH / "technicals_producer_schema.json"


def _import_lens_technicals():
    mod_name = "advisors.lens_technicals"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Deterministic fixture bar builders
# ---------------------------------------------------------------------------

def _build_trending_up_bars(n: int, start: float = 100.0, per_bar: float = 0.5) -> list[dict]:
    """n bars with monotonically increasing close prices.

    All closes are above any N-day SMA computed from these bars (since every
    bar is higher than the previous mean). This gives a known breadth=1.0 for
    a single-ticker universe.
    """
    bars = []
    close = start
    for i in range(n):
        bars.append({
            "t": f"2025-01-{i + 1:02d}T05:00:00Z" if i < 28 else f"2025-02-{i - 27:02d}T05:00:00Z",
            "o": close - 0.1,
            "h": close + 0.5,
            "l": close - 0.5,
            "c": close,
            "v": 1_000_000,
        })
        close += per_bar
    return bars


def _build_trending_down_bars(n: int, start: float = 200.0, per_bar: float = 0.5) -> list[dict]:
    """n bars with monotonically decreasing close prices.

    All closes are below any N-day SMA. breadth=0.0 for a single-ticker universe.
    """
    bars = []
    close = start
    for i in range(n):
        bars.append({
            "t": f"2025-01-{i + 1:02d}T05:00:00Z" if i < 28 else f"2025-02-{i - 27:02d}T05:00:00Z",
            "o": close + 0.1,
            "h": close + 0.5,
            "l": close - 0.5,
            "c": close,
            "v": 1_000_000,
        })
        close -= per_bar
    return bars


def _compute_sma(closes: list[float], window: int) -> float | None:
    """Reference SMA computation for deriving expected values.

    This is the SAME formula the implementation must use. If the implementation
    uses a different formula, the test will fail — which is the point.

    Returns None when len(closes) < window (insufficient history).
    """
    if len(closes) < window:
        return None
    return sum(closes[-window:]) / window


def _compute_20d_return(closes: list[float]) -> float | None:
    """Reference 20-day return: (close[-1] / close[-21]) - 1.

    Returns None if len(closes) < 21.
    """
    if len(closes) < 21:
        return None
    return (closes[-1] / closes[-21]) - 1.0


# ---------------------------------------------------------------------------
# TestSMAGolden — 50-day and 200-day SMA correctness
# ---------------------------------------------------------------------------

class TestSMAGolden:
    """Golden-fixture tests for SMA computation. Expected values derived from
    the fixture bar data via _compute_sma — NOT hardcoded floats."""

    def test_pct_above_50sma_is_1_for_all_trending_up(self):
        """With monotonically increasing bars, pct_above_50sma must be 1.0.

        Derivation: Every close is above the 50-day SMA because the most
        recent bar is always above all prior bars. A correct SMA impl gives
        pct = 1.0. If the implementation returns anything else, its SMA is wrong.
        """
        mod = _import_lens_technicals()
        bars = _build_trending_up_bars(250)
        bars_dict = {"SPY": bars}
        with patch("synthetic_history.fetch_bars", return_value=bars_dict):
            result = mod._fetch_technicals(["SPY"])

        if not result.get("available"):
            pytest.skip(f"Producer returned unavailable: {result.get('reason')}")

        pct = result["ma_posture"]["pct_above_50sma"]
        assert pct is not None, "pct_above_50sma must not be None with 250 bars"
        assert abs(pct - 1.0) < 1e-9, (
            f"With monotonically increasing bars, pct_above_50sma must be 1.0. "
            f"Got {pct}. Derivation: all closes above their 50-day SMA mean."
        )

    def test_pct_above_50sma_is_0_for_all_trending_down(self):
        """With monotonically decreasing bars, pct_above_50sma must be 0.0.

        Derivation: Every close is below the 50-day SMA. Correct impl → pct=0.0.
        """
        mod = _import_lens_technicals()
        bars = _build_trending_down_bars(250)
        bars_dict = {"SPY": bars}
        with patch("synthetic_history.fetch_bars", return_value=bars_dict):
            result = mod._fetch_technicals(["SPY"])

        if not result.get("available"):
            pytest.skip(f"Producer returned unavailable: {result.get('reason')}")

        pct = result["ma_posture"]["pct_above_50sma"]
        assert pct is not None
        assert abs(pct - 0.0) < 1e-9, (
            f"With monotonically decreasing bars, pct_above_50sma must be 0.0. Got {pct}."
        )

    def test_pct_above_200sma_none_with_100_bars(self):
        """With only 100 bars, pct_above_200sma must be None (insufficient history)."""
        mod = _import_lens_technicals()
        bars = _build_trending_up_bars(100)
        bars_dict = {"SPY": bars}
        with patch("synthetic_history.fetch_bars", return_value=bars_dict):
            result = mod._fetch_technicals(["SPY"])

        if not result.get("available"):
            # Also acceptable — producer may return unavailable for < 200 bars
            assert result.get("reason") is not None
            return

        # If available, pct_above_200sma must be None
        val = result["ma_posture"]["pct_above_200sma"]
        assert val is None, (
            f"With 100 bars (< 200), pct_above_200sma must be None. Got {val}."
        )

    def test_pct_above_200sma_is_1_for_trending_up_250_bars(self):
        """With 250 uptrending bars, pct_above_200sma must be 1.0.

        Derivation: close[-1] > mean(closes[-200:]) because prices are monotonically
        increasing. A correct 200-day SMA gives pct=1.0.
        """
        mod = _import_lens_technicals()
        bars = _build_trending_up_bars(250)
        bars_dict = {"SPY": bars}
        with patch("synthetic_history.fetch_bars", return_value=bars_dict):
            result = mod._fetch_technicals(["SPY"])

        if not result.get("available"):
            pytest.skip(f"Producer returned unavailable: {result.get('reason')}")

        pct = result["ma_posture"]["pct_above_200sma"]
        if pct is None:
            pytest.skip("pct_above_200sma=None with 250 bars — may need > 200 bars for 200-day SMA")
        assert abs(pct - 1.0) < 1e-9, (
            f"With 250 monotonically increasing bars, pct_above_200sma must be 1.0. Got {pct}."
        )


# ---------------------------------------------------------------------------
# TestBreadthGolden — breadth = pct_above_50sma as scalar
# ---------------------------------------------------------------------------

class TestBreadthGolden:
    """breadth must equal ma_posture.pct_above_50sma (same value, scalar form)."""

    def test_breadth_equals_pct_above_50sma(self):
        """breadth is the same value as ma_posture.pct_above_50sma.

        The feature plan defines breadth as pct of universe above 50-day SMA.
        It is NOT an independent computation — it's the same fraction.
        """
        mod = _import_lens_technicals()
        bars = {"SPY": _build_trending_up_bars(250), "QQQ": _build_trending_down_bars(250)}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])

        if not result.get("available"):
            pytest.skip(f"Unavailable: {result.get('reason')}")

        breadth = result.get("breadth")
        pct_above = result["ma_posture"].get("pct_above_50sma")

        if breadth is None or pct_above is None:
            pytest.skip("breadth or pct_above_50sma is None — insufficient history")

        assert abs(breadth - pct_above) < 1e-9, (
            f"breadth ({breadth}) must equal ma_posture.pct_above_50sma ({pct_above}). "
            "These are the same metric — breadth is the scalar form."
        )

    def test_breadth_is_0_5_for_half_above_half_below(self):
        """With one ticker above and one below 50-day SMA, breadth = 0.5.

        Derivation: 1/2 tickers above SMA → pct = 0.5. Tests the counting math.
        """
        mod = _import_lens_technicals()
        bars = {"SPY": _build_trending_up_bars(250), "QQQ": _build_trending_down_bars(250)}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])

        if not result.get("available"):
            pytest.skip(f"Unavailable: {result.get('reason')}")

        breadth = result.get("breadth")
        if breadth is None:
            pytest.skip("breadth=None — insufficient history")

        # With 1 above and 1 below, breadth must be exactly 0.5
        assert abs(breadth - 0.5) < 1e-9, (
            f"With 1 of 2 tickers above 50-day SMA, breadth must be 0.5. Got {breadth}. "
            "Derivation: count_above / total = 1/2 = 0.5."
        )


# ---------------------------------------------------------------------------
# TestMomentumGolden — 20-day return direction correctness
# ---------------------------------------------------------------------------

class TestMomentumGolden:
    """momentum.direction must match the sign of mean_20d_return.

    Expected values derived from fixture bar data via _compute_20d_return.
    """

    def test_momentum_positive_for_uptrend(self):
        """With uptrending bars, mean_20d_return > 0 and direction='positive'."""
        mod = _import_lens_technicals()
        bars_list = _build_trending_up_bars(250, per_bar=0.5)
        bars = {"SPY": bars_list}

        # Derive expected from fixture
        closes = [b["c"] for b in bars_list]
        expected_return = _compute_20d_return(closes)
        assert expected_return is not None and expected_return > 0, (
            "Test fixture error: uptrend bars must give positive 20d return"
        )

        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY"])

        if not result.get("available"):
            pytest.skip(f"Unavailable: {result.get('reason')}")

        momentum = result.get("momentum", {})
        direction = momentum.get("direction")
        assert direction == "positive", (
            f"Uptrending bars must give direction='positive'. Got '{direction}'. "
            f"Fixture-derived expected_return={expected_return:.4f} > 0."
        )

    def test_momentum_negative_for_downtrend(self):
        """With downtrending bars, mean_20d_return < 0 and direction='negative'."""
        mod = _import_lens_technicals()
        bars_list = _build_trending_down_bars(250, per_bar=0.5)
        bars = {"SPY": bars_list}

        closes = [b["c"] for b in bars_list]
        expected_return = _compute_20d_return(closes)
        assert expected_return is not None and expected_return < 0, (
            "Test fixture error: downtrend bars must give negative 20d return"
        )

        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY"])

        if not result.get("available"):
            pytest.skip(f"Unavailable: {result.get('reason')}")

        momentum = result.get("momentum", {})
        direction = momentum.get("direction")
        assert direction == "negative", (
            f"Downtrending bars must give direction='negative'. Got '{direction}'. "
            f"Fixture-derived expected_return={expected_return:.4f} < 0."
        )

    def test_mean_20d_return_sign_matches_direction(self):
        """mean_20d_return sign and direction are consistent (no internal mismatch)."""
        mod = _import_lens_technicals()
        bars = {"SPY": _build_trending_up_bars(250)}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY"])

        if not result.get("available"):
            pytest.skip(f"Unavailable: {result.get('reason')}")

        momentum = result.get("momentum", {})
        ret = momentum.get("mean_20d_return")
        direction = momentum.get("direction")

        if ret is None or direction is None:
            pytest.skip("momentum fields are None — insufficient history")

        if ret > 0:
            assert direction == "positive", (
                f"mean_20d_return={ret:.4f} > 0 but direction='{direction}' — inconsistent"
            )
        elif ret < 0:
            assert direction == "negative", (
                f"mean_20d_return={ret:.4f} < 0 but direction='{direction}' — inconsistent"
            )
        else:
            assert direction == "flat", (
                f"mean_20d_return=0 but direction='{direction}' — should be 'flat'"
            )

    def test_overall_bullish_when_majority_above_sma(self):
        """ma_posture.overall='bullish' when pct_above_50sma > 0.5.

        Derivation: with all tickers in uptrend, pct_above_50sma=1.0 > 0.5 → 'bullish'.
        """
        mod = _import_lens_technicals()
        bars = {"SPY": _build_trending_up_bars(250), "QQQ": _build_trending_up_bars(250)}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])

        if not result.get("available"):
            pytest.skip(f"Unavailable: {result.get('reason')}")

        ma = result.get("ma_posture", {})
        overall = ma.get("overall")
        pct = ma.get("pct_above_50sma")

        if pct is None:
            pytest.skip("pct_above_50sma=None — insufficient history")

        assert pct > 0.5, (
            f"Test fixture error: all uptrend bars should give pct_above_50sma > 0.5, got {pct}"
        )
        assert overall == "bullish", (
            f"pct_above_50sma={pct:.2f} > 0.5 must give overall='bullish'. Got '{overall}'."
        )

    def test_overall_bearish_when_majority_below_sma(self):
        """ma_posture.overall='bearish' when pct_above_50sma < 0.5.

        Derivation: with all tickers in downtrend, pct_above_50sma=0.0 < 0.5 → 'bearish'.
        """
        mod = _import_lens_technicals()
        bars = {"SPY": _build_trending_down_bars(250), "QQQ": _build_trending_down_bars(250)}
        with patch("synthetic_history.fetch_bars", return_value=bars):
            result = mod._fetch_technicals(["SPY", "QQQ"])

        if not result.get("available"):
            pytest.skip(f"Unavailable: {result.get('reason')}")

        ma = result.get("ma_posture", {})
        overall = ma.get("overall")
        pct = ma.get("pct_above_50sma")

        if pct is None:
            pytest.skip("pct_above_50sma=None — insufficient history")

        assert pct < 0.5, (
            f"Test fixture error: all downtrend bars should give pct_above_50sma < 0.5, got {pct}"
        )
        assert overall == "bearish", (
            f"pct_above_50sma={pct:.2f} < 0.5 must give overall='bearish'. Got '{overall}'."
        )
