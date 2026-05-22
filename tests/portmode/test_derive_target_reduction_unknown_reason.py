"""
RED tests — Cluster 6 AC-5: port_aggregator._derive_target_reduction must fail
SAFE on an unknown or None triggered_reason.

Audit provenance:
  invariant-coverage__2026-05-21.md MEDIUM-3 "_derive_target_reduction unknown
  triggered_reason is untested" — added to the binding AC-1 enumeration by
  risk-engine-specialist's pre-review cross-check.

This is a COVERAGE gap, not a known bug: `_derive_target_reduction` currently
fails safe (an unrecognized triggered_reason falls through to the ticker_drawdowns
fallback and returns [] when no drawdowns are supplied — no KeyError, no
exception). But NO test pins that safe behavior, so a future refactor that
introduced a dict-lookup on triggered_reason — e.g. `REDUCTION_RULES[reason]` —
would KeyError on the live path with no failing test to catch it.

These tests freeze the fail-safe contract: an unknown / None triggered_reason
must produce a defined, exception-free result. They CAN fail on a wrong
implementation (one that raises on an unknown reason) — that is the point.

No magic numbers — the only literals are the adversarial unknown-reason strings
themselves and a clearly-fixtured ticker_drawdowns amount.
"""

from __future__ import annotations

import pytest

from port_aggregator import _derive_target_reduction, build_port_signal


class TestDeriveTargetReductionFailsSafeOnUnknownReason:
    """An unrecognized triggered_reason must not raise and must produce a
    defined reduction list."""

    @pytest.mark.parametrize(
        "unknown_reason",
        ["__unknown__", "not_a_real_trigger", "HWM_TRAILING_STOP_TYPO", ""],
    )
    def test_unknown_reason_with_empty_state_returns_empty_list(self, unknown_reason):
        """No ticker_drawdowns, no peak/current exposures, unknown reason ->
        an empty reduction list, NOT a KeyError or other exception."""
        try:
            result = _derive_target_reduction({}, unknown_reason)
        except Exception as exc:  # noqa: BLE001 - the test's whole point
            pytest.fail(
                f"_derive_target_reduction raised {type(exc).__name__} on an "
                f"unknown triggered_reason {unknown_reason!r}: {exc} — it must "
                f"fail safe with an empty reduction list"
            )
        assert result == [], (
            f"unknown reason {unknown_reason!r} with no drawdown data must yield "
            f"an empty reduction list; got {result}"
        )

    def test_none_reason_with_empty_state_returns_empty_list(self):
        """A None triggered_reason must also fail safe -> empty list."""
        try:
            result = _derive_target_reduction({}, None)
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"_derive_target_reduction raised {type(exc).__name__} on a None "
                f"triggered_reason: {exc} — it must fail safe"
            )
        assert result == []

    def test_unknown_reason_with_ticker_drawdowns_uses_fallback(self):
        """When an unknown reason is paired with a ticker_drawdowns dict, the
        fallback branch must still produce a well-formed reduction list — each
        item carries ticker, a positive amount_usd, and the (unknown) reason."""
        drawdown_amount = 100.0  # fixtured input, not a producer-computed value
        port_state = {"ticker_drawdowns": {"AAPL": drawdown_amount}}
        result = _derive_target_reduction(port_state, "__unknown__")
        assert isinstance(result, list) and len(result) == 1, (
            "an unknown reason with ticker_drawdowns must still emit a reduction "
            "via the fallback branch"
        )
        item = result[0]
        assert item["ticker"] == "AAPL"
        assert item["amount_usd"] == pytest.approx(drawdown_amount, rel=1e-9)
        # The reason is carried through verbatim — the fallback does not invent one.
        assert item["reason_for_this_ticker"] == "__unknown__"

    def test_unknown_reason_drops_non_positive_drawdowns(self):
        """The fallback must drop zero/negative ticker_drawdowns — a reduction
        of <= 0 is not actionable. Property: every emitted amount_usd > 0."""
        port_state = {
            "ticker_drawdowns": {"AAPL": 50.0, "MSFT": 0.0, "NVDA": -10.0}
        }
        result = _derive_target_reduction(port_state, "some_unknown_reason")
        assert all(item["amount_usd"] > 0.0 for item in result), (
            "every reduction amount must be strictly positive; non-positive "
            "drawdowns must be dropped"
        )
        tickers = {item["ticker"] for item in result}
        assert tickers == {"AAPL"}, (
            "only the positive-drawdown ticker may appear in the reduction list"
        )


class TestBuildPortSignalSurvivesUnknownReason:
    """The public build_port_signal entry point must also not crash when a
    triggered signal carries an unrecognized triggered_reason."""

    def test_build_port_signal_with_unknown_triggered_reason_does_not_raise(self):
        port_state = {
            "triggered": True,
            "triggered_reason": "__unknown_trigger_family__",
            # no ticker_drawdowns / exposures -> fail-safe empty reduction
        }
        try:
            signal = build_port_signal(port_state, cycle_id="20260522_1500")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"build_port_signal raised {type(exc).__name__} on an unknown "
                f"triggered_reason: {exc}"
            )
        assert signal["triggered"] is True
        assert signal["triggered_reason"] == "__unknown_trigger_family__"
        assert signal["target_reduction"] == [], (
            "an unknown trigger family with no drawdown data must yield an empty "
            "target_reduction, not raise"
        )
        assert signal["port_total_reduction_usd"] == pytest.approx(0.0, abs=1e-12)
