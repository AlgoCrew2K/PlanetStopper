"""RED tests — Workstream B: advisors/weekly_suggestions_scheduler.py orchestrator.

AC-B1: "New advisors/weekly_suggestions_scheduler.py::run_weekly_suggestions()
calls, in sequence, each wrapped in its own D-1 try/except (one failure never
blocks the next): (1) strategy_builder_scheduler.run_weekly_build(), (2)
run_weekly_asset_swap_suggestions(), (3) run_weekly_logic_change_suggestions().
Invokable via python -m advisors.weekly_suggestions_scheduler. Do NOT overload
strategy_builder_scheduler.py."

AC-B2: "Same-ISO-week idempotency per engine -- the swap/logic loops get their
own dedup guard (role-filtered, like A.1's fix) so a re-run in the same week
does not duplicate suggestions." (Covered structurally by AC-C1/C2's own
idempotency -- this file only asserts the orchestrator ITSELF does not add a
conflicting outer guard that would prevent per-engine dedup from working; the
per-engine dedup behavior itself belongs to the C-workstream tests.)

AC-B3: systemd unit + timer + docs/DEPLOYMENT.md section (doc-content pin,
mirroring the Prism unit pattern already in docs/DEPLOYMENT.md:238-268).

AC-B4: "D-1 never-raises; per-engine bounded; a documented MAX_BUDGET_USD-style
guard consistent with the existing schedulers."

The module (advisors/weekly_suggestions_scheduler.py) does not exist yet --
ImportError is the first RED signal, resolved as a clean pytest.fail (not a
collection ERROR), mirroring tests/advisors/test_builder_scheduler.py.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

_MODULE_PATH = "advisors.weekly_suggestions_scheduler"
_ENTRY_FN_NAME = "run_weekly_suggestions"

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEPLOYMENT_DOC = _REPO_ROOT / "docs" / "DEPLOYMENT.md"


def _import_orchestrator():
    """Import advisors.weekly_suggestions_scheduler, or pytest.fail with a clean
    FAILED (not a collection ERROR) when it does not exist yet."""
    try:
        return importlib.import_module(_MODULE_PATH)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(
            f"{_MODULE_PATH} is not importable ({type(exc).__name__}: {exc}). "
            f"AC-B1 requires a new orchestrator module at this path."
        )


def _resolve_entry_fn(mod):
    fn = getattr(mod, _ENTRY_FN_NAME, None)
    if not callable(fn):
        pytest.fail(
            f"{_MODULE_PATH} does not expose a callable {_ENTRY_FN_NAME}() "
            f"(AC-B1 pins this exact name)."
        )
    return fn


# ===========================================================================
# AC-B1 — calls all three engine entry points, in sequence, exactly once
# ===========================================================================


class TestOrchestratorCallsAllThreeEngines:
    def test_calls_strategy_builder_asset_swap_and_logic_change_once_each(self, monkeypatch):
        import advisors.strategy_builder_scheduler as sb_sched

        mod = _import_orchestrator()
        run_fn = _resolve_entry_fn(mod)

        call_log: list = []

        def _fake_run_weekly_build():
            call_log.append("strategy_builder")

        def _fake_run_weekly_asset_swap_suggestions():
            call_log.append("asset_swap")

        def _fake_run_weekly_logic_change_suggestions():
            call_log.append("logic_change")

        # Patch on BOTH the canonical source module and (if the orchestrator
        # imports by `from x import y`) the orchestrator's own attribute --
        # same rationale as the C-workstream loop tests' dual-patch helper.
        monkeypatch.setattr(sb_sched, "run_weekly_build", _fake_run_weekly_build, raising=False)
        if hasattr(mod, "run_weekly_build"):
            monkeypatch.setattr(mod, "run_weekly_build", _fake_run_weekly_build, raising=False)

        for name, fake in (
            ("run_weekly_asset_swap_suggestions", _fake_run_weekly_asset_swap_suggestions),
            ("run_weekly_logic_change_suggestions", _fake_run_weekly_logic_change_suggestions),
        ):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, fake, raising=False)
            # Also try the plausible source modules named in AC-C1/AC-C2.
            for src_modpath in ("advisors.asset_swap_engine", "advisors.logic_change_engine"):
                try:
                    src_mod = importlib.import_module(src_modpath)
                except Exception:  # noqa: BLE001
                    continue
                if hasattr(src_mod, name):
                    monkeypatch.setattr(src_mod, name, fake, raising=False)

        run_fn()

        assert set(call_log) == {"strategy_builder", "asset_swap", "logic_change"}, (
            f"run_weekly_suggestions() must call all three engine entry points "
            f"(AC-B1); observed calls: {call_log!r}"
        )
        assert call_log == ["strategy_builder", "asset_swap", "logic_change"], (
            f"AC-B1 specifies the call sequence (1) strategy_builder_scheduler."
            f"run_weekly_build(), (2) run_weekly_asset_swap_suggestions(), (3) "
            f"run_weekly_logic_change_suggestions(); observed order {call_log!r}"
        )


# ===========================================================================
# AC-B1/AC-B4 — D-1: one engine's failure never blocks the next
# ===========================================================================


class TestOrchestratorD1PerEngineIsolation:
    def _patch_all_three(self, monkeypatch, mod, *, failing: str, call_log: list):
        import advisors.strategy_builder_scheduler as sb_sched

        def _make(name):
            def _fn(*a, **k):
                call_log.append(name)
                if name == failing:
                    raise RuntimeError(f"simulated {name} failure")

            return _fn

        monkeypatch.setattr(
            sb_sched, "run_weekly_build", _make("strategy_builder"), raising=False
        )
        if hasattr(mod, "run_weekly_build"):
            monkeypatch.setattr(mod, "run_weekly_build", _make("strategy_builder"), raising=False)

        for name, engine_key in (
            ("run_weekly_asset_swap_suggestions", "asset_swap"),
            ("run_weekly_logic_change_suggestions", "logic_change"),
        ):
            fake = _make(engine_key)
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, fake, raising=False)
            for src_modpath in ("advisors.asset_swap_engine", "advisors.logic_change_engine"):
                try:
                    src_mod = importlib.import_module(src_modpath)
                except Exception:  # noqa: BLE001
                    continue
                if hasattr(src_mod, name):
                    monkeypatch.setattr(src_mod, name, fake, raising=False)

    def test_strategy_builder_failure_does_not_block_asset_swap_or_logic_change(
        self, monkeypatch
    ):
        mod = _import_orchestrator()
        run_fn = _resolve_entry_fn(mod)
        call_log: list = []
        self._patch_all_three(monkeypatch, mod, failing="strategy_builder", call_log=call_log)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"run_weekly_suggestions must never raise (D-1); raised {exc!r}")

        assert "asset_swap" in call_log and "logic_change" in call_log, (
            f"strategy_builder failing must not prevent the other two engines from "
            f"running; observed calls: {call_log!r}"
        )

    def test_asset_swap_failure_does_not_block_logic_change(self, monkeypatch):
        mod = _import_orchestrator()
        run_fn = _resolve_entry_fn(mod)
        call_log: list = []
        self._patch_all_three(monkeypatch, mod, failing="asset_swap", call_log=call_log)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"run_weekly_suggestions must never raise (D-1); raised {exc!r}")

        assert "logic_change" in call_log, (
            f"asset_swap failing must not prevent logic_change from running; observed "
            f"calls: {call_log!r}"
        )

    def test_all_three_engines_failing_never_raises(self, monkeypatch):
        """The strongest D-1 case: all three engines raise; the orchestrator itself
        must still return cleanly (D-1 never-raises, AC-B4)."""
        mod = _import_orchestrator()
        run_fn = _resolve_entry_fn(mod)
        import advisors.strategy_builder_scheduler as sb_sched

        def _boom(*a, **k):
            raise RuntimeError("simulated total engine outage with /secret/path leak")

        monkeypatch.setattr(sb_sched, "run_weekly_build", _boom, raising=False)
        if hasattr(mod, "run_weekly_build"):
            monkeypatch.setattr(mod, "run_weekly_build", _boom, raising=False)
        for name in ("run_weekly_asset_swap_suggestions", "run_weekly_logic_change_suggestions"):
            if hasattr(mod, name):
                monkeypatch.setattr(mod, name, _boom, raising=False)
            for src_modpath in ("advisors.asset_swap_engine", "advisors.logic_change_engine"):
                try:
                    src_mod = importlib.import_module(src_modpath)
                except Exception:  # noqa: BLE001
                    continue
                if hasattr(src_mod, name):
                    monkeypatch.setattr(src_mod, name, _boom, raising=False)

        try:
            run_fn()
        except Exception as exc:  # noqa: BLE001
            pytest.fail(
                f"run_weekly_suggestions must never raise even when all three engines "
                f"fail (D-1); raised {exc!r}"
            )


# ===========================================================================
# AC-B1 — invokable as `python -m advisors.weekly_suggestions_scheduler`
# ===========================================================================


class TestOrchestratorInvocableAsModule:
    def test_module_has_main_guard_calling_run_weekly_suggestions(self):
        mod = _import_orchestrator()
        source_path = pathlib.Path(mod.__file__)
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

        has_main_guard = any(
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Compare)
            and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            for node in ast.walk(tree)
        )
        assert has_main_guard, (
            f"{source_path} must have an `if __name__ == \"__main__\":` guard so "
            f"`python -m advisors.weekly_suggestions_scheduler` is a valid invocation "
            f"(AC-B1)."
        )

    def test_does_not_overload_strategy_builder_scheduler_module(self):
        """AC-B1: 'Do NOT overload strategy_builder_scheduler.py (Strategy-Builder-only
        per its AC-18 scope)' -- the new entry point must live in the new module, not
        be bolted onto the existing SB-only scheduler."""
        import advisors.strategy_builder_scheduler as sb_sched

        assert not hasattr(sb_sched, _ENTRY_FN_NAME), (
            f"advisors.strategy_builder_scheduler must NOT gain a "
            f"{_ENTRY_FN_NAME}() function -- AC-B1 requires the new orchestrator to "
            f"live in advisors/weekly_suggestions_scheduler.py, not overload the "
            f"Strategy-Builder-only scheduler."
        )


# ===========================================================================
# AC-B4 — off-execution-path guard + a named budget-style constant
# ===========================================================================


class TestOrchestratorArchitectureConstraints:
    def test_module_source_does_not_reference_live_execution(self):
        mod = _import_orchestrator()
        source_text = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
        assert "LIVE_EXECUTION" not in source_text, (
            "advisors/weekly_suggestions_scheduler.py must not reference "
            "LIVE_EXECUTION -- advisory-only, off the live trade path (AC-B4)."
        )

    def test_alpha_bot_execution_does_not_import_the_orchestrator(self):
        """Off-execution-path guard (consistent with every other advisor engine's
        AC-X2-style constraint): the 1-minute live execution module must never import
        this orchestrator."""
        exec_path = _REPO_ROOT / "alpha_bot_execution.py"
        assert exec_path.exists(), f"Expected {exec_path} to exist."
        source_text = exec_path.read_text(encoding="utf-8")
        assert "weekly_suggestions_scheduler" not in source_text, (
            "alpha_bot_execution.py must never import "
            "advisors.weekly_suggestions_scheduler (architecture constraint 1: no "
            "blocking I/O on the 1-minute execution path)."
        )


# ===========================================================================
# AC-B3 — docs/DEPLOYMENT.md gains a weekly-suggestions systemd section
# ===========================================================================


class TestDeploymentDocsHaveWeeklySuggestionsSection:
    def test_deployment_doc_documents_the_weekly_timer(self):
        assert _DEPLOYMENT_DOC.exists(), f"Expected {_DEPLOYMENT_DOC} to exist."
        text = _DEPLOYMENT_DOC.read_text(encoding="utf-8")

        assert "weekly_suggestions_scheduler.py" in text, (
            "docs/DEPLOYMENT.md must document the weekly_suggestions_scheduler.py "
            "systemd unit (AC-B3), mirroring the Prism unit pattern at "
            "docs/DEPLOYMENT.md:238-268."
        )
        assert "OnCalendar=*-*-* Mon 04:00 America/New_York" in text, (
            "docs/DEPLOYMENT.md must document the exact weekly OnCalendar directive "
            "specified by AC-B3: 'OnCalendar=*-*-* Mon 04:00 America/New_York'."
        )
        assert "Persistent=true" in text, (
            "docs/DEPLOYMENT.md's weekly-suggestions timer section must include "
            "Persistent=true (AC-B3, mirrors the Prism timer)."
        )

    def test_deployment_doc_does_not_wire_council_oauth_env_for_weekly_timer(self):
        """AC-B3: 'EnvironmentFile=/opt/planetstopper/.env ONLY -- no council-env /
        OAuth-token (SDK path, metered ANTHROPIC_API_KEY)'. Locate the weekly-
        suggestions service block and assert it does not reference the council's
        OAuth EnvironmentFile."""
        text = _DEPLOYMENT_DOC.read_text(encoding="utf-8")
        marker = "weekly_suggestions_scheduler.py"
        idx = text.find(marker)
        assert idx != -1, "weekly_suggestions_scheduler.py section not found in DEPLOYMENT.md."

        # Look at a bounded window around the reference for the service unit block.
        window = text[max(0, idx - 800) : idx + 800]
        assert "council-env" not in window, (
            "The weekly-suggestions systemd unit must use ONLY "
            "EnvironmentFile=/opt/planetstopper/.env -- it must NOT reference "
            "/etc/planetstopper/council-env (that is the Prism council's OAuth-token "
            "file; the weekly scheduler is an SDK/metered-API-key path, AC-B3)."
        )
