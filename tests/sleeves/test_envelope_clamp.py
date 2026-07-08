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


# ---------------------------------------------------------------------------
# 8. Review finding BLOCK #1 (sleeve-review, commit 2200c66): the allowlist
# gate must never block an EXIT. AC-3 narrowing takes effect immediately;
# AC-10's "protective exits are never blocked" precedence principle applies
# to the envelope just as it does to pacing/benching. An operator removing a
# ticker from the allowlist while the sleeve still holds a position in it
# must not trap that position -- the only path to flatten it is a sell
# through clamp_order, and REASON_NOT_IN_ALLOWLIST must never be the reason
# a sell is refused.
# ---------------------------------------------------------------------------


class TestAllowlistNeverBlocksExits:
    def test_sell_of_held_position_not_in_allowlist_is_not_refused_for_allowlist_reason(self):
        env = _base_envelope(allowlist=["SPY"])  # TSLA is NOT on the allowlist
        result = envelope.clamp_order(
            symbol="TSLA",
            side="sell",
            qty=10,
            price=200.0,
            envelope=env,
            sleeve_equity=100_000.0,
            current_position_qty=10,
        )
        assert result.reason != envelope.REASON_NOT_IN_ALLOWLIST, (
            "a sell order for a symbol the sleeve currently holds must never be refused "
            "via REASON_NOT_IN_ALLOWLIST -- the allowlist gates entries only, never exits. "
            "Trapping a position an operator can no longer buy (narrowed allowlist) but "
            "also cannot sell is a stuck-exposure defect."
        )
        assert result.approved is True
        assert result.qty == 10

    def test_sell_of_delisted_symbol_still_respects_position_qty_cap(self):
        # The allowlist bypass for exits must not become a bypass for the
        # long-only/no-short cap -- a sell of a delisted symbol is still
        # clamped to current_position_qty, just not refused for allowlist.
        env = _base_envelope(allowlist=["SPY"])
        result = envelope.clamp_order(
            symbol="TSLA",
            side="sell",
            qty=25,
            price=200.0,
            envelope=env,
            sleeve_equity=100_000.0,
            current_position_qty=10,
        )
        assert result.qty <= 10
        assert result.reason != envelope.REASON_NOT_IN_ALLOWLIST

    def test_buy_of_symbol_not_in_allowlist_is_still_refused(self):
        # Regression guard: fixing the sell-side bypass must not accidentally
        # also stop gating entries -- a BUY for a delisted/never-listed
        # symbol must still be refused via REASON_NOT_IN_ALLOWLIST.
        env = _base_envelope(allowlist=["SPY"])
        result = envelope.clamp_order(
            symbol="TSLA",
            side="buy",
            qty=10,
            price=200.0,
            envelope=env,
            sleeve_equity=100_000.0,
        )
        assert result.approved is False
        assert result.reason == envelope.REASON_NOT_IN_ALLOWLIST


# ---------------------------------------------------------------------------
# 8b. PM ruling (2026-07-08, real paper-smoke defect #34): an EMPTY or ABSENT
# allowlist means NO ticker confinement, not deny-all. `symbol not in []` is
# always True, so the prior behavior refused every single buy for a
# default/simple sleeve authored with no allowlist -- a done-bar-blocking
# footgun the operator's live paper smoke caught (an armed entry rule could
# never trade its own rule's symbol). The rule's own when.symbol plus the
# dollar/position/turnover caps are the real money-safety bounds; the
# allowlist is an OPTIONAL additional ticker-confinement layer, not a
# mandatory allowlist-or-nothing gate. A NON-EMPTY allowlist continues to
# confine exactly as before (TestAllowlist/TestAllowlistNeverBlocksExits
# above are unchanged and re-pinned here for visibility alongside the fix).
# ---------------------------------------------------------------------------


class TestEmptyOrAbsentAllowlistMeansNoConfinement:
    def test_empty_allowlist_allows_a_buy_for_any_symbol(self):
        env = _base_envelope(allowlist=[])
        result = envelope.clamp_order(
            symbol="AAPL",
            side="buy",
            qty=1,
            price=100.0,
            envelope=env,
            sleeve_equity=10_000.0,
        )
        assert result.reason != envelope.REASON_NOT_IN_ALLOWLIST, (
            "an EMPTY allowlist must mean NO ticker confinement -- refusing "
            "every buy for an empty allowlist is the exact done-bar defect "
            "the PM's real paper smoke found (a default sleeve authored with "
            "no allowlist could never trade its own rule's symbol)."
        )
        assert result.approved is True

    def test_absent_allowlist_key_allows_a_buy_for_any_symbol(self):
        env = _base_envelope()
        del env["allowlist"]
        result = envelope.clamp_order(
            symbol="AAPL",
            side="buy",
            qty=1,
            price=100.0,
            envelope=env,
            sleeve_equity=10_000.0,
        )
        assert result.reason != envelope.REASON_NOT_IN_ALLOWLIST, (
            "an ABSENT allowlist key must behave identically to an empty "
            "allowlist -- no ticker confinement, matching clamp_order's own "
            "None-means-unlimited convention already used for every other cap."
        )
        assert result.approved is True

    def test_empty_allowlist_buy_is_still_subject_to_other_caps(self):
        # No ticker confinement must not mean no money-safety bounds at all
        # -- the dollar/position/turnover caps still apply exactly as today.
        env = _base_envelope(
            allowlist=[],
            max_order_usd=500.0,
            max_position_pct=1.0,
            max_daily_turnover_usd=1_000_000.0,
        )
        result = envelope.clamp_order(
            symbol="AAPL",
            side="buy",
            qty=10,  # 10 * 100 = $1000, exceeds the $500 max_order_usd cap
            price=100.0,
            envelope=env,
            sleeve_equity=100_000.0,
        )
        assert result.reason != envelope.REASON_NOT_IN_ALLOWLIST
        assert result.clamped is True
        assert result.reason == envelope.REASON_MAX_ORDER_USD, (
            "an empty allowlist must not bypass the OTHER caps -- this buy still "
            "needed clamping down to the $500 max_order_usd limit"
        )
        assert result.qty <= 5  # $500 / $100 = 5 shares, the real cap-derived ceiling

    def test_nonempty_allowlist_still_confines_symbols_outside_it(self):
        env = _base_envelope(allowlist=["SPY"])
        result = envelope.clamp_order(
            symbol="AAPL",
            side="buy",
            qty=1,
            price=100.0,
            envelope=env,
            sleeve_equity=10_000.0,
        )
        assert result.approved is False, (
            "fixing the empty/absent case must not accidentally weaken a "
            "genuinely non-empty allowlist's confinement"
        )
        assert result.reason == envelope.REASON_NOT_IN_ALLOWLIST

    def test_nonempty_allowlist_still_never_blocks_an_exit(self):
        env = _base_envelope(allowlist=["SPY"])
        result = envelope.clamp_order(
            symbol="TSLA",
            side="sell",
            qty=5,
            price=200.0,
            envelope=env,
            sleeve_equity=100_000.0,
            current_position_qty=5,
        )
        assert result.reason != envelope.REASON_NOT_IN_ALLOWLIST, (
            "fixing the empty/absent-allowlist case must not disturb the "
            "existing sell-never-blocked-by-allowlist invariant for a "
            "genuinely non-empty allowlist"
        )
        assert result.approved is True


# ---------------------------------------------------------------------------
# 8c. PM ruling (2026-07-08, folded into #34): is_envelope_widened's own
# allowlist-subset check has the INVERSE gap from clamp_order's -- an empty
# set is a subset of every set, so `not new_allowlist.issubset(old_allowlist)`
# never flags "populated -> empty/absent" as a widen. Under the #34 semantic
# (empty/absent allowlist = confines NOTHING, i.e. the universe of all
# symbols), clearing a populated allowlist to empty/absent is the SINGLE
# MOST EXTREME possible widen -- and until this is fixed, an operator could
# silently strip all ticker confinement without tripping the AC-3
# re-ceremony gate. Unified semantic: old_permitted = UNIVERSE if the old
# allowlist is empty/absent else set(old); same for new_permitted; a widen
# is new_permitted NOT a subset of old_permitted. (test-writer found this
# while investigating #34's clamp_order fix; PM ruled it ships together
# with #34, not as a separate task, since #34 without this fix opens the
# bypass.)
# ---------------------------------------------------------------------------


class TestAllowlistWidenDetectionTreatsEmptyAsUniverse:
    def test_clearing_a_populated_allowlist_to_empty_is_a_widen(self):
        old = _base_envelope(allowlist=["SPY"])
        new = _base_envelope(allowlist=[])
        assert envelope.is_envelope_widened(old, new) is True, (
            "clearing a populated allowlist to EMPTY must register as a widen "
            "-- under the #34 semantic (empty = no ticker confinement, i.e. "
            "the universe of all symbols) this is the single most extreme "
            "possible widen. Without this, an operator could silently strip "
            "all ticker confinement and bypass the AC-3 re-ceremony gate."
        )

    def test_clearing_a_populated_allowlist_by_omitting_the_key_is_a_widen(self):
        old = _base_envelope(allowlist=["SPY"])
        new = {k: v for k, v in old.items() if k != "allowlist"}
        assert envelope.is_envelope_widened(old, new) is True, (
            "omitting the allowlist key entirely must behave identically to "
            "an explicit empty list -- both mean 'no confinement' under #34"
        )

    def test_going_from_empty_allowlist_to_populated_is_not_a_widen(self):
        old = _base_envelope(allowlist=[])
        new = _base_envelope(allowlist=["SPY"])
        assert envelope.is_envelope_widened(old, new) is False, (
            "going from an empty allowlist (confines nothing -- the "
            "universe) to a populated one (confines to just SPY) is a "
            "NARROWING, not a widen -- must not require re-ceremony"
        )

    def test_going_from_absent_allowlist_key_to_populated_is_not_a_widen(self):
        old = _base_envelope()
        del old["allowlist"]
        new = _base_envelope(allowlist=["SPY"])
        assert envelope.is_envelope_widened(old, new) is False, (
            "an absent allowlist key must behave identically to an explicit "
            "empty list on the OLD side too -- going to a populated "
            "allowlist is a narrowing"
        )

    def test_two_empty_allowlists_are_not_a_widen(self):
        old = _base_envelope(allowlist=[])
        new = _base_envelope(allowlist=[])
        assert envelope.is_envelope_widened(old, new) is False, (
            "empty -> empty (universe -> universe) is unchanged, not a widen"
        )

    def test_adding_a_ticker_to_an_already_nonempty_allowlist_is_still_a_widen(self):
        # Regression guard: fixing the empty-allowlist widen semantic must
        # not disturb the existing non-empty-to-non-empty widen behavior
        # already covered by TestEnvelopeWidening.
        old = _base_envelope(allowlist=["SPY"])
        new = _base_envelope(allowlist=["SPY", "QQQ"])
        assert envelope.is_envelope_widened(old, new) is True

    def test_removing_a_ticker_from_a_still_nonempty_allowlist_is_still_not_a_widen(self):
        old = _base_envelope(allowlist=["SPY", "QQQ"])
        new = _base_envelope(allowlist=["SPY"])
        assert envelope.is_envelope_widened(old, new) is False


# ---------------------------------------------------------------------------
# 9. Review finding BLOCK #3 (sleeve-review, commit 2200c66): removing a cap
# entirely (old value present, new value None/absent) must count as a widen.
# clamp_order treats a None cap as "unlimited", so nulling out a cap is the
# MOST extreme possible widen -- is_envelope_widened's "both sides present"
# guard must not let this bypass the AC-3 re-ceremony gate.
# ---------------------------------------------------------------------------


class TestEnvelopeWideningCapRemoval:
    def test_nulling_out_max_position_pct_is_a_widen(self):
        old = _base_envelope(max_position_pct=0.25)
        new = dict(old)
        new["max_position_pct"] = None
        assert envelope.is_envelope_widened(old, new) is True, (
            "removing a max_position_pct cap (0.25 -> unlimited) is the most extreme "
            "possible widen and must require re-ceremony (AC-3)"
        )

    def test_omitting_max_position_pct_key_entirely_is_a_widen(self):
        old = _base_envelope(max_position_pct=0.25)
        new = {k: v for k, v in old.items() if k != "max_position_pct"}
        assert envelope.is_envelope_widened(old, new) is True, (
            "an absent key must be treated the same as an explicit None -- both mean "
            "'no cap' to clamp_order, so both must be detected as a widen"
        )

    def test_nulling_out_max_order_usd_is_a_widen(self):
        old = _base_envelope(max_order_usd=500.0)
        new = dict(old)
        new["max_order_usd"] = None
        assert envelope.is_envelope_widened(old, new) is True

    def test_nulling_out_max_daily_turnover_usd_is_a_widen(self):
        old = _base_envelope(max_daily_turnover_usd=500.0)
        new = dict(old)
        new["max_daily_turnover_usd"] = None
        assert envelope.is_envelope_widened(old, new) is True

    def test_both_caps_absent_from_the_start_is_not_a_widen(self):
        # A cap that was ALREADY unlimited on both sides (None -> None) must
        # not be flagged -- only a present-to-absent TRANSITION is a widen.
        old = _base_envelope(max_position_pct=None)
        new = _base_envelope(max_position_pct=None)
        assert envelope.is_envelope_widened(old, new) is False

    def test_adding_a_cap_where_none_existed_is_not_a_widen(self):
        # The asymmetric case a naive "either side is None -> widen" fix
        # would get WRONG: going from unlimited (None) to a fixed cap (0.25)
        # is NARROWING (strictly more restrictive), not a widen -- it must
        # never force an unnecessary re-ceremony.
        old = _base_envelope(max_position_pct=None)
        new = _base_envelope(max_position_pct=0.25)
        assert envelope.is_envelope_widened(old, new) is False, (
            "adding a cap where none existed before is narrowing, not widening -- "
            "must not require re-ceremony"
        )
