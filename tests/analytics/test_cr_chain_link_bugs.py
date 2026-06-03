"""
RED tests — chain-link CR/MDD bugs in analytics.py.

Independent falsifier verdict (2026-06-03):
  shadow_return is PER-DAY (today's change), NOT cumulative-since-open.
  Proof: (a) current_return resets to 0.000 at each 09:30 open — a
  cumulative-since-open value cannot reset to 0 daily; (b) HWM is reset
  to -999 in database.py:342-343 via wipe_transient_state every new trading
  day (alpha_bot_execution.py:708-713).

There are TWO real bugs in analytics.py, NOT the cumulative-snapshot issue:

BUG (a) — DOUBLE-COUNT in CR (:695-698):
  Code: dry_run = if_held + (∏(1 + r_day/100) - 1) * 100
  Problem: (∏-1)*100 already reconstructs the full from-window-start
  cumulative return of the bot's series. Adding if_held (itself the held
  cumulative return) double-counts the baseline entirely.
  Correct: bot_CR = (∏(1 + r_day/100) - 1) * 100  (no if_held addition).
  The dashboard compares bot_CR vs held_CR separately; divergence =
  bot_CR - held_CR. The if_held offset is already rendered independently.

BUG (b) — POST-TRIGGER FREEZE fabricates divergence (:910-916):
  When triggered, shadow_return is frozen at triggered_at_return (a single
  realized daily return from the exit day). Subsequent days in the same
  position epoch all record the SAME frozen value. Chain-linking that frozen
  value repeatedly as if it's a fresh per-day return compounds it into
  spurious post-exit divergence.
  Correct: post-trigger days should contribute 0% (the position is in cash
  after the exit; no further return accrues). Only the exit-day row's actual
  realized return is a real per-day contribution.

FIXTURE PROVENANCE:
  Shadow history rows are built inline (schema-derived from database.py migration
  + alpha_bot_execution.py:926-934). Expected values are derived in-test from
  the documented corrected formula — never captured from the producer.

TOLERANCE: rel=1e-9 throughout. Fixture values are small integers; IEEE-754
multiplication error is sub-femto-percent. Any deviation from the formula is a
consumer bug, not floating-point noise.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as _db
from analytics import get_symphony_cumulative_return, get_symphony_max_drawdown


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
    """
    Build a shadow_history DB from a list of row dicts.

    Each dict: trading_day (str), shadow_return (float), ts_utc (str),
    is_post_trigger (int, default 0).
    All rows share a fixed epoch (position-boundary tests live in
    test_shadow_trajectory_position_epoch.py; here we test arithmetic).
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
                row["shadow_return"],
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
    """Minimal sym_dict for analytics calls. if_held_pct is percent (e.g. 10.0)."""
    return {
        "id": sym_id,
        "simple_return": if_held_pct / 100.0,
        "net_deposits": 1000.0,   # non-zero -> simple_return path (no TWR fallback)
        "time_weighted_return": if_held_pct / 100.0,
        "max_drawdown": max_dd_pct / 100.0,
    }


def _chain_link(returns: list[float]) -> float:
    """(∏(1 + r/100)) - 1, expressed in percent. The correct bot CR formula."""
    product = 1.0
    for r in returns:
        product *= 1.0 + r / 100.0
    return (product - 1.0) * 100.0


# ---------------------------------------------------------------------------
# BUG (a) — Double-count: dry_run must equal chain_link only, NOT if_held +
# chain_link.  Tests will be RED against the current analytics.py which adds
# if_held to the chain-link product.
# ---------------------------------------------------------------------------


class TestCRDoubleCountBug:
    """
    Bug (a): analytics.py:698 computes
        dry_run = if_held + (∏(1+r_day/100) - 1) * 100

    (∏-1)*100 is already the bot's cumulative return from window-start.
    Adding if_held (the held cumulative) double-counts the baseline.

    Correct: dry_run = (∏(1+r_day/100) - 1) * 100   (no if_held addition).
    """

    def test_untriggered_flat_returns_cr_equals_chain_link_only(self, tmp_path):
        """
        BUG-A-1: A 3-day untriggered position with per-day returns [1.0, -0.5, 0.3].
        if_held = 10.0%.
        Bot CR (correct) = chain_link([1.0, -0.5, 0.3]) = ~0.7985%.
        Current (buggy) dry_run = 10.0 + 0.7985 = 10.7985%.
        This test asserts the corrected value.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return":  1.0, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": -0.5, "ts_utc": "2026-01-06T21:00:00Z"},
            {"trading_day": "2026-01-07", "shadow_return":  0.3, "ts_utc": "2026-01-07T21:00:00Z"},
        ]
        sym_id = "sym_double_count_1"
        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=10.0)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        per_day_returns = [1.0, -0.5, 0.3]
        correct_dry_run = _chain_link(per_day_returns)   # ~0.7985%
        buggy_dry_run = pytest.approx(10.0 + correct_dry_run, rel=1e-9)

        assert result["dry_run"] != buggy_dry_run, (
            f"BUG-A-1: dry_run={result['dry_run']:.4f}% matches the double-count "
            f"formula (if_held + chain_link = 10.0 + {correct_dry_run:.4f}). "
            "analytics.py:698 must NOT add if_held to the chain-link product. "
            "Bot CR is the chain-link alone; if_held is displayed separately."
        )
        assert result["dry_run"] == pytest.approx(correct_dry_run, rel=1e-9), (
            f"BUG-A-1: dry_run={result['dry_run']:.4f}%, expected "
            f"chain_link_only={correct_dry_run:.4f}%."
        )

    def test_zero_if_held_means_dry_run_equals_chain_link_exactly(self, tmp_path):
        """
        BUG-A-2: With if_held=0%, the double-count collapses (0 + chain_link =
        chain_link), so both the buggy and correct formula agree. This test
        confirms the no-double-count property holds when the baseline is zero,
        and documents the degenerate case for reviewers.

        This test PASSES with both the old and new code — it's a consistency
        anchor, not a discriminating test. Kept here to document that the fix
        must not break the zero-baseline case.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return": -1.0, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return":  2.0, "ts_utc": "2026-01-06T21:00:00Z"},
        ]
        sym_id = "sym_zero_base"
        # simple_return=0.0, net_deposits=0.0 triggers TWR fallback; use non-zero
        # net_deposits + simple_return=0.0 to avoid it.
        sym = {
            "id": sym_id,
            "simple_return": 0.0,
            "net_deposits": 500.0,   # non-zero -> uses simple_return path
            "time_weighted_return": 0.05,
            "max_drawdown": 0.02,
        }
        db_file = _make_db(tmp_path, sym_id, rows)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        expected = _chain_link([-1.0, 2.0])   # (0.99 * 1.02 - 1) * 100 = 0.98%
        # With if_held=0, both old and new formula yield chain_link. The fix must
        # preserve this.
        assert result["dry_run"] == pytest.approx(expected, rel=1e-9), (
            f"BUG-A-2: zero-baseline case broken: dry_run={result['dry_run']:.4f}%, "
            f"expected {expected:.4f}%."
        )

    def test_positive_if_held_dry_run_is_not_offset_from_if_held(self, tmp_path):
        """
        BUG-A-3: For a typical non-zero if_held (e.g. +15%), dry_run must NOT
        be close to if_held + chain_link. It must equal chain_link only.
        Specifically: if the per-day returns sum to a small positive divergence,
        dry_run should be near zero (bot and held nearly coincide), not near 15%.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return":  0.1, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": -0.1, "ts_utc": "2026-01-06T21:00:00Z"},
        ]
        sym_id = "sym_near_zero_div"
        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=15.0)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        correct_dry_run = _chain_link([0.1, -0.1])   # ≈ -0.0001% (almost zero)

        # Buggy answer would be ~15.0 + (-0.0001) ≈ 15.0 — near if_held.
        # Correct answer is near 0 — the bot's divergence from session-start is tiny.
        assert abs(result["dry_run"] - 15.0) > 1.0, (
            f"BUG-A-3: dry_run={result['dry_run']:.4f}% is near if_held (15%), "
            "which means it's carrying the held cumulative as an offset. "
            "With near-zero per-day divergence, bot_CR should be near 0%, "
            "not near the held baseline."
        )
        assert result["dry_run"] == pytest.approx(correct_dry_run, rel=1e-4), (
            # rel=1e-4 here: values are near-zero so relative tolerance is loose;
            # absolute difference is the discriminating check above.
            f"BUG-A-3: dry_run={result['dry_run']:.6f}%, expected {correct_dry_run:.6f}%."
        )


# ---------------------------------------------------------------------------
# BUG (b) — Post-trigger freeze: triggered days must contribute 0%, not the
# frozen triggered_at_return value compounded repeatedly.
# Tests will be RED against the current analytics.py which re-compounds the
# frozen value on every post-trigger day.
# ---------------------------------------------------------------------------


class TestPostTriggerFreezeCompoundingBug:
    """
    Bug (b): analytics.py:910-916 — post-trigger shadow_return is frozen at
    triggered_at_return (a single realized per-day value from the exit day).
    Subsequent EOD rows for the same epoch record the same frozen value.
    The chain-link then multiplies this frozen rate for every post-trigger day,
    as if the position keeps earning that return. This fabricates divergence
    for every day the position sits in cash post-exit.

    Correct: post-trigger days contribute 0% — the position is in cash, no
    return accrues. Only the exit-day row contributes its actual realized return.

    Row shape:
      day 1: pre-trigger, shadow_return = actual per-day, is_post_trigger=0
      day 2: exit day,    shadow_return = triggered_at_return (realized), is_post_trigger=1
      day 3: post-trigger, shadow_return = triggered_at_return (SAME frozen value), is_post_trigger=1
      day 4: post-trigger, shadow_return = triggered_at_return (SAME frozen value), is_post_trigger=1
    """

    def test_triggered_position_post_trigger_days_contribute_zero(self, tmp_path):
        """
        BUG-B-1: A 4-day sequence: 2 pre-trigger days + 1 exit day + 1
        cash day (same frozen shadow_return on exit + cash days).
        Pre-trigger: [+1.0, +0.5]. Exit day: shadow_return=-2.0, is_post_trigger=1.
        Cash day: shadow_return=-2.0 again (frozen), is_post_trigger=1.

        Correct bot_CR = chain_link([1.0, 0.5, -2.0, 0.0])
          (exit-day contributes its actual -2.0; cash day contributes 0%).
        Buggy bot_CR = chain_link([1.0, 0.5, -2.0, -2.0])
          (frozen -2.0 compounded again on the cash day).
        """
        frozen_exit_return = -2.0
        rows = [
            {
                "trading_day": "2026-01-05",
                "shadow_return": 1.0,
                "ts_utc": "2026-01-05T21:00:00Z",
                "is_post_trigger": 0,
            },
            {
                "trading_day": "2026-01-06",
                "shadow_return": 0.5,
                "ts_utc": "2026-01-06T21:00:00Z",
                "is_post_trigger": 0,
            },
            {
                "trading_day": "2026-01-07",
                "shadow_return": frozen_exit_return,   # exit day realized return
                "ts_utc": "2026-01-07T21:00:00Z",
                "is_post_trigger": 1,
            },
            {
                "trading_day": "2026-01-08",
                "shadow_return": frozen_exit_return,   # same frozen value — cash day
                "ts_utc": "2026-01-08T21:00:00Z",
                "is_post_trigger": 1,
            },
        ]
        sym_id = "sym_post_trigger_1"
        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=5.0)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        # Correct: exit-day contributes its return; cash day contributes 0%.
        correct_dry_run = _chain_link([1.0, 0.5, frozen_exit_return, 0.0])
        # Buggy: frozen value compounded on the cash day too.
        buggy_dry_run = pytest.approx(_chain_link([1.0, 0.5, frozen_exit_return, frozen_exit_return]), rel=1e-9)

        assert result["dry_run"] != buggy_dry_run, (
            f"BUG-B-1: dry_run={result['dry_run']:.4f}% matches the buggy formula "
            f"that compounds frozen exit return {frozen_exit_return}% on the cash day. "
            "Post-trigger days must contribute 0% — the position is in cash."
        )
        assert result["dry_run"] == pytest.approx(correct_dry_run, rel=1e-9), (
            f"BUG-B-1: dry_run={result['dry_run']:.4f}%, expected "
            f"{correct_dry_run:.4f}% (exit-day contributes, cash days are 0%)."
        )

    def test_triggered_mdd_post_trigger_cash_days_do_not_extend_drawdown(self, tmp_path):
        """
        BUG-B-2: MDD must not grow because of frozen post-trigger returns.
        A pre-trigger decline followed by trigger and cash days: the drawdown
        is locked at the exit-day level. The buggy code compounds the frozen
        exit return on each cash day, creating a deeper phantom drawdown.

        Pre-trigger: [+2.0, -3.0].  Exit day: -1.5.  Two cash days: -1.5, -1.5 (frozen).
        Correct MDD = peak-to-trough of chain_link over [2.0, -3.0, -1.5, 0.0, 0.0].
        Buggy MDD   = peak-to-trough of chain_link over [2.0, -3.0, -1.5, -1.5, -1.5].
        """
        frozen = -1.5
        rows = [
            {"trading_day": "2026-01-05", "shadow_return":  2.0, "ts_utc": "2026-01-05T21:00:00Z", "is_post_trigger": 0},
            {"trading_day": "2026-01-06", "shadow_return": -3.0, "ts_utc": "2026-01-06T21:00:00Z", "is_post_trigger": 0},
            {"trading_day": "2026-01-07", "shadow_return": frozen, "ts_utc": "2026-01-07T21:00:00Z", "is_post_trigger": 1},
            {"trading_day": "2026-01-08", "shadow_return": frozen, "ts_utc": "2026-01-08T21:00:00Z", "is_post_trigger": 1},
            {"trading_day": "2026-01-09", "shadow_return": frozen, "ts_utc": "2026-01-09T21:00:00Z", "is_post_trigger": 1},
        ]
        sym_id = "sym_post_trigger_mdd"
        db_file = _make_db(tmp_path, sym_id, rows)
        sym = _sym(sym_id, if_held_pct=4.0, max_dd_pct=3.0)

        result = get_symphony_max_drawdown(sym, bot_state_entry=None, db_path=db_file)

        def _mdd_from_series(returns: list[float]) -> float:
            product = 1.0
            cum: list[float] = []
            for r in returns:
                product *= 1.0 + r / 100.0
                cum.append((product - 1.0) * 100.0)
            if len(cum) < 2:
                return 0.0
            peak = cum[0]
            max_dd = 0.0
            for val in cum:
                if val > peak:
                    peak = val
                dd = peak - val
                if dd > max_dd:
                    max_dd = dd
            return max_dd

        correct_mdd = _mdd_from_series([2.0, -3.0, frozen, 0.0, 0.0])
        buggy_mdd   = pytest.approx(_mdd_from_series([2.0, -3.0, frozen, frozen, frozen]), rel=1e-9)

        assert result["dry_run"] != buggy_mdd, (
            f"BUG-B-2: dry_run MDD={result['dry_run']:.4f}% matches the buggy "
            f"formula that compounds frozen {frozen}% on each cash day, deepening "
            f"the drawdown. Post-trigger (cash) days must contribute 0%."
        )
        assert result["dry_run"] == pytest.approx(correct_mdd, rel=1e-9), (
            f"BUG-B-2: MDD dry_run={result['dry_run']:.4f}%, expected {correct_mdd:.4f}%."
        )


# ---------------------------------------------------------------------------
# REGRESSION LOCKS — behavior that MUST be preserved after the fix
# ---------------------------------------------------------------------------


class TestPreservedCorrectBehavior:
    """
    These cases already compute correctly under the current buggy code AND
    under the corrected formula — they are regression locks, not bug probes.

    An untriggered position with per-day returns that happen to net near-zero
    will read approximately correctly even with the double-count bug (when
    if_held itself is near zero). These tests confirm no regression in shape
    or sign conventions.
    """

    def test_untriggered_position_dry_run_shape(self, tmp_path):
        """
        Untriggered: result is dict with 'if_held' and 'dry_run' keys,
        both finite floats. Shape contract must hold after the fix.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return": 0.2, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": 0.3, "ts_utc": "2026-01-06T21:00:00Z"},
        ]
        db_file = _make_db(tmp_path, "sym_shape", rows)
        sym = _sym("sym_shape", if_held_pct=3.0)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        assert isinstance(result, dict), "result must be a dict"
        assert "if_held" in result and "dry_run" in result
        assert result["if_held"] == pytest.approx(3.0, rel=1e-9), (
            "if_held must be simple_return * 100 (unaffected by shadow trajectory)"
        )
        import math
        assert math.isfinite(result["dry_run"]), "dry_run must be a finite float"

    def test_fewer_than_two_shadow_days_dry_run_equals_if_held(self, tmp_path):
        """
        Fewer than 2 shadow days: trajectory returns None, dry_run must equal
        if_held (no recorded divergence). This must not change after the fix.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return": -1.0, "ts_utc": "2026-01-05T21:00:00Z"},
        ]
        db_file = _make_db(tmp_path, "sym_one_day", rows)
        sym = _sym("sym_one_day", if_held_pct=7.0)

        result = get_symphony_cumulative_return(sym, bot_state_entry=None, db_path=db_file)

        assert result["dry_run"] == pytest.approx(result["if_held"], rel=1e-9), (
            "With fewer than 2 shadow days, dry_run must equal if_held "
            "(no recorded divergence). This fallback must survive the fix."
        )

    def test_mdd_positive_magnitude_convention(self, tmp_path):
        """
        dry_run MDD must be a positive float (loss magnitude), never negative.
        Sign convention: positive = magnitude of drawdown.
        """
        rows = [
            {"trading_day": "2026-01-05", "shadow_return":  2.0, "ts_utc": "2026-01-05T21:00:00Z"},
            {"trading_day": "2026-01-06", "shadow_return": -3.0, "ts_utc": "2026-01-06T21:00:00Z"},
        ]
        db_file = _make_db(tmp_path, "sym_mdd_sign", rows)
        sym = _sym("sym_mdd_sign", if_held_pct=5.0, max_dd_pct=4.0)

        result = get_symphony_max_drawdown(sym, bot_state_entry=None, db_path=db_file)
        assert result["dry_run"] is not None
        assert result["dry_run"] >= 0.0, (
            f"dry_run MDD={result['dry_run']:.4f}% must be >= 0 "
            "(positive-magnitude convention). The fix must preserve sign."
        )
