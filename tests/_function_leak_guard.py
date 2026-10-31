"""Put module functions back after a test when a patch or monkeypatch undo left a stand-in.

Several suites replace a module function for every test under them (``tests/ui/conftest.py``
stubs ``database.load_state`` and ``app.get_api_state_dict`` with an autouse ``patch``). A test
that also patches the same name with ``monkeypatch`` saves the stub as the value to restore,
and when its undo runs after the autouse patch's, the stub is written back and stays on the
module for every later test in the worker. Each undo is correct in isolation; only their order
is wrong, so no single polluter is the cause and none can be fixed once for good.

``FunctionLeakGuard`` records the real function of each guarded name once, before any test
runs, and after each test puts it back if anything else is bound there, returning what it
found so the caller can say which test left it.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass

# (module name, attribute): the functions a suite's conftest replaces with an autouse patch.
GUARDED_FUNCTIONS: tuple[tuple[str, str], ...] = (
    ("database", "load_state"),
    ("app", "get_api_state_dict"),
)

_MISSING = object()


@dataclass(frozen=True)
class Leak:
    module: str
    attribute: str
    leaked: object

    def describe(self) -> str:
        return f"{self.module}.{self.attribute} was left bound to {self.leaked!r}"


def _same_definition(candidate: object, pristine: object) -> bool:
    """True when *candidate* is the same function re-created by a module reload.

    A reload builds new function objects from unchanged source; their code objects compare
    equal, while a stub (a Mock, a lambda, a test's own function) does not.
    """
    candidate_code = getattr(candidate, "__code__", None)
    pristine_code = getattr(pristine, "__code__", None)
    return candidate_code is not None and candidate_code == pristine_code


class FunctionLeakGuard:
    def __init__(self, guarded: tuple[tuple[str, str], ...] = GUARDED_FUNCTIONS) -> None:
        self._guarded = guarded
        self._pristine: dict[tuple[str, str], object] = {}

    def snapshot(self, *, import_modules: bool = False) -> None:
        """Record each guarded function not yet recorded.

        With ``import_modules`` a guarded module no test has imported yet is imported first: a
        module first imported inside a test could already hold a stand-in when it is recorded.
        """
        for module_name, attribute in self._guarded:
            if (module_name, attribute) in self._pristine:
                continue
            module = sys.modules.get(module_name)
            if module is None and import_modules:
                module = importlib.import_module(module_name)
            value = getattr(module, attribute, _MISSING)
            if value is not _MISSING:
                self._pristine[(module_name, attribute)] = value

    def restore(self) -> list[Leak]:
        """Rebind every guarded name that no longer holds the recorded function; return those."""
        leaks: list[Leak] = []
        for (module_name, attribute), pristine in self._pristine.items():
            module = sys.modules.get(module_name)
            if module is None:
                continue
            current = getattr(module, attribute, _MISSING)
            if current is pristine:
                continue
            if current is not _MISSING and _same_definition(current, pristine):
                self._pristine[(module_name, attribute)] = current
                continue
            setattr(module, attribute, pristine)
            leaks.append(Leak(module_name, attribute, current))
        return leaks
