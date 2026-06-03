"""
RED tests — guard-alpha divergence formula correctness.

ADJUDICATOR VERDICT (2026-06-03, definitive):
  CLAIM 1 (double-count) CONFIRMED — but the correct fix is NOT removing if_held.
  The current code computes:
      dry_run = if_held + (∏(1 + shadow/100) − 1) × 100

  The bug: (∏(shadow) − 1) is the ABSOLUTE shadow cumulative, not the guard DIVERGENCE.
  For untriggered symphonies shadow_return == current_return every day, so
  (∏(shadow) − 1) equals (∏(current) − 1), which is non-zero. The code then adds this
  to if_held, producing a non-zero Guard Alpha for symphonies the guard never touched
  — fabricating up to −6.5% phantom "damage" (verified live on alphabot_state.db).

  CORRECT formula:
      divergence = (∏(1 + shadow/100) − ∏(1 + current/100)) × 100
      dry_run = if_held + divergence

  For untriggered: shadow_r == current_r always → ∏shadow == ∏current → divergence == 0
  → dry_run == if_held → Guard Alpha == 0 EXACTLY.
  For triggered: shadow frozen at exit level, current keeps moving → divergence captures
  exactly what the guard saved or cost.

  CLAIM 2 (post-trigger freeze) REFUTED — no code change needed.

  IMPLEMENTATION REQUIREMENT:
  _get_shadow_cumulative_trajectory (analytics.py:560) must return BOTH shadow_return
  AND current_return per day (currently returns shadow_return only). The CR consumer
  (analytics.py:694-698) must use both to compute the divergence formula above.
  The MDD consumer (analytics.py:730-747) must build the bot's equity series using
  (if_held + cumulative_divergence_at_each_step) as the running level, not the
  absolute shadow cumulative series.

FIXTURE PROVENANCE:
  tests/fixtures/math/shadow_divergence_golden.json — schema-derived.
  Expected values derived in-test from the divergence formula. No producer output.

TOLERANCE:
  Untriggered: abs=1e-9 — divergence MUST be identically zero (not approximately).
  Triggered: abs=1e-6 — float arithmetic on small values.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

import database as _db
from analytics import get_symphony_cumulative_return, get_symphony_max_drawdown

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE = (
    Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "shadow_divergence_golden.json"
)


def _load() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Shadow DB helpers
# ---------------------------------------------------------------------------

_SCHEMA = """
    CREATE TABLE shadow_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symphony_id TEXT NOT NULL,
        ts_utc TEXT NOT NULL,
        trading_day TEXT NOT NULL,
        current_return REAL NOT NULL,
        shadow_return REAL NOT NULL,
        is_post_trigger INTEGER NOT NULL DEFAULT 0,
        position_epoch TEXT
    )
"""


def _make_db(tmp_path: Path, sym_id: str, rows: list[dict]) -> str:
    """Build a shadow_history DB from a list of row dicts.

    Each dict must have: trading_day, shadow_return, current_return, ts_utc.
    is_post_trigger defaults to 0.
    """
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    db_file = str(Path(tmp_path) / f"shadow_{sym_id[:12]}.db")
    conn = sqlite3.connect(db_file)
    conn.execute(_SCHEMA)
    epoch = "2026-01-04T14:30:00Z"
    for row in rows:
        conn.execute(
            "INSERT INTO shadow_history "
            "(symphony_id, ts_utc, trading_day, current_return, shadow_return, "
            "is_post_trigger, position_epoch) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                sym_id,
                row["ts_utc"],
                row["trading_day"],
                row["current_return"],
                row["shadow_return"],
                row.get("is_post_trigger", 0),
                epoch,
            ),
        )
    conn.commit()
    conn.close()
    _db._shadow_cr_cache.clear()
    return db_file


def _sym(sym_id: str, if_held_pct: float, max_dd_pct: float = 5.0) -> dict:
    return {
        "id": sym_id,
        "simple_return": if_held_pct / 100.0,
        "net_deposits": 1000.0,
        "time_weighted_return": if_held_pct / 100.0,
        "max_drawdown": max_dd_pct / 100.0,
    }


def _divergence(shadow_returns: list[float], current_returns: list[float]) -> float:
    """(∏(1+s/100) − ∏(1+c/100)) × 100. The correct guard-alpha divergence."""
    ps = 1.0
    pc = 1.0
    for s, c in zip(shadow_returns, current_returns):
        ps *= 1.0 + s / 100.0
        pc *= 1.0 + c / 100.0
    return (ps - pc) * 100.0


# ---------------------------------------------------------------------------
# KILLER TEST — never-triggered symphony MUST have Guard Alpha == 0 exactly
# ---------------------------------------------------------------------------


class TestNeverTriggeredAlphaIsExactlyZero:
    """
    The anchor invariant: if the guard never triggered, its cumulative alpha
    MUST be exactly zero.

    Current code FAILS this: (∏(shadow) − 1) is non-zero even when shadow ==
    current every day, because both series diverge from 1.0 with the same
    market moves. The code then adds this non-zero number to if_held, producing
    fabricated phantom guard "damage" or "gain".

    Live evidence: alphabot_state.db shows never-triggered symphonies
    (n2ooA, 5XjzX, MoAkU, all triggered=False) showing non-zero negative Guard
    Alpha of −0.65% to −4.20%.

    Fixture: scenarios.untriggered_never_touched from shadow_divergence_golden.json.
    """

    def test_never_triggered_guard_alpha_is_zero(self, tmp_path):
        """
        ANCHOR INVARIANT: a symphony with shadow_return == current_return on every
        day (guard never acted) must yield dry_run == if_held exactly.
        Guard Alpha (dry_run − if_held) must be 0.0000%.

        Current code FAILS: (∏(shadow) − 1) ≠ 0 for non-zero market returns,
        so dry_run ≠ if_held even when shadow ≡ current.
        """
        fixture = _load()["scenarios"]["untriggered_never_touched"]
        rows = fixture["shadow_history_rows"]
        if_held = fixture["if_held_pct"]
        sym_id = "sym_never_triggered"

        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=if_held)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        assert result is not None and "dry_run" in result, (
            f"ANCHOR FAIL: get_symphony_cumulative_return returned {result!r}"
        )

        # Guard Alpha = dry_run − if_held. For a never-triggered symphony this MUST be 0.
        guard_alpha = result["dry_run"] - if_held
        assert guard_alpha == pytest.approx(0.0, abs=1e-9), (
            f"ANCHOR INVARIANT FAIL: never-triggered symphony shows Guard Alpha "
            f"= {guard_alpha:.6f}% (dry_run={result['dry_run']:.6f}%, if_held={if_held}%). "
            "A symphony the guard never touched must have exactly 0% cumulative alpha. "
            "Bug: analytics.py:698 computes (∏shadow − 1) which is non-zero even when "
            "shadow == current. Correct formula: divergence = (∏shadow − ∏current)*100 = 0 "
            "when shadow_return == current_return every day."
        )

    def test_never_triggered_dry_run_equals_if_held(self, tmp_path):
        """
        Corollary: dry_run == if_held exactly when guard never fired.
        The bot and held series must coincide at the inception anchor.
        """
        fixture = _load()["scenarios"]["untriggered_never_touched"]
        rows = fixture["shadow_history_rows"]
        if_held = fixture["if_held_pct"]
        sym_id = "sym_never_triggered_2"

        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=if_held)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        assert result["dry_run"] == pytest.approx(if_held, abs=1e-9), (
            f"ANCHOR FAIL: dry_run={result['dry_run']:.6f}% != if_held={if_held}% "
            "for a never-triggered symphony. The bot and held series must coincide "
            "when the guard never acted."
        )

    def test_never_triggered_formula_property_multiple_if_held_values(self, tmp_path):
        """
        Property: for ANY if_held baseline, a never-triggered symphony must yield
        dry_run == if_held regardless of what if_held is.
        Tests if_held ∈ {−5.0%, 0.0%, 15.0%, 50.0%} with the same shadow trajectory.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return": -1.5, "current_return": -1.5, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return":  2.3, "current_return":  2.3, "ts_utc": "2026-01-06T21:00:00Z"},
            {"trading_day": "2026-01-07", "shadow_return":  0.4, "current_return":  0.4, "ts_utc": "2026-01-07T21:00:00Z"},
        ]

        for i, if_held_test in enumerate([-5.0, 0.0, 15.0, 50.0]):
            sym_id = f"sym_prop_{i}"
            db_file = _make_db(tmp_path / f"db_{i}", sym_id, rows)
            sym = {
                "id": sym_id,
                "simple_return": if_held_test / 100.0,
                "net_deposits": 500.0,
                "time_weighted_return": if_held_test / 100.0,
                "max_drawdown": 0.05,
            }
            result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)
            guard_alpha = result["dry_run"] - if_held_test
            assert guard_alpha == pytest.approx(0.0, abs=1e-9), (
                f"PROPERTY FAIL with if_held={if_held_test}%: guard_alpha={guard_alpha:.6f}%. "
                "Guard alpha must be 0% for a never-triggered symphony regardless of if_held."
            )


# ---------------------------------------------------------------------------
# GOLDEN — triggered symphony: divergence captures exactly what guard saved/cost
# ---------------------------------------------------------------------------


class TestTriggeredDivergenceGolden:
    """
    GOLDEN fixture: a triggered symphony shows the correct divergence.

    3-day position:
      days 1-2: pre-trigger, shadow_return == current_return (guard not yet armed)
      day 3: trigger fires. shadow_return = -2.0% (exit level). current_return = +0.3%
             (market moved up after exit — guard "cost" by exiting early)

    Correct divergence = (∏shadow − ∏current) × 100
    = (0.99×1.005×0.98 − 1.01×1.005×1.003) × 100
    = (0.994749 − 1.018095) × 100
    = −2.3346%

    dry_run = if_held + (−2.3346) = 5.0 + (−2.3346) = 2.6654%
    Guard Alpha = dry_run − if_held = −2.3346% (guard exited too early)

    Fixture: scenarios.triggered_guard_acted from shadow_divergence_golden.json.
    """

    def test_triggered_dry_run_equals_if_held_plus_divergence(self, tmp_path):
        """
        GOLDEN: dry_run = if_held + (∏shadow − ∏current)*100.
        Tolerance: abs=1e-6 (float arithmetic on small values).
        """
        fixture = _load()["scenarios"]["triggered_guard_acted"]
        rows = fixture["shadow_history_rows"]
        if_held = fixture["if_held_pct"]
        expected_dry_run = fixture["expected_dry_run_pct"]
        sym_id = "sym_triggered_golden"

        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=if_held)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        assert result["dry_run"] == pytest.approx(expected_dry_run, abs=1e-6), (
            f"GOLDEN FAIL: dry_run={result['dry_run']:.6f}%, "
            f"expected {expected_dry_run:.6f}%. "
            "Correct formula: dry_run = if_held + (∏shadow − ∏current)*100."
        )

    def test_triggered_guard_alpha_negative_when_exit_premature(self, tmp_path):
        """
        Triggered symphony where market rose after exit: guard alpha must be
        negative (guard cost the portfolio by exiting). The value must equal
        the divergence formula, not be fabricated from the absolute shadow series.
        """
        fixture = _load()["scenarios"]["triggered_guard_acted"]
        rows = fixture["shadow_history_rows"]
        if_held = fixture["if_held_pct"]
        expected_divergence = fixture["expected_divergence_pct"]
        sym_id = "sym_triggered_neg_alpha"

        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=if_held)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)
        guard_alpha = result["dry_run"] - if_held

        assert guard_alpha == pytest.approx(expected_divergence, abs=1e-6), (
            f"GOLDEN FAIL: guard_alpha={guard_alpha:.6f}%, "
            f"expected {expected_divergence:.6f}% (negative = guard exited too early)."
        )


# ---------------------------------------------------------------------------
# MDD — bot drawdown uses the shadow-vs-current equity path
# ---------------------------------------------------------------------------


class TestMddOnBotEquityPath:
    """
    get_symphony_max_drawdown dry_run must reflect peak-to-trough on the BOT's
    equity path, which is defined by the cumulative divergence:

      bot_equity[t] = if_held + (∏shadow[0..t] − ∏current[0..t]) × 100

    The current code builds the MDD series from ∏shadow alone (analytics.py:733-737),
    which is the ABSOLUTE shadow trajectory, not the relative guard effect.

    For an untriggered symphony, the bot equity path is flat at if_held — MDD == 0.
    For a triggered symphony, the bot equity series correctly captures the loss
    incurred on the exit day.
    """

    def test_never_triggered_mdd_is_zero(self, tmp_path):
        """
        Untriggered symphony: bot equity is flat at if_held (shadow == current
        every day → divergence == 0 at every step). MDD of a flat series == 0.
        Current code FAILS: (∏shadow - 1) builds a non-flat cum_series even when
        shadow == current, yielding non-zero MDD.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return": 1.0, "current_return": 1.0, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": -2.0, "current_return": -2.0, "ts_utc": "2026-01-06T21:00:00Z"},
            {"trading_day": "2026-01-07", "shadow_return": 0.5, "current_return": 0.5, "ts_utc": "2026-01-07T21:00:00Z"},
        ]
        sym_id = "sym_mdd_untriggered"
        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=8.0, max_dd_pct=5.0)

        result = get_symphony_max_drawdown(sym, bot_state_entry=None, db_path=db_file)

        assert result["dry_run"] is not None, "dry_run must not be None with 3 shadow rows"
        assert result["dry_run"] == pytest.approx(0.0, abs=1e-9), (
            f"MDD ANCHOR FAIL: never-triggered symphony has bot MDD "
            f"= {result['dry_run']:.6f}% (should be exactly 0%). "
            "When shadow == current, the bot equity is flat at if_held — no drawdown. "
            "Bug: analytics.py:733-737 builds cum_series from ∏shadow, which is not flat "
            "even when shadow == current, producing phantom MDD."
        )

    def test_triggered_mdd_equals_peak_to_trough_on_divergence_series(self, tmp_path):
        """
        GOLDEN MDD: triggered symphony.

        Bot equity series (from fixture):
          day 1: if_held + (∏shadow[1] − ∏current[1])*100 = 5.0 + 0.0 = 5.0
          day 2: if_held + (∏shadow[2] − ∏current[2])*100 = 5.0 + 0.0 = 5.0
          day 3: if_held + (∏shadow[3] − ∏current[3])*100 = 5.0 + (−2.3346) = 2.6654

        Peak = 5.0 (days 1-2), trough = 2.6654 (day 3).
        MDD = 5.0 − 2.6654 = 2.3346%.
        """
        fixture = _load()["scenarios"]["triggered_guard_acted"]
        rows = fixture["shadow_history_rows"]
        if_held = fixture["if_held_pct"]
        expected_mdd = fixture["expected_mdd_pct"]
        sym_id = "sym_mdd_triggered"

        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=if_held, max_dd_pct=10.0)

        result = get_symphony_max_drawdown(sym, bot_state_entry=None, db_path=db_file)

        assert result["dry_run"] == pytest.approx(expected_mdd, abs=1e-6), (
            f"MDD GOLDEN FAIL: dry_run={result['dry_run']:.6f}%, "
            f"expected {expected_mdd:.6f}% (peak-to-trough on bot equity path). "
            "MDD must be built from the divergence-based equity series, "
            "not the absolute shadow cumulative."
        )
