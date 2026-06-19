"""
RED tests for AC-3 (LOAD-BEARING): advisory-only invariant.

The calibration sweep and report MUST NOT write to bot_state, MUST NOT import
the execution path, MUST NOT mutate live constants. These tests make it
impossible to silently wire the sweep into live trading without failing here.

Two-layer guard (primary guards listed first):
  1. Monkeypatch database to a sentinel that raises on any mutating call —
     call the report generator and assert it does NOT raise. This is the
     primary load-bearing assertion.
  2. Import-walk: assert the report script does not import database write-path
     symbols at module level.
  3. (Secondary) alpha_bot_execution not imported at module level by the script.

No live API calls. No hardcoded producer values.
"""

import importlib
import importlib.util
import inspect
import pathlib
import sys
import types

import pytest

_WORKTREE = pathlib.Path(__file__).parent.parent
_REPORT_SCRIPT = _WORKTREE / "scripts" / "vwap-calibration-report.py"

# ---------------------------------------------------------------------------
# Helper: load the report script as a module without executing __main__
# ---------------------------------------------------------------------------


def _load_report_module():
    """Import scripts/vwap-calibration-report.py as a module.

    Uses importlib.util.spec_from_file_location so the file's name (with a
    hyphen) does not have to be a valid Python identifier. If the file does
    not exist the test will fail with a clear ImportError.
    """
    if not _REPORT_SCRIPT.exists():
        pytest.fail(
            f"scripts/vwap-calibration-report.py does not exist at "
            f"{_REPORT_SCRIPT}. Implementer must create it. (AC-3 / AC-2)"
        )
    spec = importlib.util.spec_from_file_location("vwap_calibration_report", _REPORT_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# AC-3.1 — PRIMARY: database sentinel proves zero DB writes
# ---------------------------------------------------------------------------

# Symbols in database that constitute a write path.  This list is intentionally
# conservative: any symbol with a mutating verb is forbidden from being called
# by the report script.
_DB_WRITE_SYMBOLS = {
    "insert_advisor_observation",
    "set_symphony_live_mode",
    "save_regime_label",
    "save_autotune_run",
    "record_cvar_diagnostic",
    "insert_dof_ledger_row",
    "insert_prism_audit_entry",
    "init_db",
    "get_connection",  # any connection is also suspect in an advisory-only script
}


class _DatabaseWriteSentinel(types.ModuleType):
    """Replacement for the database module that raises on any mutating call.

    If the report script attempts to call any write-path symbol, the test
    fails immediately with a clear message naming the violated symbol.
    """

    def __getattr__(self, name):
        if name in _DB_WRITE_SYMBOLS:

            def _raiser(*args, **kwargs):
                raise AssertionError(
                    f"AC-3 VIOLATION: vwap-calibration-report.py called "
                    f"database.{name}() — the report is advisory-only and "
                    f"must NEVER write to the state DB."
                )

            return _raiser
        # Pass through read-only / non-mutating symbols without raising
        return super().__getattribute__(name)


def test_report_generator_does_not_write_to_database(monkeypatch):
    """Primary AC-3 guard: report generator must not call any DB write symbol.

    Monkeypatches the database module with a sentinel that raises on mutating
    calls, then invokes the report's entry point with fixture-like input.
    If the report touches any write path, AssertionError is raised inside the
    call and propagates here as a test failure.

    Fails RED until scripts/vwap-calibration-report.py exists AND avoids DB
    writes.
    """
    # Replace database in sys.modules with our sentinel BEFORE loading the
    # report module so any top-level import of database binds to the sentinel.
    sentinel = _DatabaseWriteSentinel("database")
    monkeypatch.setitem(sys.modules, "database", sentinel)

    report_mod = _load_report_module()

    # Locate the report's entry point.  We expect a callable named
    # generate_report, render_report, or main that accepts study rows.
    entry_point = None
    for candidate in ("generate_report", "render_report", "build_report"):
        entry_point = getattr(report_mod, candidate, None)
        if entry_point is not None:
            break

    assert entry_point is not None, (
        "scripts/vwap-calibration-report.py must expose a callable named "
        "generate_report, render_report, or build_report. None found."
    )

    # Minimal fixture-derived study rows — shape only, no hardcoded values
    minimal_rows = [
        {
            "symphony_id": "SYM-ALPHA",
            "param_name": "PARABOLIC_VELOCITY_THRESHOLD",
            "current_value": 2.0,
            "proposed_value": 1.5,
            "expected_trigger_freq_change": 1.0,
            "pbo_veto_status": False,
            "haircut_outcome": "cleared",
            "study_name": "20260618T000000Z__SYM-ALPHA__calsweep",
        }
    ]

    # Must not raise — any DB write sentinel hit is an AssertionError
    try:
        entry_point(minimal_rows)
    except AssertionError:
        raise  # sentinel hit — propagate as test failure
    except Exception as exc:
        # Other exceptions (e.g. missing key) are implementation bugs, not
        # DB-write violations; still fail the test but with a clear message.
        pytest.fail(
            f"report entry point raised unexpectedly (non-DB-write error): "
            f"{type(exc).__name__}: {exc}"
        )


# ---------------------------------------------------------------------------
# AC-3.2 — Import-walk: no database write symbols at module level
# ---------------------------------------------------------------------------


def test_report_module_does_not_import_database_write_path_at_module_level():
    """The report script must not import database write-path symbols.

    Walks the module's __dict__ after import and asserts none of the known
    write-path symbol names appear as a direct attribute (i.e. were imported
    with `from database import <sym>`).

    Fails RED until the script exists and is confirmed write-path-free.
    """
    report_mod = _load_report_module()

    leaked = [sym for sym in _DB_WRITE_SYMBOLS if hasattr(report_mod, sym)]
    assert not leaked, (
        f"AC-3 VIOLATION: the report module imported write-path DB symbols at "
        f"module level: {leaked}. Advisory-only scripts must never import "
        f"write-path symbols."
    )


# ---------------------------------------------------------------------------
# AC-3.3 — (Secondary) alpha_bot_execution not imported at module level
# ---------------------------------------------------------------------------


def test_report_module_does_not_import_alpha_bot_execution():
    """The report script must not import alpha_bot_execution (the live engine).

    alpha_bot_execution is the live execution-path module; importing it from
    an advisory script risks wiring the sweep into live trading.

    This is a SECONDARY guard (sys.modules is process-global and can be
    polluted by other tests). Failure here is informative; the DB-sentinel
    test is the primary load-bearing assertion.
    """
    # Isolate: remove alpha_bot_execution from sys.modules before loading
    # so the report's module-level import (if any) is detectable via the
    # module's own namespace rather than relying solely on sys.modules.
    prior = sys.modules.pop("alpha_bot_execution", None)
    try:
        report_mod = _load_report_module()
        assert not hasattr(report_mod, "alpha_bot_execution"), (
            "AC-3 VIOLATION: the report module imported alpha_bot_execution "
            "at module level. The live execution path must not be imported "
            "by advisory tooling."
        )
    finally:
        if prior is not None:
            sys.modules["alpha_bot_execution"] = prior


# ---------------------------------------------------------------------------
# AC-3.4 — run_calibration_sweep return value has no write-confirmation keys
# ---------------------------------------------------------------------------


def test_run_calibration_sweep_return_has_no_write_confirmation_keys():
    """run_calibration_sweep return dicts must not contain write-confirmation keys.

    A write-confirmation key (e.g. 'rows_written', 'db_updated', 'committed')
    in the return value would indicate the sweep is writing to the DB.
    The return is advisory data only — it must not carry evidence of DB side-effects.

    Fails RED until run_calibration_sweep either does not exist (trivially no
    keys) or is confirmed to return advisory-only dicts.
    """
    import autotuner

    assert hasattr(autotuner, "run_calibration_sweep"), (
        "autotuner.run_calibration_sweep not found — implementer must add it"
    )

    # We do not call the actual sweep (it would run Optuna trials, too slow).
    # Instead, inspect the return type annotation or docstring for evidence of
    # write-confirmation semantics, and assert the function signature does NOT
    # suggest DB persistence.
    fn = autotuner.run_calibration_sweep
    source = inspect.getsource(fn)

    # Forbidden patterns that indicate DB writes inside the sweep
    forbidden_write_patterns = [
        "insert_advisor_observation",
        "set_symphony_live_mode",
        "save_autotune_run",
        "init_db(",
        "get_connection(",
        ".execute(",  # raw SQL execute
        "bot_state",
    ]

    violations = [p for p in forbidden_write_patterns if p in source]
    assert not violations, (
        f"AC-3 VIOLATION: run_calibration_sweep source contains write-path "
        f"patterns: {violations}. The sweep must be read-only / advisory."
    )
