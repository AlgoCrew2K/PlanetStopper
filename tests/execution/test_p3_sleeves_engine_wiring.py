"""
RED tests — Managed Sleeves P3: engine wiring into alpha_bot_execution.main().

CONTRACT this file specifies for the GREEN implementer (s3-engine):

  * ``alpha_bot_execution.py`` gains a lazy import inside ``main()`` — never at
    module level (CC-2 convention, matching the ai_advisor/lens_pipeline
    precedent elsewhere in this file's imports):

        from sleeves import tick_orchestrator
        tick_orchestrator.run_sleeve_tick_for_all_sleeves(
            now_utc=<the tick's current_et>,
            discord_webhook_url=DISCORD_WEBHOOK_URL,
        )

  * The call happens strictly AFTER the existing per-symphony execution queue
    (the "exit machine") has finished processing for this tick — absolute
    precedence per DE-SLEEVES-P1-001 / feature-plans/managed-sleeves.md
    Architecture: "a sleeve can never delay/preempt symphony protection."

  * The whole sleeves call is wrapped so ANY exception raised inside sleeve
    processing is caught and logged, and the tick continues to completion
    (the exit machine's own ``database.save_state``/``save_chart_history``
    and the ``finally: database.release_lock()`` must still run). Sleeve code
    must never be able to break symphony trading.

This file requires NO ``sleeves`` package to exist to run its structural
checks (a repo with no sleeves/tick_orchestrator.py trivially fails the
positive-existence check, proving genuine RED at authoring time) — the
runtime behavioral tests inject a fake ``sleeves.tick_orchestrator`` module
via ``sys.modules`` so they exercise the *wiring contract* without depending
on P3's own module being implemented yet.
"""

from __future__ import annotations

import ast
import pathlib
import sys
import types
from unittest.mock import patch

import pytest

import alpha_bot_execution

# Reuse the proven end-to-end harness from the existing B1 pipeline tests —
# duplicating fixture/state-seeding logic here would let the two modules
# drift apart over time (same rationale as test_main_live_execution.py).
from tests.execution.test_main_pipeline import (
    _ACCOUNT_ID,  # noqa: F401  (re-exported for readability of call-arg pins)
    _ACTUAL_SYMPHONY_ID,  # noqa: F401
    _SYMPHONY_ID,
    _make_symphony_payload,
    _make_vwap_payload,
    _seed_state,
    patched_environment,  # noqa: F401  (re-exported pytest fixture)
)

_SOURCE_PATH = pathlib.Path(alpha_bot_execution.__file__)

# Scenario constants mirroring B1 scenario 1 (trigger-fires) so the exit
# machine's execute_sell_to_cash call site is genuinely reached.
_FIXTURE_PCT_CHANGE = 0.12
_SEED_HWM = 20.0
_LIVE_PRICE = 505.25


def _configure_trigger_scenario(env):
    env["fetch_symphony_stats"].return_value = [
        _make_symphony_payload(last_percent_change=_FIXTURE_PCT_CHANGE)
    ]
    env["fetch_intraday_vwaps"].return_value = _make_vwap_payload(_LIVE_PRICE)
    env["db"].load_state.return_value = _seed_state(armed=True, hwm=_SEED_HWM)


def _inject_fake_tick_orchestrator(monkeypatch, run_fn):
    """Install a fake sleeves.tick_orchestrator module via sys.modules.

    Lets the wiring contract (``from sleeves import tick_orchestrator``) be
    exercised without depending on the real sleeves/tick_orchestrator.py
    module existing yet — the lazy import resolves to this double instead.
    """
    fake_module = types.ModuleType("sleeves.tick_orchestrator")
    fake_module.run_sleeve_tick_for_all_sleeves = run_fn
    fake_package = types.ModuleType("sleeves")
    fake_package.tick_orchestrator = fake_module
    monkeypatch.setitem(sys.modules, "sleeves", fake_package)
    monkeypatch.setitem(sys.modules, "sleeves.tick_orchestrator", fake_module)


# ---------------------------------------------------------------------------
# CC-2 lazy-import boundary (never-regress guard + positive-existence RED)
# ---------------------------------------------------------------------------


class TestCC2LazyImportBoundary:
    """alpha_bot_execution.py must never import sleeves at module level."""

    def test_no_module_level_sleeves_import(self):
        """Never-regress guard: no top-level (module-scope) statement may
        import the sleeves package — it must only ever be imported lazily
        inside a function body (CC-2 convention)."""
        tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
        offenders = []
        for node in tree.body:  # module-level statements ONLY
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "sleeves" or alias.name.startswith("sleeves."):
                        offenders.append(node.lineno)
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "sleeves" or mod.startswith("sleeves."):
                    offenders.append(node.lineno)
        assert not offenders, (
            f"CC-2 violation: alpha_bot_execution.py imports sleeves at module "
            f"level (line(s) {offenders}). The sleeves runner must be imported "
            f"lazily inside main(), never at module scope."
        )

    def test_sleeves_wiring_exists_inside_main_function_body(self):
        """Positive existence check — proves the negative check above isn't
        vacuously true. main() itself must reference the sleeves package
        somewhere in its body (the lazy import this cycle adds).

        Currently FAILS: no P3 wiring exists yet in main().
        """
        tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
        main_fn = next(
            (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "main"),
            None,
        )
        assert main_fn is not None, "alpha_bot_execution.py must define main()"

        found_lazy_import = False
        for node in ast.walk(main_fn):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("sleeves"):
                found_lazy_import = True
            if isinstance(node, ast.Import) and any(
                a.name.startswith("sleeves") for a in node.names
            ):
                found_lazy_import = True

        assert found_lazy_import, (
            "main() must lazily import the sleeves package/module somewhere in "
            "its own body — no sleeves reference found. This is the P3 engine "
            "wiring that hooks the rule engine into the live tick."
        )


# ---------------------------------------------------------------------------
# Runtime precedence: exit machine strictly before the sleeves tick
# ---------------------------------------------------------------------------


class TestPrecedenceExitMachineBeforeSleeves:
    def test_sleeves_tick_invoked_after_execution_queue_processing(
        self, patched_environment, monkeypatch
    ):
        env = patched_environment
        _configure_trigger_scenario(env)

        call_order: list[str] = []

        def _tracking_execute(*_a, **_k):
            call_order.append("exit_machine")
            return True

        def _fake_run(*, now_utc, discord_webhook_url=None):
            call_order.append("sleeves_tick")
            return []

        _inject_fake_tick_orchestrator(monkeypatch, _fake_run)

        with (
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),  # force the trailing-stop-hit branch
            ),
            patch.object(
                alpha_bot_execution, "execute_sell_to_cash", side_effect=_tracking_execute
            ),
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
        ):
            alpha_bot_execution.main()

        assert "sleeves_tick" in call_order, (
            "sleeves.tick_orchestrator.run_sleeve_tick_for_all_sleeves was never "
            "invoked by main() — the P3 engine wiring is missing."
        )
        assert "exit_machine" in call_order, (
            "fixture wiring bug: the exit machine's execute_sell_to_cash was "
            "never reached — cannot prove precedence without it firing."
        )
        assert call_order.index("exit_machine") < call_order.index("sleeves_tick"), (
            f"the sleeves tick must run strictly AFTER the exit machine's "
            f"per-symphony execution queue processing (absolute precedence, "
            f"DE-SLEEVES-P1-001) — a sleeve can never delay/preempt symphony "
            f"protection; got call order {call_order}"
        )

    def test_sleeves_tick_receives_the_ticks_own_current_et_as_now_utc(
        self, patched_environment, monkeypatch
    ):
        """The sleeves tick must be handed the SAME wall-clock moment the rest
        of the tick used (get_current_et()'s patched fixed clock) — not an
        independently-computed timestamp that could drift from the exit
        machine's own notion of "now"."""
        env = patched_environment
        _configure_trigger_scenario(env)

        captured: dict = {}

        def _fake_run(*, now_utc, discord_webhook_url=None):
            captured["now_utc"] = now_utc
            return []

        _inject_fake_tick_orchestrator(monkeypatch, _fake_run)

        with (
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),
            ),
            patch.object(alpha_bot_execution, "execute_sell_to_cash", return_value=True),
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
        ):
            alpha_bot_execution.main()

        assert "now_utc" in captured, (
            "run_sleeve_tick_for_all_sleeves was never called with a now_utc "
            "keyword argument — P3 engine wiring is missing or uses the wrong "
            "call signature."
        )
        # get_current_et is patched to return the fixture's fixed clock — the
        # sleeves tick must receive that exact same moment, not re-derive its
        # own via datetime.now().
        expected_now = alpha_bot_execution.get_current_et()
        assert captured["now_utc"] == expected_now, (
            f"sleeves tick now_utc ({captured['now_utc']!r}) must equal the "
            f"tick's own get_current_et() ({expected_now!r}) — the exit "
            f"machine and the sleeves tick must share one wall-clock moment."
        )


# ---------------------------------------------------------------------------
# Fail-safe containment: a sleeves exception must never break the tick
# ---------------------------------------------------------------------------


class TestSleevesExceptionDoesNotBreakTheTick:
    def test_sleeves_tick_exception_is_caught_and_exit_machine_state_survives(
        self, patched_environment, monkeypatch
    ):
        env = patched_environment
        _configure_trigger_scenario(env)

        invocation_count = {"n": 0}

        def _raising_run(*, now_utc, discord_webhook_url=None):
            invocation_count["n"] += 1
            raise RuntimeError("simulated sleeves processing failure")

        _inject_fake_tick_orchestrator(monkeypatch, _raising_run)

        with (
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),
            ),
            patch.object(alpha_bot_execution, "execute_sell_to_cash", return_value=True),
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
        ):
            try:
                alpha_bot_execution.main()
            except BaseException as exc:  # pragma: no cover — only fires on regression
                pytest.fail(
                    f"main() must never propagate an exception raised during "
                    f"sleeves processing — sleeve code must never be able to "
                    f"break symphony trading; got {type(exc).__name__}: {exc}"
                )

        assert invocation_count["n"] == 1, (
            "sleeves.tick_orchestrator.run_sleeve_tick_for_all_sleeves was "
            "never invoked — cannot verify exception containment because the "
            "P3 wiring itself is missing."
        )

        # The exit machine's own commit must have survived the sleeves failure.
        save_calls = env["db"].save_state.call_args_list
        assert save_calls, (
            "database.save_state was never called — a sleeves-tick exception "
            "must not abort the rest of main()'s tick."
        )
        final_state = save_calls[-1].args[0]
        assert final_state.get(_SYMPHONY_ID, {}).get("triggered") is True, (
            "the exit machine's own triggered=True commit must survive a "
            "sleeves-tick exception — this is the fail-safe containment "
            "invariant (a sleeve bug must never cost a symphony its exit)."
        )

        # The lock must still be released cleanly.
        env["db"].release_lock.assert_called_once()

    def test_sleeves_tick_invoked_exactly_once_per_main_invocation(
        self, patched_environment, monkeypatch
    ):
        env = patched_environment
        _configure_trigger_scenario(env)

        call_count = {"n": 0}

        def _fake_run(*, now_utc, discord_webhook_url=None):
            call_count["n"] += 1
            return []

        _inject_fake_tick_orchestrator(monkeypatch, _fake_run)

        with (
            patch.object(
                alpha_bot_execution.math_engine,
                "compute_exit_confirmation",
                return_value=(3, True),
            ),
            patch.object(alpha_bot_execution, "execute_sell_to_cash", return_value=True),
            patch.object(alpha_bot_execution, "LIVE_EXECUTION", True),
        ):
            alpha_bot_execution.main()

        assert call_count["n"] == 1, (
            f"run_sleeve_tick_for_all_sleeves must be invoked exactly once per "
            f"main() tick; got {call_count['n']} invocations."
        )
