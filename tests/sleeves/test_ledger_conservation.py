"""
RED tests — sleeves/ledger.py: sleeve cash/position conservation invariant.

CONTRACT this file specifies for the GREEN implementer (sleeve-risk-impl):

    sleeves/ledger.py

    class InsufficientCashError(Exception): ...
    class InsufficientPositionError(Exception): ...

    @dataclass(frozen=True)
    class Position:
        qty: float
        cost_basis_usd: float      # total $ paid for the currently-held qty

    @dataclass(frozen=True)
    class LedgerState:
        capital_usd: float          # fixed at sleeve creation (AC-1)
        cash_usd: float
        reserved_usd: float         # $ set aside for open (unfilled) orders
        realized_pnl_usd: float     # cumulative realized gain/loss from sells
        positions: dict[str, Position]

    def new_ledger(capital_usd: float) -> LedgerState:
        # cash_usd == capital_usd, reserved_usd == 0, realized_pnl_usd == 0,
        # positions == {}

    def reserve(ledger: LedgerState, notional_usd: float) -> LedgerState:
        # cash_usd -= notional_usd; reserved_usd += notional_usd.
        # Raises InsufficientCashError if notional_usd > ledger.cash_usd —
        # AC-1: "a sleeve can never spend beyond its allocation."

    def release(ledger: LedgerState, notional_usd: float) -> LedgerState:
        # Un-reserve (order canceled or rejected before any fill):
        # reserved_usd -= notional_usd; cash_usd += notional_usd.

    def apply_fill(
        ledger: LedgerState, *, symbol: str, side: str, qty: float,
        price: float, reserved_usd: float,
    ) -> LedgerState:
        # BUY: reserved_usd -= reserved_usd_param; positions[symbol].qty += qty;
        #      positions[symbol].cost_basis_usd += qty * price.
        #      (any difference between reserved_usd_param and qty*price, e.g.
        #      a favorable fill vs. the reservation estimate, returns to cash.)
        # SELL: positions[symbol].qty -= qty;
        #       cost_removed = qty * (position.cost_basis_usd / position.qty)  [avg cost]
        #       cash_usd += qty * price   (full sale proceeds)
        #       realized_pnl_usd += (qty * price) - cost_removed
        #       positions[symbol].cost_basis_usd -= cost_removed
        #       Raises InsufficientPositionError if qty > current position qty.

    CONSERVATION LAW (must hold after every legal operation, by construction):
        cash_usd + reserved_usd + sum(p.cost_basis_usd for p in positions.values())
            == capital_usd + realized_pnl_usd

    This is the "capital invariant" — no dollar is created or destroyed by
    the ledger's own bookkeeping. Property-based tests below throw random
    legal operation sequences at it (hypothesis) and assert the law holds
    after every single step, not just at the end.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytest.importorskip("sleeves.ledger", reason="RED phase — sleeves.ledger not implemented yet")

import sleeves.ledger as ledger  # noqa: E402

_TOL = 1e-6


def _accounted_usd(state) -> float:
    return (
        state.cash_usd
        + state.reserved_usd
        + sum(p.cost_basis_usd for p in state.positions.values())
    )


def _assert_conserved(state, label: str = "") -> None:
    accounted = _accounted_usd(state)
    expected = state.capital_usd + state.realized_pnl_usd
    assert accounted == pytest.approx(expected, abs=_TOL), (
        f"{label}: conservation violated — cash({state.cash_usd}) + "
        f"reserved({state.reserved_usd}) + positions_at_cost("
        f"{accounted - state.cash_usd - state.reserved_usd}) = {accounted}, "
        f"expected capital({state.capital_usd}) + realized_pnl({state.realized_pnl_usd}) = {expected}"
    )


# ---------------------------------------------------------------------------
# 1. new_ledger — initialization
# ---------------------------------------------------------------------------


class TestNewLedger:
    def test_new_ledger_cash_equals_full_capital(self):
        state = ledger.new_ledger(10_000.0)
        assert state.cash_usd == pytest.approx(10_000.0)
        assert state.capital_usd == pytest.approx(10_000.0)

    def test_new_ledger_has_zero_reservations_and_positions(self):
        state = ledger.new_ledger(10_000.0)
        assert state.reserved_usd == 0
        assert state.positions == {}
        assert state.realized_pnl_usd == 0

    def test_new_ledger_satisfies_conservation_law(self):
        state = ledger.new_ledger(25_000.0)
        _assert_conserved(state, "new_ledger")


# ---------------------------------------------------------------------------
# 2. reserve / release lifecycle
# ---------------------------------------------------------------------------


class TestReserveRelease:
    def test_reserve_moves_cash_to_reserved(self):
        state = ledger.new_ledger(10_000.0)
        state = ledger.reserve(state, 1_000.0)
        assert state.cash_usd == pytest.approx(9_000.0)
        assert state.reserved_usd == pytest.approx(1_000.0)
        _assert_conserved(state, "after reserve")

    def test_reserve_more_than_available_cash_raises(self):
        state = ledger.new_ledger(1_000.0)
        with pytest.raises(ledger.InsufficientCashError):
            ledger.reserve(state, 1_000.01)

    def test_reserve_exactly_all_cash_succeeds(self):
        state = ledger.new_ledger(1_000.0)
        state = ledger.reserve(state, 1_000.0)
        assert state.cash_usd == pytest.approx(0.0)
        assert state.reserved_usd == pytest.approx(1_000.0)
        _assert_conserved(state, "reserve-all")

    def test_release_returns_reservation_to_cash(self):
        state = ledger.new_ledger(10_000.0)
        state = ledger.reserve(state, 2_000.0)
        state = ledger.release(state, 2_000.0)
        assert state.cash_usd == pytest.approx(10_000.0)
        assert state.reserved_usd == pytest.approx(0.0)
        _assert_conserved(state, "after release")

    def test_release_models_both_cancel_and_reject_identically(self):
        # A canceled order and a rejected order both simply return the
        # reservation — there is no distinct "reject" transition, they use
        # the same release() path (an order never partially executes on
        # reject; a cancel may follow a partial fill, releasing only the
        # UNFILLED remainder's reservation).
        state_a = ledger.new_ledger(5_000.0)
        state_a = ledger.reserve(state_a, 500.0)
        state_a = ledger.release(state_a, 500.0)  # simulating "rejected"

        state_b = ledger.new_ledger(5_000.0)
        state_b = ledger.reserve(state_b, 500.0)
        state_b = ledger.release(state_b, 500.0)  # simulating "canceled"

        assert state_a.cash_usd == pytest.approx(state_b.cash_usd)
        assert state_a.reserved_usd == pytest.approx(state_b.reserved_usd)


# ---------------------------------------------------------------------------
# 3. apply_fill — buy side
# ---------------------------------------------------------------------------


class TestApplyFillBuy:
    def test_buy_fill_converts_reservation_into_a_position(self):
        state = ledger.new_ledger(10_000.0)
        state = ledger.reserve(state, 1_000.0)
        state = ledger.apply_fill(
            state, symbol="SPY", side="buy", qty=10, price=100.0, reserved_usd=1_000.0
        )
        assert state.positions["SPY"].qty == pytest.approx(10)
        assert state.positions["SPY"].cost_basis_usd == pytest.approx(1_000.0)
        assert state.reserved_usd == pytest.approx(0.0)
        _assert_conserved(state, "after buy fill")

    def test_buy_fill_favorable_price_returns_difference_to_cash(self):
        # Reserved $1000 for an estimated fill; actual fill is cheaper ($950).
        # The $50 difference must return to cash, not vanish.
        state = ledger.new_ledger(10_000.0)
        state = ledger.reserve(state, 1_000.0)
        state = ledger.apply_fill(
            state, symbol="SPY", side="buy", qty=10, price=95.0, reserved_usd=1_000.0
        )
        assert state.positions["SPY"].cost_basis_usd == pytest.approx(950.0)
        _assert_conserved(state, "after favorable buy fill")

    def test_partial_fill_then_cancel_remainder_conserves_capital(self):
        # Reserve $1000 for 10 shares @ $100. Only 4 fill @ $101 ($404).
        # The remaining $596 of the reservation is canceled (released).
        state = ledger.new_ledger(10_000.0)
        state = ledger.reserve(state, 1_000.0)
        state = ledger.apply_fill(
            state, symbol="SPY", side="buy", qty=4, price=101.0, reserved_usd=404.0
        )
        state = ledger.release(state, 596.0)  # unfilled remainder canceled
        assert state.positions["SPY"].qty == pytest.approx(4)
        assert state.reserved_usd == pytest.approx(0.0)
        _assert_conserved(state, "after partial fill + cancel remainder")

    def test_multiple_buy_fills_accumulate_cost_basis_and_qty(self):
        state = ledger.new_ledger(10_000.0)
        state = ledger.reserve(state, 500.0)
        state = ledger.apply_fill(
            state, symbol="SPY", side="buy", qty=5, price=100.0, reserved_usd=500.0
        )
        state = ledger.reserve(state, 300.0)
        state = ledger.apply_fill(
            state, symbol="SPY", side="buy", qty=3, price=100.0, reserved_usd=300.0
        )
        assert state.positions["SPY"].qty == pytest.approx(8)
        assert state.positions["SPY"].cost_basis_usd == pytest.approx(800.0)
        _assert_conserved(state, "after accumulated buy fills")


# ---------------------------------------------------------------------------
# 4. apply_fill — sell side, realized P&L
# ---------------------------------------------------------------------------


class TestApplyFillSell:
    def _state_with_position(self, capital=10_000.0, qty=10, cost_basis=1_000.0):
        state = ledger.new_ledger(capital)
        state = ledger.reserve(state, cost_basis)
        state = ledger.apply_fill(
            state,
            symbol="SPY",
            side="buy",
            qty=qty,
            price=cost_basis / qty,
            reserved_usd=cost_basis,
        )
        return state

    def test_sell_at_a_gain_credits_realized_pnl(self):
        state = self._state_with_position(qty=10, cost_basis=1_000.0)  # $100/share cost
        state = ledger.apply_fill(
            state, symbol="SPY", side="sell", qty=10, price=110.0, reserved_usd=0.0
        )
        assert state.positions["SPY"].qty == pytest.approx(0)
        assert state.realized_pnl_usd == pytest.approx(100.0)  # 10 * (110-100)
        _assert_conserved(state, "after profitable sell")

    def test_sell_at_a_loss_debits_realized_pnl(self):
        state = self._state_with_position(qty=10, cost_basis=1_000.0)
        state = ledger.apply_fill(
            state, symbol="SPY", side="sell", qty=10, price=90.0, reserved_usd=0.0
        )
        assert state.realized_pnl_usd == pytest.approx(-100.0)
        _assert_conserved(state, "after losing sell")

    def test_partial_sell_leaves_remaining_position_and_cost_basis(self):
        state = self._state_with_position(qty=10, cost_basis=1_000.0)  # $100/share
        state = ledger.apply_fill(
            state, symbol="SPY", side="sell", qty=4, price=100.0, reserved_usd=0.0
        )
        assert state.positions["SPY"].qty == pytest.approx(6)
        assert state.positions["SPY"].cost_basis_usd == pytest.approx(600.0)
        _assert_conserved(state, "after partial sell")

    def test_selling_more_than_held_raises_insufficient_position(self):
        state = self._state_with_position(qty=5, cost_basis=500.0)
        with pytest.raises(ledger.InsufficientPositionError):
            ledger.apply_fill(
                state, symbol="SPY", side="sell", qty=10, price=100.0, reserved_usd=0.0
            )

    def test_selling_a_symbol_never_held_raises_insufficient_position(self):
        state = ledger.new_ledger(10_000.0)
        with pytest.raises(ledger.InsufficientPositionError):
            ledger.apply_fill(
                state, symbol="QQQ", side="sell", qty=1, price=400.0, reserved_usd=0.0
            )


# ---------------------------------------------------------------------------
# 5. Property-based conservation across randomized legal operation sequences
# ---------------------------------------------------------------------------


class TestLedgerConservationProperty:
    @settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
    @given(data=st.data())
    def test_conservation_holds_across_random_buy_side_sequences(self, data):
        """
        Random sequence of reserve -> {fill | cancel | reject} on the buy
        side only (sells require tracking which symbol/qty is available,
        covered by the deterministic sell tests above + the mixed-sequence
        test below). Every operation is drawn to be LEGAL (bounded by
        current ledger state) so the invariant must hold at every step if
        the implementation is correct.
        """
        capital = data.draw(
            st.floats(min_value=1_000.0, max_value=200_000.0, allow_nan=False, allow_infinity=False)
        )
        state = ledger.new_ledger(capital)
        _assert_conserved(state, "initial")

        open_reservations: list[float] = []
        num_ops = data.draw(st.integers(min_value=1, max_value=25))

        for step in range(num_ops):
            choices = ["reserve"]
            if open_reservations:
                choices += ["fill", "cancel_or_reject"]
            op = data.draw(st.sampled_from(choices))

            if op == "reserve":
                if state.cash_usd <= 0.01:
                    continue
                notional = data.draw(
                    st.floats(
                        min_value=0.01,
                        max_value=state.cash_usd,
                        allow_nan=False,
                        allow_infinity=False,
                    )
                )
                state = ledger.reserve(state, notional)
                open_reservations.append(notional)

            elif op == "fill":
                idx = data.draw(st.integers(min_value=0, max_value=len(open_reservations) - 1))
                notional = open_reservations.pop(idx)
                price = data.draw(
                    st.floats(
                        min_value=0.01, max_value=5_000.0, allow_nan=False, allow_infinity=False
                    )
                )
                qty = notional / price
                symbol = data.draw(st.sampled_from(["SPY", "QQQ", "AAPL"]))
                state = ledger.apply_fill(
                    state, symbol=symbol, side="buy", qty=qty, price=price, reserved_usd=notional
                )

            else:  # cancel_or_reject
                idx = data.draw(st.integers(min_value=0, max_value=len(open_reservations) - 1))
                notional = open_reservations.pop(idx)
                state = ledger.release(state, notional)

            _assert_conserved(state, f"step {step} ({op})")

    def test_conservation_holds_across_buy_then_sell_round_trip(self):
        """Deterministic mixed-sequence regression: buy, partial sell, buy
        more, sell all — conservation must hold at every intermediate step,
        not just at the end.
        """
        state = ledger.new_ledger(10_000.0)

        state = ledger.reserve(state, 1_000.0)
        state = ledger.apply_fill(
            state, symbol="SPY", side="buy", qty=10, price=100.0, reserved_usd=1_000.0
        )
        _assert_conserved(state, "after 1st buy")

        state = ledger.apply_fill(
            state, symbol="SPY", side="sell", qty=4, price=105.0, reserved_usd=0.0
        )
        _assert_conserved(state, "after partial sell")

        state = ledger.reserve(state, 300.0)
        state = ledger.apply_fill(
            state, symbol="SPY", side="buy", qty=3, price=100.0, reserved_usd=300.0
        )
        _assert_conserved(state, "after 2nd buy")

        remaining_qty = state.positions["SPY"].qty
        state = ledger.apply_fill(
            state, symbol="SPY", side="sell", qty=remaining_qty, price=98.0, reserved_usd=0.0
        )
        _assert_conserved(state, "after final sell-out")
        assert state.positions["SPY"].qty == pytest.approx(0)
        assert state.positions["SPY"].cost_basis_usd == pytest.approx(0.0, abs=_TOL)
