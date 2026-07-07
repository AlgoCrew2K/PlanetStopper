"""
RED tests — sleeves/envelope.py: the envelope hard box (AC-2, AC-3).

CONTRACT this file specifies for the GREEN implementer (sleeve-risk-impl):

    sleeves/envelope.py

    @dataclass(frozen=True)
    class ClampResult:
        approved: bool          # False => refuse the order outright, qty == 0
        qty: float               # final qty to submit; NEVER > original_qty
        original_qty: float      # qty exactly as requested by the caller
        clamped: bool            # True iff qty != original_qty
        reason: str | None       # one of the REASON_* constants below; set
                                  # whenever clamped is True or approved is False

    Reason-code constants (module-level, importable):
        REASON_NOT_IN_ALLOWLIST
        REASON_MAX_POSITION_PCT
        REASON_MAX_ORDER_USD
        REASON_MAX_DAILY_TURNOVER
        REASON_LONG_ONLY_NO_SHORT
        REASON_REDUCED_TO_ZERO

    def clamp_order(
        *, symbol: str, side: str, qty: float, price: float, envelope: dict,
        sleeve_equity: float, current_position_qty: float = 0.0,
        turnover_used_usd: float = 0.0,
    ) -> ClampResult: ...

    def is_envelope_widened(old_envelope: dict, new_envelope: dict) -> bool:
        # True iff `new_envelope` is LESS restrictive than `old_envelope` in
        # any dimension (new ticker added to allowlist, any cap raised,
        # long_only flipped to allow shorting). Used by the P3 arming route
        # to decide whether the widen-requires-re-ceremony gate (AC-3) fires.

Envelope dict shape (operator-authored, schema-validated elsewhere in P2):

    {
        "allowlist": ["SPY", "QQQ"],
        "max_position_pct": 0.25,        # of sleeve equity, i.e. 0.25 == 25%
        "max_order_usd": 2000.0,
        "max_daily_turnover_usd": 5000.0,
        "long_only": True,               # v1: always True; no margin/shorting
    }

Reduce-only semantics (AC-2): clamp_order NEVER returns qty > original_qty.
Every applicable limit can only shrink the order. If shrinking a limit to
its floor would leave qty <= 0 (nothing left worth sending), the order is
REFUSED (approved=False, qty=0), never silently sent at a larger size.

This file tests sleeves.envelope directly (no mocking of the math) —
per quant-test-writer discipline, never mock the module under test.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("sleeves.envelope", reason="RED phase — sleeves.envelope not implemented yet")

import sleeves.envelope as envelope  # noqa: E402

# ---------------------------------------------------------------------------
# Shared envelope fixture
# ---------------------------------------------------------------------------


def _base_envelope(**overrides) -> dict:
    base = {
        "allowlist": ["SPY", "QQQ"],
        "max_position_pct": 0.25,
        "max_order_usd": 2000.0,
        "max_daily_turnover_usd": 5000.0,
        "long_only": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. Ticker allowlist
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_symbol_in_allowlist_is_not_clamped_for_that_reason(self):
        env = _base_envelope()
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=1,
            price=500.0,
            envelope=env,
            sleeve_equity=10_000.0,
        )
        assert result.reason != envelope.REASON_NOT_IN_ALLOWLIST

    def test_symbol_not_in_allowlist_is_refused(self):
        env = _base_envelope()
        result = envelope.clamp_order(
            symbol="TSLA",  # not in ["SPY", "QQQ"]
            side="buy",
            qty=1,
            price=500.0,
            envelope=env,
            sleeve_equity=10_000.0,
        )
        assert result.approved is False, (
            "an order for a ticker outside the allowlist must be refused outright"
        )
        assert result.qty == 0, "a refused order must clamp qty to 0, never leave a partial size"
        assert result.reason == envelope.REASON_NOT_IN_ALLOWLIST

    def test_allowlist_refusal_never_increases_qty(self):
        env = _base_envelope()
        result = envelope.clamp_order(
            symbol="TSLA",
            side="buy",
            qty=50,
            price=100.0,
            envelope=env,
            sleeve_equity=100_000.0,
        )
        assert result.qty <= 50, "clamp must never increase qty above the requested amount"


# ---------------------------------------------------------------------------
# 2. Max single-position % of sleeve equity
# ---------------------------------------------------------------------------


class TestMaxPositionPct:
    def test_order_within_position_cap_is_unclamped(self):
        env = _base_envelope(max_position_pct=0.50, max_order_usd=1_000_000.0)
        # 2 shares @ 100 = $200 notional against $10,000 equity => 2% << 50% cap
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=2,
            price=100.0,
            envelope=env,
            sleeve_equity=10_000.0,
        )
        assert result.clamped is False
        assert result.qty == 2

    def test_order_exceeding_position_cap_is_reduced_not_refused(self):
        # Requesting a position worth 80% of equity against a 25% cap must be
        # reduced to fit the cap, not refused outright (still tradeable).
        env = _base_envelope(max_position_pct=0.25, max_order_usd=1_000_000.0)
        sleeve_equity = 10_000.0
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=80,  # 80 * 100 = $8000 = 80% of equity
            price=100.0,
            envelope=env,
            sleeve_equity=sleeve_equity,
        )
        max_allowed_notional = env["max_position_pct"] * sleeve_equity  # $2500
        max_allowed_qty = math.floor(max_allowed_notional / 100.0)  # 25 shares
        assert result.clamped is True
        assert result.qty <= max_allowed_qty
        assert result.qty < 80, "qty must be reduced below the originally requested amount"
        assert result.reason == envelope.REASON_MAX_POSITION_PCT

    def test_position_cap_accounts_for_existing_position_qty(self):
        # Sleeve already holds 20 shares; cap allows a total position of 25
        # shares. A new buy order for 20 more must be clamped to 5, not 25,
        # because the existing 20 already consume most of the cap.
        env = _base_envelope(max_position_pct=0.25, max_order_usd=1_000_000.0)
        sleeve_equity = 10_000.0  # cap = $2500 notional = 25 shares @ $100
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=20,
            price=100.0,
            envelope=env,
            sleeve_equity=sleeve_equity,
            current_position_qty=20,
        )
        assert result.qty <= 5, (
            f"existing 20-share position + cap of 25 leaves room for only 5 more; got qty={result.qty}"
        )

    def test_position_cap_already_maxed_out_refuses_new_buy(self):
        env = _base_envelope(max_position_pct=0.25, max_order_usd=1_000_000.0)
        sleeve_equity = 10_000.0  # cap = 25 shares @ $100
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=10,
            price=100.0,
            envelope=env,
            sleeve_equity=sleeve_equity,
            current_position_qty=25,  # already at the cap
        )
        assert result.approved is False
        assert result.qty == 0
        assert result.reason == envelope.REASON_REDUCED_TO_ZERO


# ---------------------------------------------------------------------------
# 3. Per-order dollar cap
# ---------------------------------------------------------------------------


class TestMaxOrderUsd:
    def test_order_under_dollar_cap_is_unclamped(self):
        env = _base_envelope(max_order_usd=5_000.0, max_position_pct=1.0)
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=10,
            price=100.0,  # $1000 notional < $5000 cap
            envelope=env,
            sleeve_equity=100_000.0,
        )
        assert result.clamped is False
        assert result.qty == 10

    def test_order_over_dollar_cap_is_reduced(self):
        env = _base_envelope(max_order_usd=500.0, max_position_pct=1.0)
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=10,
            price=100.0,  # $1000 notional > $500 cap
            envelope=env,
            sleeve_equity=100_000.0,
        )
        max_allowed_qty = math.floor(env["max_order_usd"] / 100.0)  # 5 shares
        assert result.clamped is True
        assert result.qty <= max_allowed_qty
        assert result.reason == envelope.REASON_MAX_ORDER_USD

    def test_dollar_cap_below_one_share_price_refuses(self):
        # A $50 cap against a $100 share price cannot buy even one share.
        env = _base_envelope(max_order_usd=50.0, max_position_pct=1.0)
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=5,
            price=100.0,
            envelope=env,
            sleeve_equity=100_000.0,
        )
        assert result.approved is False
        assert result.qty == 0
        assert result.reason == envelope.REASON_REDUCED_TO_ZERO


# ---------------------------------------------------------------------------
# 4. Max daily turnover cap
# ---------------------------------------------------------------------------


class TestMaxDailyTurnover:
    def test_order_within_remaining_turnover_budget_is_unclamped(self):
        env = _base_envelope(
            max_daily_turnover_usd=5_000.0, max_order_usd=1_000_000.0, max_position_pct=1.0
        )
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=10,
            price=100.0,  # $1000, well within $5000 - $0 used = $5000 remaining
            envelope=env,
            sleeve_equity=100_000.0,
            turnover_used_usd=0.0,
        )
        assert result.clamped is False

    def test_order_exceeding_remaining_turnover_budget_is_reduced(self):
        env = _base_envelope(
            max_daily_turnover_usd=1_000.0, max_order_usd=1_000_000.0, max_position_pct=1.0
        )
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=10,
            price=100.0,  # $1000 requested notional
            envelope=env,
            sleeve_equity=100_000.0,
            turnover_used_usd=800.0,  # only $200 of budget remains
        )
        remaining = env["max_daily_turnover_usd"] - 800.0  # $200
        max_allowed_qty = math.floor(remaining / 100.0)  # 2 shares
        assert result.clamped is True
        assert result.qty <= max_allowed_qty
        assert result.reason == envelope.REASON_MAX_DAILY_TURNOVER

    def test_turnover_budget_fully_consumed_refuses_new_order(self):
        env = _base_envelope(
            max_daily_turnover_usd=1_000.0, max_order_usd=1_000_000.0, max_position_pct=1.0
        )
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=1,
            price=100.0,
            envelope=env,
            sleeve_equity=100_000.0,
            turnover_used_usd=1_000.0,  # fully consumed
        )
        assert result.approved is False
        assert result.qty == 0
        assert result.reason == envelope.REASON_REDUCED_TO_ZERO


# ---------------------------------------------------------------------------
# 5. Long-only / no margin / no shorting
# ---------------------------------------------------------------------------


class TestLongOnly:
    def test_sell_within_existing_position_is_unclamped(self):
        env = _base_envelope(max_order_usd=1_000_000.0, max_position_pct=1.0)
        result = envelope.clamp_order(
            symbol="SPY",
            side="sell",
            qty=5,
            price=100.0,
            envelope=env,
            sleeve_equity=100_000.0,
            current_position_qty=10,
        )
        assert result.clamped is False
        assert result.qty == 5

    def test_sell_exceeding_position_qty_is_clamped_to_position_qty(self):
        # Selling more than held would create a short position — long_only
        # forbids this. Clamp to the held qty (reduce-only), never refuse
        # a legitimate partial-position sell.
        env = _base_envelope(max_order_usd=1_000_000.0, max_position_pct=1.0)
        result = envelope.clamp_order(
            symbol="SPY",
            side="sell",
            qty=20,
            price=100.0,
            envelope=env,
            sleeve_equity=100_000.0,
            current_position_qty=10,
        )
        assert result.clamped is True
        assert result.qty <= 10
        assert result.reason == envelope.REASON_LONG_ONLY_NO_SHORT

    def test_sell_with_zero_position_is_refused(self):
        env = _base_envelope(max_order_usd=1_000_000.0, max_position_pct=1.0)
        result = envelope.clamp_order(
            symbol="SPY",
            side="sell",
            qty=5,
            price=100.0,
            envelope=env,
            sleeve_equity=100_000.0,
            current_position_qty=0,
        )
        assert result.approved is False
        assert result.qty == 0
        assert result.reason in (
            envelope.REASON_LONG_ONLY_NO_SHORT,
            envelope.REASON_REDUCED_TO_ZERO,
        )


# ---------------------------------------------------------------------------
# 6. Reduce-only invariant — clamp NEVER increases qty, across randomized inputs
# ---------------------------------------------------------------------------


class TestReduceOnlyInvariant:
    @pytest.mark.parametrize(
        "qty,price,max_position_pct,max_order_usd,max_daily_turnover_usd,turnover_used",
        [
            (1, 500.0, 1.0, 1_000_000.0, 1_000_000.0, 0.0),
            (100, 10.0, 0.05, 50.0, 100.0, 0.0),
            (1000, 1.0, 0.01, 10.0, 20.0, 5.0),
            (3, 999.0, 0.10, 500.0, 500.0, 400.0),
        ],
    )
    def test_qty_never_exceeds_original_across_parameter_grid(
        self, qty, price, max_position_pct, max_order_usd, max_daily_turnover_usd, turnover_used
    ):
        env = _base_envelope(
            max_position_pct=max_position_pct,
            max_order_usd=max_order_usd,
            max_daily_turnover_usd=max_daily_turnover_usd,
        )
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=qty,
            price=price,
            envelope=env,
            sleeve_equity=10_000.0,
            turnover_used_usd=turnover_used,
        )
        assert result.qty <= qty, (
            f"clamp_order increased qty from {qty} to {result.qty} — reduce-only violated"
        )
        assert result.original_qty == qty

    def test_every_clamp_or_refusal_carries_a_reason(self):
        # A tiny per-order cap forces a clamp; reason must be populated.
        env = _base_envelope(max_order_usd=10.0, max_position_pct=1.0)
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=100,
            price=500.0,
            envelope=env,
            sleeve_equity=100_000.0,
        )
        if result.clamped or not result.approved:
            assert result.reason, "every clamp or refusal must carry a non-empty reason"

    def test_unclamped_pass_through_has_no_reason(self):
        env = _base_envelope(
            max_order_usd=1_000_000.0, max_position_pct=1.0, max_daily_turnover_usd=1_000_000.0
        )
        result = envelope.clamp_order(
            symbol="SPY",
            side="buy",
            qty=1,
            price=100.0,
            envelope=env,
            sleeve_equity=100_000.0,
        )
        assert result.clamped is False
        assert result.approved is True
        assert result.reason is None, "an order that passes through unclamped must have reason=None"


# ---------------------------------------------------------------------------
# 7. Envelope widening detection (AC-3)
# ---------------------------------------------------------------------------


class TestEnvelopeWidening:
    def test_adding_a_ticker_to_allowlist_is_a_widen(self):
        old = _base_envelope(allowlist=["SPY"])
        new = _base_envelope(allowlist=["SPY", "QQQ"])
        assert envelope.is_envelope_widened(old, new) is True

    def test_removing_a_ticker_from_allowlist_is_not_a_widen(self):
        old = _base_envelope(allowlist=["SPY", "QQQ"])
        new = _base_envelope(allowlist=["SPY"])
        assert envelope.is_envelope_widened(old, new) is False

    def test_raising_max_position_pct_is_a_widen(self):
        old = _base_envelope(max_position_pct=0.10)
        new = _base_envelope(max_position_pct=0.50)
        assert envelope.is_envelope_widened(old, new) is True

    def test_lowering_max_position_pct_is_not_a_widen(self):
        old = _base_envelope(max_position_pct=0.50)
        new = _base_envelope(max_position_pct=0.10)
        assert envelope.is_envelope_widened(old, new) is False

    def test_raising_max_order_usd_is_a_widen(self):
        old = _base_envelope(max_order_usd=500.0)
        new = _base_envelope(max_order_usd=5_000.0)
        assert envelope.is_envelope_widened(old, new) is True

    def test_raising_max_daily_turnover_usd_is_a_widen(self):
        old = _base_envelope(max_daily_turnover_usd=500.0)
        new = _base_envelope(max_daily_turnover_usd=5_000.0)
        assert envelope.is_envelope_widened(old, new) is True

    def test_identical_envelopes_are_not_a_widen(self):
        old = _base_envelope()
        new = _base_envelope()
        assert envelope.is_envelope_widened(old, new) is False

    def test_flipping_long_only_off_is_a_widen(self):
        old = _base_envelope(long_only=True)
        new = _base_envelope(long_only=False)
        assert envelope.is_envelope_widened(old, new) is True
