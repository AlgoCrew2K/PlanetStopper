"""
RED tests for AC-10 and AC-11 — the port-level exit-state lifecycle defects
surfaced by post-audit verification (the port-level analogue of the
per-symphony C-2 finding).

AC-10 (port-level C-2 analogue):
  database.rebase_port_state_on_composition_change resets several
  momentum-tracking fields when the portfolio composition changes, but does
  NOT reset `triggered` or `triggered_reason`. A composition change that
  leaves a stale triggered=True makes build_port_signal emit a SPURIOUS
  port-wide exit signal on the first cycle of the new composition. The fix
  adds `triggered` and `triggered_reason` to the reset payload.

AC-11 (port-level daily reset):
  There is no port-level daily-reset path that wipes the port_state
  transient exit-guard fields (`triggered`, `triggered_reason`, `armed`,
  `para_armed`, `port_breakeven_active`) at the start of a new trading day.
  new_day_reset_port_state currently only resets `prev_return`. The fix
  extends it (or adds a sibling path) to wipe the transient exit-guard
  fields — the port_state analogue of database.wipe_transient_state.

Provenance: port_state rows are schema-derived (database._PORT_STATE_COLUMNS).
Expected values assert SHAPE (field is reset to a sentinel/falsy value),
never a producer-computed number.
"""

from __future__ import annotations

import json

import pytest


from database import (  # noqa: F401
    new_day_reset_port_state,
    read_port_state,
    rebase_port_state_on_composition_change,
    write_port_state,
)


@pytest.fixture()
def mem_db(tmp_path, monkeypatch):
    """Isolated SQLite DB per test (mirrors test_composition_change_reset.py)."""
    db_path = str(tmp_path / "test_port_lifecycle.db")
    monkeypatch.setenv("DB_PATH", db_path)
    import database
    database.init_db()
    yield db_path


def _falsy(value) -> bool:
    """SQLite stores bools as 0/1; a reset boolean field is 0 / False / None."""
    return value in (0, False, None)


# ===========================================================================
# AC-10 — rebase_port_state_on_composition_change resets triggered fields
# ===========================================================================


class TestCompositionChangeResetsTriggeredState:
    """AC-10: a stale triggered=True must NOT survive a composition change."""

    def test_rebase_resets_triggered_to_false(self, mem_db):
        """
        AC-10: when the portfolio composition changes, a stale triggered=True
        from the prior composition must be reset to a falsy value.

        Without this, build_port_signal sees triggered=True on the first
        cycle of the new composition and emits a spurious port-wide exit
        signal — a real-money go-to-cash on a composition that never
        actually tripped a stop.

        RED: rebase_port_state_on_composition_change currently does NOT
        include `triggered` in its reset payload, so the True survives.
        """
        write_port_state("acct-trig", {
            "composition_hash": "old-comp",
            "triggered": True,
            "triggered_reason": "Trailing Stop",
            "high_water_mark": 100000.0,
        })

        rebase_port_state_on_composition_change(
            "acct-trig",
            new_composition_hash="new-comp",
            current_port_value=105000.0,
        )

        result = read_port_state("acct-trig")
        assert _falsy(result["triggered"]), (
            f"AC-10 VIOLATED: triggered={result['triggered']!r} survived a "
            f"composition change. A stale triggered=True makes "
            f"build_port_signal emit a spurious port-wide exit on the first "
            f"cycle of the new composition. rebase_port_state_on_composition_"
            f"change must reset `triggered` alongside the fields it already "
            f"resets."
        )

    def test_rebase_resets_triggered_reason(self, mem_db):
        """
        AC-10: `triggered_reason` must also be cleared on a composition
        change — a stale reason string ("Trailing Stop", "VWAP Breakdown")
        from the prior composition is meaningless for the new one and would
        mislabel any telemetry/alert keyed off it.

        RED: triggered_reason is not in the rebase reset payload.
        """
        write_port_state("acct-reason", {
            "composition_hash": "old",
            "triggered": True,
            "triggered_reason": "VWAP Breakdown",
        })

        rebase_port_state_on_composition_change(
            "acct-reason",
            new_composition_hash="new",
            current_port_value=100000.0,
        )

        result = read_port_state("acct-reason")
        assert result["triggered_reason"] in (None, ""), (
            f"AC-10 VIOLATED: triggered_reason={result['triggered_reason']!r} "
            f"survived a composition change. It must be cleared to None (or "
            f"empty) — a prior composition's exit reason does not apply to "
            f"the new composition."
        )

    def test_rebase_still_resets_previously_covered_fields(self, mem_db):
        """
        AC-10 regression guard: adding `triggered`/`triggered_reason` to the
        reset payload must NOT drop the fields rebase already resets
        (prev_return, mc_history_json, vwap counters, armed, para_armed,
        port_breakeven_active, composition_hash, high_water_mark).

        This pins the existing AC-P2.5.5 contract so the AC-10 change is
        purely additive.
        """
        write_port_state("acct-keep", {
            "composition_hash": "old",
            "triggered": True,
            "triggered_reason": "Take-Profit",
            "high_water_mark": 200000.0,
            "prev_return": 0.05,
            "mc_history_json": json.dumps([0.6, 0.7]),
            "vwap_ticks_json": json.dumps([1, 2]),
            "vwap_bleed_ticks_json": json.dumps([3]),
            "armed": True,
            "para_armed": True,
            "port_breakeven_active": True,
        })

        rebase_port_state_on_composition_change(
            "acct-keep",
            new_composition_hash="fresh",
            current_port_value=180000.0,
        )

        result = read_port_state("acct-keep")
        assert result["composition_hash"] == "fresh"
        assert result["prev_return"] is None
        assert json.loads(result["mc_history_json"]) == []
        assert json.loads(result["vwap_ticks_json"]) == []
        assert json.loads(result["vwap_bleed_ticks_json"]) == []
        assert _falsy(result["armed"])
        assert _falsy(result["para_armed"])
        assert _falsy(result["port_breakeven_active"])
        assert result["high_water_mark"] == pytest.approx(180000.0, rel=1e-6)
        # And the AC-10 additions:
        assert _falsy(result["triggered"])
        assert result["triggered_reason"] in (None, "")

    def test_rebase_clears_triggered_even_when_already_false(self, mem_db):
        """
        AC-10 idempotence: rebasing a port_state whose triggered is already
        False must leave it False (the reset is unconditional, not a toggle).
        """
        write_port_state("acct-idem", {
            "composition_hash": "old",
            "triggered": False,
            "triggered_reason": None,
        })
        rebase_port_state_on_composition_change(
            "acct-idem",
            new_composition_hash="new",
            current_port_value=100000.0,
        )
        result = read_port_state("acct-idem")
        assert _falsy(result["triggered"])
        assert result["triggered_reason"] in (None, "")


# ===========================================================================
# AC-11 — port-level daily reset wipes transient exit-guard fields
# ===========================================================================


class TestPortStateDailyResetWipesExitGuards:
    """
    AC-11: a new trading day must wipe the port_state transient exit-guard
    fields — the port_state analogue of database.wipe_transient_state.
    """

    # The five transient exit-guard fields AC-11 names explicitly.
    _EXIT_GUARD_FIELDS = (
        "triggered",
        "triggered_reason",
        "armed",
        "para_armed",
        "port_breakeven_active",
    )

    def test_new_day_reset_wipes_all_transient_exit_guard_fields(self, mem_db):
        """
        AC-11: at the start of a new trading day the port-level reset path
        must clear `triggered`, `triggered_reason`, `armed`, `para_armed`,
        and `port_breakeven_active`.

        Without this, a port_state that ended yesterday in a triggered /
        armed state carries that stale guard into the new day — e.g. a
        triggered=True survives overnight and build_port_signal fires a
        spurious go-to-cash on the first cycle of the new day.

        RED: new_day_reset_port_state currently only resets `prev_return`.
        """
        write_port_state("acct-newday", {
            "triggered": True,
            "triggered_reason": "Trailing Stop",
            "armed": True,
            "para_armed": True,
            "port_breakeven_active": True,
            "prev_return": 0.03,
        })

        new_day_reset_port_state("acct-newday")

        result = read_port_state("acct-newday")
        for field in self._EXIT_GUARD_FIELDS:
            if field == "triggered_reason":
                assert result[field] in (None, ""), (
                    f"AC-11 VIOLATED: new_day_reset_port_state did not clear "
                    f"'{field}'. Got {result[field]!r}. A stale exit reason "
                    f"must not carry into the new trading day."
                )
            else:
                assert _falsy(result[field]), (
                    f"AC-11 VIOLATED: new_day_reset_port_state did not reset "
                    f"'{field}' to a falsy value. Got {result[field]!r}. The "
                    f"port-level new-day reset is the port_state analogue of "
                    f"wipe_transient_state and must wipe every transient "
                    f"exit-guard field."
                )

    def test_new_day_reset_still_clears_prev_return(self, mem_db):
        """
        AC-11 regression guard: the existing AC-P2.5.4 behavior
        (prev_return -> None, preventing PARA-ARM on the opening gap) must
        be preserved when the exit-guard wipe is added.
        """
        write_port_state("acct-prev", {
            "prev_return": 0.07,
            "triggered": True,
        })
        new_day_reset_port_state("acct-prev")
        result = read_port_state("acct-prev")
        assert result["prev_return"] is None, (
            "new_day_reset_port_state must still reset prev_return to None "
            "(AC-P2.5.4) — the AC-11 exit-guard wipe is additive."
        )

    def test_new_day_reset_is_noop_when_no_port_state_row_exists(self, mem_db):
        """
        AC-11 robustness: calling new_day_reset_port_state for an account
        with no port_state row must be a safe no-op (no crash, no row
        created). Mirrors the existing guard in new_day_reset_port_state.
        """
        # No write_port_state — the row does not exist.
        new_day_reset_port_state("acct-absent")
        result = read_port_state("acct-absent")
        assert result is None, (
            "new_day_reset_port_state must not create a port_state row for "
            "an account that has none — it is a reset, not an upsert."
        )

    def test_new_day_reset_wipes_exit_guards_even_when_already_clean(
        self, mem_db
    ):
        """
        AC-11 idempotence: running the new-day reset on an already-clean
        port_state leaves every exit-guard field falsy. The wipe is
        unconditional.
        """
        write_port_state("acct-clean", {
            "triggered": False,
            "triggered_reason": None,
            "armed": False,
            "para_armed": False,
            "port_breakeven_active": False,
            "prev_return": None,
        })
        new_day_reset_port_state("acct-clean")
        result = read_port_state("acct-clean")
        for field in self._EXIT_GUARD_FIELDS:
            if field == "triggered_reason":
                assert result[field] in (None, "")
            else:
                assert _falsy(result[field])

    def test_triggered_true_does_not_survive_new_day(self, mem_db):
        """
        AC-11 — the load-bearing scenario in isolation: a port_state that
        ended a trading day with triggered=True must start the next day with
        triggered cleared. This is the exact stale-state path the audit's
        port-level C-2 analogue flagged.
        """
        write_port_state("acct-overnight", {"triggered": True})
        new_day_reset_port_state("acct-overnight")
        result = read_port_state("acct-overnight")
        assert _falsy(result["triggered"]), (
            "A triggered=True port_state survived overnight into a new "
            "trading day. The new-day reset must clear it so the first "
            "cycle of the new day does not emit a spurious port-wide exit."
        )
