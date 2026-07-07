"""
RED tests — sleeves/reconciliation.py: tolerance-based broker-truth checks (AC-9).

CONTRACT this file specifies for the GREEN implementer (sleeve-integration-impl):

    sleeves/reconciliation.py

    @dataclass(frozen=True)
    class ReconciliationResult:
        ok: bool                  # True iff zero breaches
        verdict: str               # "OK" | "PAUSED_RECONCILIATION"
        breaches: list[str]        # machine-readable reason strings; [] iff ok

    def reconcile_positions(
        sleeve_positions: dict[str, float],   # symbol -> qty per OUR ledger
        broker_positions: dict[str, float],   # symbol -> qty per Alpaca truth
        tolerance_pct: float,                  # relative tolerance, e.g. 0.005 = 0.5%
    ) -> ReconciliationResult: ...

    def reconcile_cash(
        sleeve_cash_usd: float, broker_cash_usd: float, tolerance_usd: float,
    ) -> ReconciliationResult: ...

    def reconcile_sleeve(
        *, sleeve_positions: dict[str, float], broker_positions: dict[str, float],
        sleeve_cash_usd: float, broker_cash_usd: float,
        position_tolerance_pct: float, cash_tolerance_usd: float,
    ) -> ReconciliationResult:
        # Combines both checks; breaches is the union; verdict is
        # PAUSED_RECONCILIATION if either check breaches.

Breach vocabulary (machine-readable, must appear verbatim as a substring of
an entry in `.breaches`):
    "unknown_position:<SYMBOL>"  — broker holds a symbol our ledger has no
                                    record of (orphaned bracket leg / manual
                                    operator intervention at the broker).
    "missing_position:<SYMBOL>"  — our ledger believes we hold a symbol the
                                    broker has zero (or no) position in.
    "position_drift:<SYMBOL>"    — both sides have the symbol, but qty
                                    differs beyond tolerance_pct.
    "cash_drift"                 — cash differs beyond tolerance_usd.

AC-9: any breach ⇒ verdict == "PAUSED_RECONCILIATION" (never a partial/soft
state) and `ok is False`. No order may be placed while paused — this file
tests the pure verdict function; the "no order" enforcement itself is a
P3/runner integration concern, out of P1 scope.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "sleeves.reconciliation", reason="RED phase — sleeves.reconciliation not implemented yet"
)

import sleeves.reconciliation as reconciliation  # noqa: E402

_PAUSED = "PAUSED_RECONCILIATION"
_OK = "OK"


# ---------------------------------------------------------------------------
# 1. reconcile_positions
# ---------------------------------------------------------------------------


class TestReconcilePositions:
    def test_matching_positions_within_tolerance_is_ok(self):
        result = reconciliation.reconcile_positions(
            sleeve_positions={"SPY": 10.0},
            broker_positions={"SPY": 10.0},
            tolerance_pct=0.005,
        )
        assert result.ok is True
        assert result.verdict == _OK
        assert result.breaches == []

    def test_tiny_drift_within_tolerance_is_ok(self):
        # 10.0 vs 10.02 => 0.2% drift, within a 0.5% tolerance.
        result = reconciliation.reconcile_positions(
            sleeve_positions={"SPY": 10.0},
            broker_positions={"SPY": 10.02},
            tolerance_pct=0.005,
        )
        assert result.ok is True

    def test_drift_beyond_tolerance_pauses(self):
        # 10.0 vs 12.0 => 20% drift, well beyond a 0.5% tolerance.
        result = reconciliation.reconcile_positions(
            sleeve_positions={"SPY": 10.0},
            broker_positions={"SPY": 12.0},
            tolerance_pct=0.005,
        )
        assert result.ok is False
        assert result.verdict == _PAUSED
        assert any("position_drift:SPY" in b for b in result.breaches)

    def test_unknown_broker_position_is_flagged(self):
        # Broker holds AAPL; our ledger has no record of it at all.
        result = reconciliation.reconcile_positions(
            sleeve_positions={},
            broker_positions={"AAPL": 5.0},
            tolerance_pct=0.005,
        )
        assert result.ok is False
        assert result.verdict == _PAUSED
        assert any("unknown_position:AAPL" in b for b in result.breaches)

    def test_missing_broker_position_is_flagged(self):
        # Our ledger believes we hold QQQ; broker shows nothing.
        result = reconciliation.reconcile_positions(
            sleeve_positions={"QQQ": 3.0},
            broker_positions={},
            tolerance_pct=0.005,
        )
        assert result.ok is False
        assert result.verdict == _PAUSED
        assert any("missing_position:QQQ" in b for b in result.breaches)

    def test_multiple_breaches_all_reported_not_just_first(self):
        result = reconciliation.reconcile_positions(
            sleeve_positions={"SPY": 10.0, "QQQ": 3.0},
            broker_positions={"SPY": 20.0, "AAPL": 1.0},
            tolerance_pct=0.005,
        )
        assert result.ok is False
        breach_text = " ".join(result.breaches)
        assert "SPY" in breach_text
        assert "QQQ" in breach_text
        assert "AAPL" in breach_text

    def test_both_sides_empty_is_ok(self):
        result = reconciliation.reconcile_positions(
            sleeve_positions={}, broker_positions={}, tolerance_pct=0.005
        )
        assert result.ok is True
        assert result.breaches == []

    def test_zero_qty_entries_do_not_spuriously_flag_as_missing_or_unknown(self):
        # A symbol explicitly present with qty=0 on both sides (e.g. a
        # fully-closed position still tracked) must not be flagged.
        result = reconciliation.reconcile_positions(
            sleeve_positions={"SPY": 0.0},
            broker_positions={"SPY": 0.0},
            tolerance_pct=0.005,
        )
        assert result.ok is True


# ---------------------------------------------------------------------------
# 2. reconcile_cash
# ---------------------------------------------------------------------------


class TestReconcileCash:
    def test_matching_cash_is_ok(self):
        result = reconciliation.reconcile_cash(1000.0, 1000.0, tolerance_usd=1.0)
        assert result.ok is True
        assert result.verdict == _OK

    def test_cash_within_tolerance_is_ok(self):
        # $0.50 diff, tolerance $1.00 (fees/rounding).
        result = reconciliation.reconcile_cash(1000.00, 999.50, tolerance_usd=1.0)
        assert result.ok is True

    def test_cash_beyond_tolerance_pauses(self):
        result = reconciliation.reconcile_cash(1000.00, 900.00, tolerance_usd=1.0)
        assert result.ok is False
        assert result.verdict == _PAUSED
        assert any("cash_drift" in b for b in result.breaches)

    def test_cash_drift_direction_agnostic(self):
        # Broker showing MORE cash than expected is just as much a breach
        # as showing less — both indicate a bookkeeping mismatch.
        result = reconciliation.reconcile_cash(500.00, 800.00, tolerance_usd=1.0)
        assert result.ok is False
        assert result.verdict == _PAUSED


# ---------------------------------------------------------------------------
# 3. reconcile_sleeve — combined verdict
# ---------------------------------------------------------------------------


class TestReconcileSleeveCombined:
    def test_all_clean_is_ok(self):
        result = reconciliation.reconcile_sleeve(
            sleeve_positions={"SPY": 10.0},
            broker_positions={"SPY": 10.0},
            sleeve_cash_usd=500.0,
            broker_cash_usd=500.0,
            position_tolerance_pct=0.005,
            cash_tolerance_usd=1.0,
        )
        assert result.ok is True
        assert result.verdict == _OK
        assert result.breaches == []

    def test_position_breach_alone_pauses(self):
        result = reconciliation.reconcile_sleeve(
            sleeve_positions={"SPY": 10.0},
            broker_positions={"SPY": 999.0},
            sleeve_cash_usd=500.0,
            broker_cash_usd=500.0,
            position_tolerance_pct=0.005,
            cash_tolerance_usd=1.0,
        )
        assert result.ok is False
        assert result.verdict == _PAUSED

    def test_cash_breach_alone_pauses(self):
        result = reconciliation.reconcile_sleeve(
            sleeve_positions={"SPY": 10.0},
            broker_positions={"SPY": 10.0},
            sleeve_cash_usd=500.0,
            broker_cash_usd=1.0,
            position_tolerance_pct=0.005,
            cash_tolerance_usd=1.0,
        )
        assert result.ok is False
        assert result.verdict == _PAUSED

    def test_both_breaches_reported_together(self):
        result = reconciliation.reconcile_sleeve(
            sleeve_positions={"SPY": 10.0},
            broker_positions={"SPY": 999.0},
            sleeve_cash_usd=500.0,
            broker_cash_usd=1.0,
            position_tolerance_pct=0.005,
            cash_tolerance_usd=1.0,
        )
        assert result.ok is False
        breach_text = " ".join(result.breaches)
        assert "SPY" in breach_text
        assert "cash_drift" in breach_text

    def test_verdict_is_never_a_partial_or_soft_state(self):
        # AC-9: no third verdict value exists — always exactly OK or
        # PAUSED_RECONCILIATION, never e.g. "WARNING" or None.
        clean = reconciliation.reconcile_sleeve(
            sleeve_positions={},
            broker_positions={},
            sleeve_cash_usd=0.0,
            broker_cash_usd=0.0,
            position_tolerance_pct=0.005,
            cash_tolerance_usd=1.0,
        )
        breached = reconciliation.reconcile_sleeve(
            sleeve_positions={"SPY": 10.0},
            broker_positions={},
            sleeve_cash_usd=0.0,
            broker_cash_usd=0.0,
            position_tolerance_pct=0.005,
            cash_tolerance_usd=1.0,
        )
        assert clean.verdict in (_OK, _PAUSED)
        assert breached.verdict in (_OK, _PAUSED)
        assert clean.verdict != breached.verdict
