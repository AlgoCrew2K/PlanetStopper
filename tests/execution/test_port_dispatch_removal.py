"""
RED tests for port-execution-dispatch-removal (Sprint 3, Stream A).

These tests assert the POST-removal contract for SITE-A1 through SITE-A5 per
docs/audit/sprint-3-port-removal-manifest.md.  They will all FAIL against the
current (pre-removal) source and must all PASS once the implementer completes
the removal.

Removal targets:
  SITE-A1: get_exit_authority() call at line ~1000; import no longer needed.
  SITE-A2: is_authoritative(altitude="port_level", …) block at lines 1535–1664.
  SITE-A3: initialize_port_state_if_absent call at lines 1024–1028.
  SITE-A4: port_symphony_snapshots dict declaration at lines 1018–1021.
  SITE-A5: is_authoritative guard on per-symphony execution queue at lines 1673–1675;
           replaced by unconditional ``if execution_queue:``.

What must NOT change (architecture invariants):
  - Dashboard display reads (app.py SITE-D1/D2/D3) — untouched.
  - Symphony-level decision math — fires exactly as before.
  - LIVE_EXECUTION defaults False — default never changes.
  - Manual perform_account_liquidation button — untouched, not exercised here.

Mocking philosophy mirrors test_main_pipeline.py:
  - network, DB, time, and reporting are mocked; math engine is NOT mocked
    wholesale (only surgically patched for trigger-force scenarios).
  - Fixture-derived rule: per project memory ``feedback_no_hardcoded_test_values``,
    assertions touching producer-computed values derive from fixture inputs, never
    from inline expected literals produced by the math engine.
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import patch

import pytest

import alpha_bot_execution

# Reuse the shared harness from the B1 pipeline tests — same shape fixtures,
# same patch infrastructure.
from tests.execution.test_main_pipeline import (
    _SYMPHONY_ID,
    _TICKER,
    _make_symphony_payload,
    _make_vwap_payload,
    _seed_state,
    patched_environment,  # noqa: F401  (re-exported pytest fixture)
)

# ---------------------------------------------------------------------------
# Helper: get the parsed AST of the current alpha_bot_execution source.
# Used by structural assertion tests so they inspect source text rather than
# runtime behaviour — the import machinery would mask removed symbols if an
# older .pyc is still loaded.
# ---------------------------------------------------------------------------


def _get_abe_source() -> str:
    return inspect.getsource(alpha_bot_execution)


def _get_abe_ast() -> ast.Module:
    return ast.parse(_get_abe_source())


def _ast_names_in_import(tree: ast.Module) -> set[str]:
    """Return every name imported at module level (top-level import / from-import)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) or isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


# ===========================================================================
# Group 1 — Structural: removed symbols must not appear in module-level
#            imports (SITE-A1, SITE-A5 import cleanup).
# ===========================================================================


class TestRemovedImportsAbsent:
    """
    SITE-A1: get_exit_authority and is_authoritative must no longer be imported
    from engine.exit_authority at module level in alpha_bot_execution.py.

    After removal the only reason to keep this import would be to service the
    port-level dispatch block (SITE-A2) or the per-symphony guard (SITE-A5),
    both of which are removed.  No other callers exist in the module.
    """

    def test_get_exit_authority_not_imported_at_module_level(self):
        tree = _get_abe_ast()
        imported = _ast_names_in_import(tree)
        assert "get_exit_authority" not in imported, (
            "SITE-A1: get_exit_authority must no longer be imported in "
            "alpha_bot_execution.py after port-dispatch removal; it has no "
            "callers once the port-level toggle read is removed."
        )

    def test_is_authoritative_not_imported_at_module_level(self):
        tree = _get_abe_ast()
        imported = _ast_names_in_import(tree)
        assert "is_authoritative" not in imported, (
            "SITE-A5: is_authoritative must no longer be imported in "
            "alpha_bot_execution.py after port-dispatch removal; the per-symphony "
            "execution-queue guard reduces to an unconditional 'if execution_queue:'."
        )

    def test_initialize_port_state_if_absent_not_imported_at_module_level(self):
        tree = _get_abe_ast()
        imported = _ast_names_in_import(tree)
        assert "initialize_port_state_if_absent" not in imported, (
            "SITE-A3: initialize_port_state_if_absent must no longer be imported "
            "in alpha_bot_execution.py; it was sourced from engine.dual_altitude "
            "solely to seed port_state rows for the port-level dispatch path."
        )

    def test_port_aggregator_not_imported(self):
        tree = _get_abe_ast()
        imported = _ast_names_in_import(tree)
        assert "aggregate_to_port" not in imported, (
            "SITE-A2: aggregate_to_port (port_aggregator) must no longer be "
            "imported; it was only used inside the port-level dispatch block."
        )
        assert "build_port_signal" not in imported, (
            "SITE-A2: build_port_signal (port_aggregator) must no longer be "
            "imported; it was only used inside the port-level dispatch block."
        )

    def test_port_selector_not_imported(self):
        tree = _get_abe_ast()
        imported = _ast_names_in_import(tree)
        assert "select_symphony_with_mc_gate" not in imported, (
            "SITE-A2: select_symphony_with_mc_gate (port_selector) must no longer "
            "be imported; it was only used inside the port-level dispatch block."
        )


# ===========================================================================
# Group 2 — Structural: removed call sites must not appear in source text.
# ===========================================================================


class TestRemovedCallSitesAbsent:
    """
    Source-text assertions that confirm the exact call patterns removed by
    SITE-A1 through SITE-A4 are gone.  These are coarser than AST checks but
    catch partial removals where an import stub remains.
    """

    def test_get_exit_authority_not_called_in_source(self):
        src = _get_abe_source()
        # The call site is `exit_authority = get_exit_authority()` at ~line 1000.
        assert "get_exit_authority()" not in src, (
            "SITE-A1: get_exit_authority() must not appear in source after removal; "
            "exit_authority variable is no longer needed once the port-level toggle "
            "fork is gone."
        )

    def test_initialize_port_state_if_absent_not_called_in_source(self):
        src = _get_abe_source()
        assert "initialize_port_state_if_absent" not in src, (
            "SITE-A3: initialize_port_state_if_absent must not appear in source after "
            "removal; it was called per-account per-cycle to seed port_state rows "
            "for the port-level dispatch path — which is now removed."
        )

    def test_port_symphony_snapshots_not_declared_in_source(self):
        src = _get_abe_source()
        assert "port_symphony_snapshots" not in src, (
            "SITE-A4: port_symphony_snapshots dict must not appear in source after "
            "removal; it was populated inside the per-symphony loop solely to feed "
            "the port-level dispatch block (SITE-A2)."
        )

    def test_aggregate_to_port_not_called_in_source(self):
        src = _get_abe_source()
        assert "aggregate_to_port(" not in src, (
            "SITE-A2: aggregate_to_port must not be called in alpha_bot_execution.py "
            "after port-dispatch removal."
        )

    def test_build_port_signal_not_called_in_source(self):
        src = _get_abe_source()
        assert "build_port_signal(" not in src, (
            "SITE-A2: build_port_signal must not be called in alpha_bot_execution.py "
            "after port-dispatch removal."
        )

    def test_select_symphony_with_mc_gate_not_called_in_source(self):
        src = _get_abe_source()
        assert "select_symphony_with_mc_gate(" not in src, (
            "SITE-A2: select_symphony_with_mc_gate must not be called in "
            "alpha_bot_execution.py after port-dispatch removal."
        )

    def test_is_authoritative_not_called_in_source(self):
        src = _get_abe_source()
        assert "is_authoritative(" not in src, (
            "SITE-A1/A5: is_authoritative must not appear in alpha_bot_execution.py "
            "source after removal; both call sites (port-level toggle read and "
            "per-symphony queue guard) are removed or replaced."
        )

    def test_execute_sell_to_cash_not_guarded_by_port_level_block(self):
        """
        SITE-A2 apex: execute_sell_to_cash must NOT appear inside a block
        gated on is_authoritative(altitude='port_level', ...).  The only
        remaining call to execute_sell_to_cash should be inside the
        per-symphony execution queue.
        """
        src = _get_abe_source()
        # The manifest cites this literal in SITE-A2 verbatim; if it still
        # appears the block was not removed.
        assert 'altitude="port_level"' not in src, (
            "SITE-A2: altitude='port_level' must not appear in source after "
            "port-dispatch removal; this is the apex guard of the removed block."
        )


# ===========================================================================
# Group 3 — Behavioural: per-symphony execution queue fires unconditionally
#            (SITE-A5 replacement: ``if execution_queue:`` not guarded by
#            is_authoritative).
# ===========================================================================


class TestPerSymphonyQueueFiresUnconditionally:
    """
    SITE-A5: After removal, the per-symphony execution queue must fire whenever
    execution_queue is non-empty — no EXIT_AUTHORITY env-var check blocks it.

    Pre-condition (old code): when EXIT_AUTHORITY=port_level, is_authoritative
    returned False for altitude='per_symphony', suppressing the queue entirely.
    Post-removal contract: the queue always fires, regardless of the env var.

    Scenario: one armed symphony, EXIT_AUTHORITY env var set to "port_level"
    (the old gate value that used to suppress per-symphony execution).
    Trailing-stop confirmation forced via math_engine patch.
    Expected: execute_sell_to_cash IS invoked (LIVE_EXECUTION=True path) OR
    the trigger freeze fires (LIVE_EXECUTION=False path, which we test below).
    """

    def test_execution_queue_fires_when_exit_authority_set_to_port_level(self, patched_environment):
        """
        SITE-A5: EXIT_AUTHORITY=port_level must no longer suppress the
        per-symphony execution queue.  If the guard ``is_authoritative(
        altitude='per_symphony', exit_authority=exit_authority)`` is still in
        place this test will fail because the trigger freeze (triggered=True in
        save_state) will not happen.
        """
        env = patched_environment

        fixture_pct_change = 0.12
        seed_hwm = 20.0

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(505.0)
        env["db"].load_state.return_value = _seed_state(armed=True, hwm=seed_hwm)

        # Set the env var that used to gate the per-symphony queue to False
        # (i.e., suppress it).  Post-removal it must have no effect.
        with (
            patch.dict("os.environ", {"EXIT_AUTHORITY": "port_level"}),
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),  # force trailing-stop-hit branch
            ),
        ):
            alpha_bot_execution.main()

        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        final_state = save_calls[-1].args[0]
        sym_state = final_state[_SYMPHONY_ID]

        # The trigger freeze MUST have fired; triggered=True is the proxy for
        # "execution queue ran".  If is_authoritative(altitude='per_symphony',
        # exit_authority='port_level') is still in place, it would return False
        # and the queue would be skipped, leaving triggered=False.
        assert sym_state["triggered"] is True, (
            "SITE-A5: EXIT_AUTHORITY=port_level must no longer suppress the "
            "per-symphony execution queue.  triggered must be True because the "
            "trailing-stop confirmation fired and the queue executed the freeze "
            "block — but if the old is_authoritative guard is still in place "
            "the queue is skipped and triggered stays False."
        )

    def test_execution_queue_fires_with_default_exit_authority_absent(self, patched_environment):
        """
        SITE-A5: Even with EXIT_AUTHORITY unset (defaulting to 'per_symphony'
        in the old code), the queue must fire after removal.  Baseline
        confirmation that removal does not break the default path.
        """
        env = patched_environment

        fixture_pct_change = 0.08
        seed_hwm = 15.0

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
        env["db"].load_state.return_value = _seed_state(armed=True, hwm=seed_hwm)

        # Remove EXIT_AUTHORITY entirely so the default path is exercised.
        patched_env = {k: v for k, v in __import__("os").environ.items() if k != "EXIT_AUTHORITY"}
        with (
            patch.dict("os.environ", patched_env, clear=True),
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),
            ),
        ):
            alpha_bot_execution.main()

        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        final_state = save_calls[-1].args[0]
        sym_state = final_state[_SYMPHONY_ID]

        assert sym_state["triggered"] is True, (
            "SITE-A5 default path: per-symphony queue must fire when "
            "EXIT_AUTHORITY is not set.  triggered must be True after trailing-stop "
            "confirmation."
        )


# ===========================================================================
# Group 4 — Behavioural: engine cycle completes normally when port_state has
#            no row (SITE-A3/A2 smoke test).
# ===========================================================================


class TestEngineCycleCompletesWithoutPortStateRow:
    """
    Smoke test: after SITE-A2 and SITE-A3 removal, a cycle with no port_state
    row in the DB must complete normally.  Pre-removal, the port-level dispatch
    block called read_port_state (returning None) and then continued/skipped.
    Post-removal: port_state is never read on the execution path so a missing
    row is entirely irrelevant to cycle completion.

    In both cases main() should complete (save_state called, lock released).
    This test specifically exercises the case where read_port_state returns None
    — the old code had a ``if _port_state is None: continue`` guard that would
    have skipped the account but not crashed.  Post-removal there must be no
    reference to read_port_state on the cycle path at all.
    """

    def test_cycle_completes_normally_when_port_state_row_absent(self, patched_environment):
        env = patched_environment

        # Standard non-triggering fixture so no execute_sell_to_cash call.
        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.01)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
        env["db"].load_state.return_value = _seed_state(armed=False, hwm=0.0)
        # read_port_state returning None used to trigger `continue` in the
        # port-level dispatch block; post-removal it should never be called
        # on the execution path.
        env["db"].read_port_state.return_value = None

        alpha_bot_execution.main()

        # Cycle completed: save_state was called, lock was released.
        assert env["db"].save_state.call_count >= 1, (
            "save_state must be called at least once — cycle did not complete."
        )
        env["db"].acquire_lock.assert_called_once()
        env["db"].release_lock.assert_called_once()

    def test_read_port_state_not_called_on_execution_path(self, patched_environment):
        """
        SITE-A2 residual check: read_port_state must NOT be invoked on the
        per-cycle execution path after port-dispatch removal.  The only
        remaining callers are in app.py (display routes — SITE-D1/D2), which
        are not exercised by alpha_bot_execution.main().
        """
        env = patched_environment

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.02)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
        env["db"].load_state.return_value = _seed_state(armed=False, hwm=0.0)
        env["db"].read_port_state.return_value = None

        alpha_bot_execution.main()

        (
            env["db"].read_port_state.assert_not_called(),
            (
                "SITE-A2: read_port_state must not be called on the execution path "
                "after port-dispatch removal.  Its only remaining callers are the "
                "dashboard display routes in app.py (SITE-D1/D2)."
            ),
        )

    def test_write_port_state_not_called_on_execution_path(self, patched_environment):
        """
        SITE-A2/A3 write-side: write_port_state must NOT be invoked on the
        per-cycle execution path after removal.  Pre-removal: it was written
        during the port-level dispatch block (composition hash update) and via
        initialize_port_state_if_absent.  Post-removal: port_state table becomes
        dead-write on this path.
        """
        env = patched_environment

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=0.03)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
        env["db"].load_state.return_value = _seed_state(armed=False, hwm=0.0)

        alpha_bot_execution.main()

        (
            env["db"].write_port_state.assert_not_called(),
            (
                "SITE-A2/A3: write_port_state must not be called on the execution "
                "path after port-dispatch removal.  Both write sites (port-level "
                "dispatch block and initialize_port_state_if_absent) are removed."
            ),
        )


# ===========================================================================
# Group 5 — Behavioural: symphony-level decision math fires unchanged.
# ===========================================================================


class TestSymphonyLevelMathFiringUnchanged:
    """
    The removal of SITE-A1 through A5 must NOT suppress or alter the
    symphony-level math layer.  _haircut_select, compute_crra_eu_tstat, and the
    6-layer exit decision (resolve_trigger_priority) are exercised implicitly by
    the per-symphony loop.  We assert the observable proxy — compute_exit_confirmation
    (part of the exit-confirmation layer called on every armed symphony) is still
    reached and its return value drives the state freeze — rather than directly
    patching or inspecting every math function.
    """

    def test_compute_exit_confirmation_still_called_per_armed_symphony(self, patched_environment):
        """
        Confirm that compute_exit_confirmation (the exit-decision layer reached
        after all upstream math) is still called on the per-symphony loop for
        an armed symphony.  If the loop were accidentally gated by the removed
        is_authoritative guard it would not be reached.
        """
        env = patched_environment

        fixture_pct_change = 0.05
        seed_hwm = 8.0

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
        env["db"].load_state.return_value = _seed_state(armed=True, hwm=seed_hwm)

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(0, False),  # no trigger — we just care it was called
            wraps=None,
        ) as mock_confirm:
            alpha_bot_execution.main()

        assert mock_confirm.call_count >= 1, (
            "compute_exit_confirmation must be called at least once for an armed "
            "symphony after port-dispatch removal — the per-symphony math layer "
            "must still be reachable.  If the old is_authoritative guard is still "
            "in place on the queue path the loop body may be suppressed."
        )

    def test_trigger_fired_via_symphony_math_not_port_path(self, patched_environment):
        """
        End-to-end symphony math path: trigger fires, freeze runs, Discord alert
        sent — all via the per-symphony loop with no port-level dispatch.

        This is the canonical regression pin: the Trailing Stop path must work
        exactly as it did before, just without the port-level toggle branching.
        """
        env = patched_environment

        fixture_pct_change = 0.12
        seed_hwm = 20.0
        live_price = 502.50

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(live_price)
        env["db"].load_state.return_value = _seed_state(armed=True, hwm=seed_hwm)

        with patch.object(
            alpha_bot_execution.math_engine,
            "compute_exit_confirmation",
            return_value=(3, True),
        ):
            alpha_bot_execution.main()

        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called — pipeline aborted early"
        final_state = save_calls[-1].args[0]
        sym_state = final_state[_SYMPHONY_ID]

        # Trigger freeze invariants.
        assert sym_state["triggered"] is True, (
            "Trailing Stop trigger must fire via the per-symphony loop after port-dispatch removal."
        )
        assert sym_state["armed"] is False
        assert sym_state["triggered_reason"] == "Trailing Stop"

        # Fixture-derived: current_return == last_percent_change * 100.
        expected_return = fixture_pct_change * 100.0
        assert sym_state["triggered_at_return"] == pytest.approx(
            expected_return,
            rel=1e-9,  # exact arithmetic — engine multiplies fixture value by 100
        ), "triggered_at_return must echo fixture-derived current_return"

        # Discord exit alert sent exactly once.
        env["reporting"].send_discord_alert.assert_called_once()
        alert_kwargs = env["reporting"].send_discord_alert.call_args.kwargs
        assert alert_kwargs.get("exit_reason") == "Trailing Stop"


# ===========================================================================
# Group 6 — Behavioural: LIVE_EXECUTION safety — no order path opens.
# ===========================================================================


class TestLiveExecutionDefaultFalsePreserved:
    """
    The LIVE_EXECUTION safety contract must survive the removal.

    Post-removal, when LIVE_EXECUTION=False, execute_sell_to_cash must NOT be
    called even when a trigger fires.  This is the same contract as before;
    we pin it here so a naive refactor that accidentally inverts the guard
    would be caught.
    """

    def test_execute_sell_to_cash_not_called_when_live_execution_false(self, patched_environment):
        env = patched_environment

        fixture_pct_change = 0.15
        seed_hwm = 25.0

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(500.0)
        env["db"].load_state.return_value = _seed_state(armed=True, hwm=seed_hwm)

        with (
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", False),
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),
            ),
            patch.object(alpha_bot_execution, "execute_sell_to_cash") as mock_execute,
        ):
            alpha_bot_execution.main()

        (
            mock_execute.assert_not_called(),
            (
                "LIVE_EXECUTION=False must prevent execute_sell_to_cash from being "
                "called even when a trigger fires.  The port-dispatch removal must "
                "not alter the dry-run safety gate."
            ),
        )

    def test_trigger_freeze_still_runs_in_dry_run_mode(self, patched_environment):
        """
        Dry-run mode (LIVE_EXECUTION=False) must still produce the full trigger
        freeze: triggered=True, triggered_reason set, trigger_prices populated.
        The audit trail is mandatory regardless of execution mode.
        """
        env = patched_environment

        fixture_pct_change = 0.10
        seed_hwm = 18.0
        live_price = 498.75

        env["fetch_symphony_stats"].return_value = [
            _make_symphony_payload(last_percent_change=fixture_pct_change)
        ]
        env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(live_price)
        env["db"].load_state.return_value = _seed_state(armed=True, hwm=seed_hwm)

        with (
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", False),
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),
            ),
            patch.object(alpha_bot_execution, "execute_sell_to_cash"),
        ):
            alpha_bot_execution.main()

        save_calls = env["db"].save_state.call_args_list
        assert save_calls, "save_state was never called"
        sym_state = save_calls[-1].args[0][_SYMPHONY_ID]

        assert sym_state["triggered"] is True
        assert sym_state.get("triggered_reason"), "triggered_reason must be set in dry-run mode"
        assert _TICKER in sym_state.get("trigger_prices", {}), (
            "trigger_prices must be populated from live_vwaps in dry-run mode"
        )
        assert sym_state["trigger_prices"][_TICKER] == pytest.approx(
            live_price,
            rel=1e-9,  # exact arithmetic — engine writes the vwap value verbatim
        )


# ===========================================================================
# Group 7 — Existing portmode tests slated for deletion: tombstone markers.
# ===========================================================================


class TestPortmodeTestFilesToBeDeleted:
    """
    Permanent deletion assertions for portmode symbols removed in Sprint 3.
    Each test asserts the deleted symbol is no longer importable.
    """

    def test_port_aggregator_import_fails_after_removal(self):
        with pytest.raises((ImportError, ModuleNotFoundError)):
            from port_aggregator import aggregate_to_port  # noqa: F401

    def test_port_selector_import_fails_after_removal(self):
        with pytest.raises((ImportError, ModuleNotFoundError)):
            from port_selector import select_symphony_with_mc_gate  # noqa: F401

    def test_dual_altitude_initialize_import_fails_after_removal(self):
        with pytest.raises((ImportError, ModuleNotFoundError)):
            from engine.dual_altitude import initialize_port_state_if_absent  # noqa: F401

    def test_exit_authority_get_exit_authority_import_fails_after_removal(self):
        # engine.exit_authority module STILL EXISTS (display helpers preserved per AX-2).
        # Only the get_exit_authority symbol was removed. The import itself raises ImportError.
        with pytest.raises((ImportError, ModuleNotFoundError, AttributeError)):
            from engine.exit_authority import get_exit_authority  # noqa: F401
