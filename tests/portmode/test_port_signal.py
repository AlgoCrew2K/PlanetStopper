"""
RED tests for port-level signal: binary trigger + sized asset-reduction target (AC-P2.6.*).

The port signal is a dict with 'triggered', 'triggered_reason', 'fired_at_cycle',
'target_reduction', and 'port_total_reduction_usd'.
"""

from __future__ import annotations

import pytest


# These imports WILL FAIL until implemented (RED intent)
from port_aggregator import build_port_signal  # noqa: F401


def _make_port_state(
    triggered: bool,
    triggered_reason: str | None,
    hwm: float = 100000.0,
    current_return: float = 0.0,
    ticker_drawdowns: dict | None = None,
) -> dict:
    return {
        "triggered": triggered,
        "triggered_reason": triggered_reason,
        "high_water_mark": hwm,
        "current_return": current_return,
        "ticker_drawdowns": ticker_drawdowns or {},
    }


class TestPortSignalSchema:
    """AC-P2.6.1: Signal payload schema."""

    def test_triggered_false_has_empty_target_reduction(self):
        """AC-P2.6.3: When triggered=False, target_reduction is empty/None."""
        signal = build_port_signal(
            port_state=_make_port_state(triggered=False, triggered_reason=None),
            cycle_id="cycle-001",
        )
        assert signal["triggered"] is False
        assert not signal["target_reduction"], (
            "AC-P2.6.3: target_reduction must be empty when triggered=False"
        )

    def test_triggered_true_has_nonempty_target_reduction(self):
        """AC-P2.6.1: When triggered=True, target_reduction must be non-empty."""
        signal = build_port_signal(
            port_state=_make_port_state(
                triggered=True,
                triggered_reason="hwm_trailing_stop",
                ticker_drawdowns={"SPY": 5000.0, "QQQ": 2000.0},
            ),
            cycle_id="cycle-002",
        )
        assert signal["triggered"] is True
        assert len(signal["target_reduction"]) > 0

    def test_signal_schema_has_required_keys(self):
        """AC-P2.6.1: Signal dict must have all required keys."""
        signal = build_port_signal(
            port_state=_make_port_state(triggered=True, triggered_reason="tp_mc_gated"),
            cycle_id="cycle-003",
        )
        required = {"triggered", "triggered_reason", "fired_at_cycle", "target_reduction", "port_total_reduction_usd"}
        assert required.issubset(signal.keys()), (
            f"Signal missing keys: {required - signal.keys()}"
        )

    def test_target_reduction_items_have_required_keys(self):
        """AC-P2.6.1: Each item in target_reduction must have ticker, amount_usd, reason_for_this_ticker."""
        signal = build_port_signal(
            port_state=_make_port_state(
                triggered=True,
                triggered_reason="hwm_trailing_stop",
                ticker_drawdowns={"AAPL": 3000.0},
            ),
            cycle_id="cycle-004",
        )
        for item in signal["target_reduction"]:
            assert "ticker" in item
            assert "amount_usd" in item
            assert "reason_for_this_ticker" in item

    def test_port_total_reduction_usd_equals_sum_of_amounts(self):
        """AC-P2.6.1: port_total_reduction_usd must equal sum(target_reduction[].amount_usd)."""
        signal = build_port_signal(
            port_state=_make_port_state(
                triggered=True,
                triggered_reason="hwm_trailing_stop",
                ticker_drawdowns={"SPY": 4000.0, "QQQ": 3000.0, "AAPL": 1000.0},
            ),
            cycle_id="cycle-005",
        )
        total = sum(item["amount_usd"] for item in signal["target_reduction"])
        assert signal["port_total_reduction_usd"] == pytest.approx(total, rel=1e-6)


class TestPortSignalTargetDerivation:
    """AC-P2.6.2: Per-ticker reduction amounts derived from the firing signal."""

    def test_hwm_trailing_stop_uses_drawdown_contributions(self):
        """
        AC-P2.6.2: Port HWM trailing-stop -> target = per-ticker drawdown contributions:
        (peak_exposure_usd - current_exposure_usd) clamped at zero.
        """
        signal = build_port_signal(
            port_state={
                "triggered": True,
                "triggered_reason": "hwm_trailing_stop",
                "ticker_peak_exposures": {"SPY": 10000.0, "QQQ": 5000.0},
                "ticker_current_exposures": {"SPY": 8000.0, "QQQ": 6000.0},
            },
            cycle_id="cycle-006",
        )
        items = {item["ticker"]: item["amount_usd"] for item in signal["target_reduction"]}
        # SPY: peak=10000, current=8000 -> drawdown=2000 (clamped at 0: yes, >0)
        assert items["SPY"] == pytest.approx(2000.0, rel=1e-6)
        # QQQ: peak=5000, current=6000 -> drawdown=-1000, clamped at 0 -> NOT included or 0
        assert items.get("QQQ", 0.0) == pytest.approx(0.0, abs=1e-6), (
            "Negative drawdown contribution (gain vs peak) must be clamped at 0"
        )

    def test_amount_usd_is_positive_float(self):
        """All amount_usd values must be positive floats (not negative, not zero, not None)."""
        signal = build_port_signal(
            port_state=_make_port_state(
                triggered=True,
                triggered_reason="hwm_trailing_stop",
                ticker_drawdowns={"SPY": 5000.0},
            ),
            cycle_id="cycle-007",
        )
        for item in signal["target_reduction"]:
            assert isinstance(item["amount_usd"], float)
            assert item["amount_usd"] > 0, "amount_usd must be a positive float"


class TestPortSignalSnapshotPersistence:
    """AC-P2.6.4: On every triggered port signal, last_target_reduction_json is updated."""

    def test_triggered_signal_updates_last_target_reduction(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_sig.db"))
        import database
        database.init_db()

        import json
        from database import write_port_state, read_port_state

        write_port_state("acct-sig", {"composition_hash": "h1"})

        signal = {
            "triggered": True,
            "triggered_reason": "hwm_trailing_stop",
            "fired_at_cycle": "cycle-008",
            "target_reduction": [{"ticker": "SPY", "amount_usd": 3000.0, "reason_for_this_ticker": "hwm_drawdown"}],
            "port_total_reduction_usd": 3000.0,
        }

        from port_aggregator import persist_port_signal_snapshot
        persist_port_signal_snapshot("acct-sig", signal)

        result = read_port_state("acct-sig")
        stored = json.loads(result["last_target_reduction_json"])
        assert stored["triggered"] is True
        assert stored["port_total_reduction_usd"] == pytest.approx(3000.0, rel=1e-6)

    def test_non_triggered_signal_does_not_persist_target_reduction(self, tmp_path, monkeypatch):
        """AC-P2.6.3: Non-triggered signal must NOT persist target_reduction."""
        monkeypatch.setenv("DB_PATH", str(tmp_path / "test_sig2.db"))
        import database
        database.init_db()
        from database import write_port_state, read_port_state

        write_port_state("acct-nosig", {"composition_hash": "h2", "last_target_reduction_json": None})

        signal = {
            "triggered": False,
            "triggered_reason": None,
            "fired_at_cycle": "cycle-009",
            "target_reduction": [],
            "port_total_reduction_usd": 0.0,
        }

        from port_aggregator import persist_port_signal_snapshot
        persist_port_signal_snapshot("acct-nosig", signal)

        result = read_port_state("acct-nosig")
        assert result["last_target_reduction_json"] is None, (
            "AC-P2.6.3: last_target_reduction_json must not be updated on non-triggered signal"
        )


class TestPortSignalAuthorityGating:
    """AC-P2.6.5: When EXIT_AUTHORITY=per_symphony, port signal is computed but not actioned."""

    def test_port_signal_computed_under_per_symphony_authority(self, monkeypatch):
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        signal = build_port_signal(
            port_state=_make_port_state(
                triggered=True,
                triggered_reason="hwm_trailing_stop",
                ticker_drawdowns={"SPY": 5000.0},
            ),
            cycle_id="cycle-010",
        )
        # Signal IS computed (triggered=True, target_reduction non-empty)
        assert signal["triggered"] is True
        assert len(signal["target_reduction"]) > 0

    def test_port_signal_not_in_actioning_path_under_per_symphony_authority(self, monkeypatch):
        """
        AC-P2.6.5: The port signal result under per_symphony authority must carry
        an 'actioned' flag = False (the caller checks this before order placement).
        """
        monkeypatch.setenv("EXIT_AUTHORITY", "per_symphony")
        from port_aggregator import build_port_signal_with_authority
        result = build_port_signal_with_authority(
            port_state=_make_port_state(
                triggered=True,
                triggered_reason="hwm_trailing_stop",
                ticker_drawdowns={"SPY": 5000.0},
            ),
            cycle_id="cycle-011",
            exit_authority="per_symphony",
        )
        assert result["actioned"] is False, (
            "AC-P2.6.5: port signal must not be actioned when EXIT_AUTHORITY=per_symphony"
        )
