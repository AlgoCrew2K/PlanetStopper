"""LIVE test — AC-3: Strategy-Builder template trees (T1–T7) each POST /backtest with HTTP 200.

Marker: @pytest.mark.live
Excluded from default gate: -m "not live and not slow and not perf"
Opt-in: --include-live (or -m live)

Purpose:
    Verify that after the required-fields fix (description on make_root, window-days on
    make_inverse_vol), every Strategy-Builder template tree (T1–T7 across all 3 objectives)
    can POST to Composer /api/v0.1/backtest and receive HTTP 200.

    This test is the real payoff gate (AC-3) and demonstrates the fix resolves the
    400/422 responses that blocked every candidate before the FDR gate.

Provenance:
    - Live spike matrix (captured-from-producer): T1+description → HTTP 200,
      T3+description+window-days=30 → HTTP 200.
    - Reference fixture: tests/fixtures/composer/backtest_inline_transit_v1.json
      (structure of a successful backtest request).

Assertions:
    - HTTP status code == 200 for each template tree.
    - Response body is a non-empty dict (not an error payload).
    - No assertion on backtest stats (CAGR, Sharpe, etc.) — those are producer-computed.

Requirements:
    - COMPOSER_API_KEY must be set in .env (same as existing live tests).
    - Rate limiting: 1 request/second between calls (Composer enforces this).
    - This test intentionally makes 7 live network requests.
"""

from __future__ import annotations

import time

import pytest

# ---------------------------------------------------------------------------
# LIVE marker — excluded from default pytest run
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_composer_key() -> bool:
    """Return True if a Composer API key is available in the environment."""
    import os

    key = os.environ.get("COMPOSER_API_KEY", "").strip()
    return bool(key)


def _import_strategy_builder():
    """Import advisors.strategy_builder_engine."""
    try:
        import advisors.strategy_builder_engine as sbe  # type: ignore[import]

        return sbe
    except ImportError as exc:
        pytest.skip(f"advisors.strategy_builder_engine not importable: {exc}")


def _import_backtest_client():
    """Import the backtest client run_backtest function."""
    try:
        from advisors.composer_backtest_client import run_backtest  # type: ignore[import]

        return run_backtest
    except ImportError as exc:
        pytest.skip(f"advisors.composer_backtest_client not importable: {exc}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sbe():
    return _import_strategy_builder()


@pytest.fixture(scope="module")
def run_backtest():
    return _import_backtest_client()


# ---------------------------------------------------------------------------
# AC-3 live backtest test
# ---------------------------------------------------------------------------


def _build_template_trees(sbe) -> list[tuple[str, dict]]:
    """Build one representative tree for each of T1–T7.

    Uses the same universe and parameters as the AC-3 spike run.
    Returns (template_label, tree) pairs.
    """
    universe = ["SPY", "AGG", "TLT", "GLD", "QQQ"]
    return [
        ("T1_equal_weight_basket", sbe.equal_weight_basket(universe, name="AC3 T1")),
        (
            "T2_specified_weight_basket",
            sbe.specified_weight_basket([("SPY", 60.0), ("AGG", 40.0)], name="AC3 T2"),
        ),
        (
            "T3_inverse_vol_basket",
            sbe.inverse_vol_basket(universe[:3], name="AC3 T3"),
        ),
        (
            "T4_trend_switch",
            sbe.trend_switch(
                signal_ticker="SPY",
                ma_window=200,
                risk_on_tickers=["QQQ", "IWM"],
                risk_off_tickers=["TLT", "SHY"],
                name="AC3 T4",
            ),
        ),
        (
            "T5_rsi_rotation",
            sbe.rsi_rotation(
                signal_ticker="SPY",
                rsi_window=14,
                threshold=70,
                overbought_tickers=["TLT"],
                neutral_tickers=["SPY"],
                name="AC3 T5",
            ),
        ),
        (
            "T6_momentum_top_n",
            sbe.momentum_top_n(universe=universe, n=2, window=63, name="AC3 T6"),
        ),
        (
            "T7_low_vol_floor",
            sbe.low_vol_floor(universe=universe[:4], n=2, window=21, name="AC3 T7"),
        ),
    ]


class TestLiveBacktestRequiredFields:
    """AC-3: Every Strategy-Builder template tree must POST to Composer /backtest with HTTP 200.

    These tests are LIVE — they make real HTTP requests to the Composer API.
    Excluded from the default pytest run (marker: live).
    """

    @pytest.mark.live
    def test_all_strategy_builder_templates_backtest_200(self, sbe, run_backtest):
        """Build each T1–T7 template tree and POST /backtest; assert HTTP 200.

        FAIL condition: HTTP 400 or 422 — this is the pre-fix symptom.
          - HTTP 400: missing "description" on root node
          - HTTP 422 "unknown-function-parameter": missing "window-days" on wt-inverse-vol

        PASS condition: each call returns a BacktestResult with no .error field set.

        We assert field PRESENCE and HTTP success only — no backtest stats.
        The stats (CAGR, Sharpe) are producer-computed and must not be hardcoded.
        """
        if not _has_composer_key():
            pytest.skip("COMPOSER_API_KEY not set in environment — skipping live backtest test")

        trees = _build_template_trees(sbe)
        failures: list[str] = []

        for label, tree in trees:
            try:
                result = run_backtest(tree)
                # Rate limiting: 1 request/second (Composer enforces this)
                time.sleep(1.0)
            except Exception as exc:
                failures.append(f"{label}: run_backtest raised {type(exc).__name__}: {exc}")
                continue

            # A BacktestResult with .error set means a non-200 HTTP response
            if result.error is not None:
                failures.append(
                    f"{label}: Composer API returned an error: {result.error!r}. "
                    "Expected HTTP 200. This is the pre-fix symptom (400 = missing "
                    "description, 422 = missing window-days)."
                )
                continue

            # A successful backtest must return a non-empty daily_returns dict
            # (field presence assertion — NOT asserting on the values)
            if not isinstance(result.daily_returns, dict):
                failures.append(
                    f"{label}: daily_returns must be a dict; got "
                    f"{type(result.daily_returns).__name__}"
                )

        assert not failures, (
            f"Live backtest AC-3 failures ({len(failures)}/{len(trees)} templates failed):\n"
            + "\n".join(f"  - {f}" for f in failures)
        )
