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


# ---------------------------------------------------------------------------
# 4. reconcile_aggregate_cash / reconcile_aggregate_position — shared-account
#    semantics (BLOCK, s3-ux live finding + PM ruling, 2026-07-08)
#
# CONTRACT this section specifies for the GREEN implementer (s3-engine):
#
#     def reconcile_aggregate_cash(
#         *, total_sleeve_cash_claim_usd: float, broker_cash_usd: float,
#         cash_tolerance_usd: float,
#     ) -> ReconciliationResult:
#         # ONE-SIDED. The broker has no concept of per-sleeve cash — only
#         # ONE account-level cash figure exists, shared by every sleeve. The
#         # money-safety invariant that matters: sleeves must never
#         # COLLECTIVELY believe they can spend more than the account
#         # actually holds. Breach iff total_sleeve_cash_claim_usd exceeds
#         # broker_cash_usd + cash_tolerance_usd. The REVERSE — broker cash
#         # exceeding the sleeves' combined claim — is explicitly NOT a
#         # breach: unallocated float, or the operator's own money sharing
#         # the same account, is normal and expected. breach reason string
#         # must contain "aggregate_cash_exceeds_account".
#
#     def reconcile_aggregate_position(
#         *, symbol: str, total_sleeve_qty: float, broker_qty: float,
#         position_tolerance_pct: float,
#     ) -> ReconciliationResult:
#         # ONE-SIDED, per-symbol. Breach iff the sleeves' COMBINED qty for
#         # this symbol (summed across every sleeve that has ANY history in
#         # it) exceeds the broker's own qty for that symbol beyond
#         # tolerance. total_sleeve_qty <= broker_qty is NEVER a breach —
#         # broker surplus in a symbol (an operator-external holding sharing
#         # the account, or a symbol no sleeve tracks at all, i.e.
#         # total_sleeve_qty == 0) is explicitly ignored by design. breach
#         # reason string must contain "aggregate_position_exceeds_broker:<SYMBOL>".
# ---------------------------------------------------------------------------


class TestReconcileAggregateCash:
    def test_matching_aggregate_cash_is_ok(self):
        result = reconciliation.reconcile_aggregate_cash(
            total_sleeve_cash_claim_usd=3000.0,
            broker_cash_usd=3000.0,
            cash_tolerance_usd=1.0,
        )
        assert result.ok is True
        assert result.verdict == _OK

    def test_broker_cash_exceeding_sleeve_claim_is_fine_not_a_breach(self):
        """The load-bearing one-sided invariant: unallocated float in the
        shared account (broker holds MORE than sleeves collectively claim)
        must never pause anything — this is normal, not drift."""
        result = reconciliation.reconcile_aggregate_cash(
            total_sleeve_cash_claim_usd=3000.0,
            broker_cash_usd=50000.0,  # a large operator float sharing the account
            cash_tolerance_usd=1.0,
        )
        assert result.ok is True, (
            f"broker cash exceeding the sleeves' combined claim must NEVER "
            f"breach — got {result.breaches}"
        )
        assert result.verdict == _OK

    def test_sleeve_claim_exceeding_broker_cash_beyond_tolerance_breaches(self):
        """The actual money-safety invariant: sleeves collectively believing
        they hold more cash than the account actually has must breach."""
        result = reconciliation.reconcile_aggregate_cash(
            total_sleeve_cash_claim_usd=3000.0,
            broker_cash_usd=100.0,
            cash_tolerance_usd=1.0,
        )
        assert result.ok is False
        assert result.verdict == _PAUSED
        assert any("aggregate_cash_exceeds_account" in b for b in result.breaches)

    def test_sleeve_claim_within_tolerance_of_broker_cash_is_ok(self):
        result = reconciliation.reconcile_aggregate_cash(
            total_sleeve_cash_claim_usd=3000.50,
            broker_cash_usd=3000.00,
            cash_tolerance_usd=1.0,
        )
        assert result.ok is True


class TestReconcileAggregatePosition:
    def test_matching_aggregate_qty_is_ok(self):
        result = reconciliation.reconcile_aggregate_position(
            symbol="SPY",
            total_sleeve_qty=15.0,
            broker_qty=15.0,
            position_tolerance_pct=0.005,
        )
        assert result.ok is True

    def test_broker_surplus_in_symbol_is_fine_not_a_breach(self):
        """An operator-external holding (or slack) in a symbol the sleeves
        also partially hold must never breach — only a sleeve claim
        EXCEEDING the broker's truth is drift."""
        result = reconciliation.reconcile_aggregate_position(
            symbol="SPY",
            total_sleeve_qty=15.0,
            broker_qty=100.0,  # operator holds 85 more shares of SPY outright
            position_tolerance_pct=0.005,
        )
        assert result.ok is True, (
            f"broker qty exceeding the sleeves' combined claim must never "
            f"breach; got {result.breaches}"
        )

    def test_symbol_no_sleeve_tracks_is_ignored_entirely(self):
        """total_sleeve_qty=0 (no sleeve has ever touched this symbol) vs any
        broker qty must never breach — this is the "operator-external
        position" scenario from the plan's shared-account model."""
        result = reconciliation.reconcile_aggregate_position(
            symbol="TSLA",
            total_sleeve_qty=0.0,
            broker_qty=200.0,
            position_tolerance_pct=0.005,
        )
        assert result.ok is True

    def test_sleeve_claim_exceeding_broker_qty_beyond_tolerance_breaches(self):
        result = reconciliation.reconcile_aggregate_position(
            symbol="SPY",
            total_sleeve_qty=15.0,
            broker_qty=10.0,
            position_tolerance_pct=0.005,
        )
        assert result.ok is False
        assert result.verdict == _PAUSED
        assert any("aggregate_position_exceeds_broker:SPY" in b for b in result.breaches)

    def test_sleeve_claim_exceeding_zero_broker_qty_always_breaches(self):
        """Mirrors the old missing_position semantics: sleeves collectively
        claim a symbol the broker shows ZERO of — always a breach,
        regardless of the relative tolerance (there's no meaningful
        percentage of zero)."""
        result = reconciliation.reconcile_aggregate_position(
            symbol="QQQ",
            total_sleeve_qty=3.0,
            broker_qty=0.0,
            position_tolerance_pct=0.005,
        )
        assert result.ok is False
        assert any("aggregate_position_exceeds_broker:QQQ" in b for b in result.breaches)

    def test_tiny_drift_within_tolerance_is_ok(self):
        # 15.0 vs 15.02 broker => ~0.13% over-claim relative to broker qty, within 0.5% tolerance.
        result = reconciliation.reconcile_aggregate_position(
            symbol="SPY",
            total_sleeve_qty=15.02,
            broker_qty=15.0,
            position_tolerance_pct=0.005,
        )
        assert result.ok is True

    def test_both_sides_zero_is_ok(self):
        result = reconciliation.reconcile_aggregate_position(
            symbol="SPY", total_sleeve_qty=0.0, broker_qty=0.0, position_tolerance_pct=0.005
        )
        assert result.ok is True
