"""A stand-in left on a module function is put back, and the test that left it is named.

The guard in ``tests/conftest.py`` exists because an autouse ``patch`` and a ``monkeypatch``
on the same name undo in an order that can leave the autouse stub on the module. These tests
build that exact order on a throwaway module and check what the guard does with it.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from tests._function_leak_guard import FunctionLeakGuard

_MODULE_NAME = "leak_guard_probe_module"


def _real():
    return "real"


def _stub_return():
    return "stub"


@pytest.fixture()
def probe_module():
    module = types.ModuleType(_MODULE_NAME)
    module.read = _real
    sys.modules[_MODULE_NAME] = module
    try:
        yield module
    finally:
        sys.modules.pop(_MODULE_NAME, None)


@pytest.fixture()
def guard(probe_module):
    recorded = FunctionLeakGuard(((_MODULE_NAME, "read"),))
    recorded.snapshot()
    return recorded


@contextmanager
def _suite_stub():
    """What tests/ui/conftest.py does: a context-managed patch around every test."""
    with patch.object(sys.modules[_MODULE_NAME], "read", _stub_return):
        yield


def _leave_the_stub_behind():
    """The wrong undo order: the test's patch saved the suite stub, and is undone last."""
    module = sys.modules[_MODULE_NAME]
    suite_stub = _suite_stub()
    suite_stub.__enter__()
    test_patch = patch.object(module, "read", lambda: "test double")
    test_patch.start()
    suite_stub.__exit__(None, None, None)
    test_patch.stop()


def test_the_wrong_undo_order_really_leaves_the_stub(probe_module):
    _leave_the_stub_behind()

    assert probe_module.read is _stub_return, "the scenario must reproduce the leak it guards"


def test_a_stub_left_by_a_wrong_undo_order_is_replaced_by_the_real_function(probe_module, guard):
    _leave_the_stub_behind()

    leaks = guard.restore()

    assert probe_module.read is _real
    assert [(leak.module, leak.attribute, leak.leaked) for leak in leaks] == [
        (_MODULE_NAME, "read", _stub_return)
    ]


def test_a_second_restore_after_a_clean_test_reports_nothing(probe_module, guard):
    _leave_the_stub_behind()
    guard.restore()

    assert guard.restore() == []


def test_a_function_that_was_never_replaced_is_not_reported(probe_module, guard):
    assert guard.restore() == []
    assert probe_module.read is _real


def _function_from_source():
    namespace: dict = {}
    exec(compile("def read():\n    return 'real'\n", "<reload>", "exec"), namespace)  # noqa: S102
    return namespace["read"]


def test_a_reloaded_module_is_not_a_leak(probe_module):
    """A reload re-creates the function from the same source; that is the module's new truth."""
    probe_module.read = _function_from_source()
    reload_guard = FunctionLeakGuard(((_MODULE_NAME, "read"),))
    reload_guard.snapshot()
    reloaded = _function_from_source()
    probe_module.read = reloaded

    assert reload_guard.restore() == []
    assert probe_module.read is reloaded


def test_a_function_with_other_code_in_its_place_is_a_leak_even_when_it_is_a_function(
    probe_module, guard
):
    probe_module.read = lambda: "another body"

    leaks = guard.restore()

    assert len(leaks) == 1
    assert probe_module.read is _real


def test_a_guarded_name_that_was_deleted_is_restored(probe_module, guard):
    del probe_module.read

    leaks = guard.restore()

    assert [leak.attribute for leak in leaks] == ["read"]
    assert probe_module.read is _real


def test_a_module_that_is_not_imported_is_left_alone():
    absent = FunctionLeakGuard((("module_that_is_never_imported_here", "read"),))
    absent.snapshot()

    assert absent.restore() == []


def test_the_functions_the_ui_suite_stubs_are_guarded():
    from tests._function_leak_guard import GUARDED_FUNCTIONS

    assert {("database", "load_state"), ("app", "get_api_state_dict")} <= set(GUARDED_FUNCTIONS)


# ---------------------------------------------------------------------------
# Module values (paths): a reload re-reads the environment, so a reload inside one test can
# leave a module attribute naming a file that test then deleted. Unlike a function, a changed
# value after a reload is the leak, never the module's new truth.
# ---------------------------------------------------------------------------


@pytest.fixture()
def value_module():
    module = types.ModuleType(_MODULE_NAME)
    module.PATH = "/session/state.db"
    module.OPTIONAL = None
    sys.modules[_MODULE_NAME] = module
    try:
        yield module
    finally:
        sys.modules.pop(_MODULE_NAME, None)


@pytest.fixture()
def value_guard(value_module):
    recorded = FunctionLeakGuard((), values=((_MODULE_NAME, "PATH"), (_MODULE_NAME, "OPTIONAL")))
    recorded.snapshot()
    return recorded


def test_a_path_left_pointing_elsewhere_is_put_back_and_reported(value_module, value_guard):
    value_module.PATH = "/tmp/deleted-by-the-polluting-test.db"

    leaks = value_guard.restore()

    assert value_module.PATH == "/session/state.db"
    assert [(leak.module, leak.attribute, leak.leaked) for leak in leaks] == [
        (_MODULE_NAME, "PATH", "/tmp/deleted-by-the-polluting-test.db")
    ]


def test_a_value_equal_to_the_recorded_one_is_not_reported(value_module, value_guard):
    value_module.PATH = "".join(["/session/", "state.db"])

    assert value_guard.restore() == []


def test_a_recorded_none_is_restored_after_it_was_set(value_module, value_guard):
    value_module.OPTIONAL = "/tmp/other.db"

    leaks = value_guard.restore()

    assert value_module.OPTIONAL is None
    assert [leak.attribute for leak in leaks] == ["OPTIONAL"]


def test_a_second_value_restore_after_a_clean_test_reports_nothing(value_module, value_guard):
    value_module.PATH = "/tmp/other.db"
    value_guard.restore()

    assert value_guard.restore() == []


def test_the_database_paths_a_reload_re_reads_are_guarded():
    from tests._function_leak_guard import GUARDED_VALUES

    assert {
        ("database", "DB_FILE"),
        ("database", "_DB_FILE_DEFAULT"),
        ("analytics", "DB_FILE"),
    } <= set(GUARDED_VALUES)
