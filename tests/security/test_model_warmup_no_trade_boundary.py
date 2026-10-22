"""No-trade boundary of the model warm-up helpers in app.py.

``_start_model_warmup`` (spawns the daemon thread) and ``_model_warmup_worker``
(resolves both model knobs through the Anthropic Models API) run at daemon start
and from the scheduler. They are advisory: they may build an Anthropic client and
list models, and nothing else. No broker order call, no Composer symphony write
or deploy call, no live-execution flag and no engine import may be reachable from
either of them, in app.py or in any repository module their call graph enters.

The scan is a pure function over module sources, so it is also run against
sources with a forbidden call planted at several depths: a scan that cannot fail
proves nothing. The forbidden fragment set and the docstring stripping are kept
in this file on purpose and are not imported from the sibling scanners, so an edit
to one scanner never changes another's guarantee.
"""

from __future__ import annotations

import ast
import copy
import pathlib
from collections.abc import Callable

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_ENTRY_MODULE = "app"
_ENTRY_POINTS = ("_start_model_warmup", "_model_warmup_worker")

# Lower-cased fragments: a hit anywhere in an executable (docstring-free) function
# source, as an identifier, an attribute, an import or a string, is a violation.
_FORBIDDEN_FRAGMENTS = (
    "submit_order",
    "place_order",
    "cancel_order",
    "replace_order",
    "close_position",
    "liquidat",
    "go_to_cash",
    "go-to-cash",
    "save_symphony",
    "composer_draft_client",
    "alpha_bot_execution",
    "live_execution",
    "set_symphony_live_mode",
    "/orders",
    "/deploy",
    "/invest",
    "/sell",
)

Loader = Callable[[str], str | None]
Node = tuple[str, str]  # (module name, function name)


def _read_repo_module(module: str) -> str | None:
    path = REPO_ROOT / (module.replace(".", "/") + ".py")
    return path.read_text(encoding="utf-8") if path.is_file() else None


class _ParsedModule:
    def __init__(self, name: str, source: str, loader: Loader):
        self.name = name
        self.tree = ast.parse(source)
        self.functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {
            node.name: node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.imports = self._collect_imports(loader)

    def _collect_imports(self, loader: Loader) -> dict[str, tuple[str, str | None]]:
        """Local name -> (module, attribute or None) for every repository-local import."""
        found: dict[str, tuple[str, str | None]] = {}
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name if alias.asname else alias.name.split(".")[0]
                    if loader(target) is not None:
                        found[alias.asname or target] = (target, None)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                for alias in node.names:
                    local = alias.asname or alias.name
                    if loader(f"{node.module}.{alias.name}") is not None:
                        found[local] = (f"{node.module}.{alias.name}", None)
                    elif loader(node.module) is not None:
                        found[local] = (node.module, alias.name)
        return found


def _executable_text(node: ast.AST) -> str:
    """Lower-cased source of ``node`` with every docstring blanked."""
    clone = copy.deepcopy(node)
    for inner in ast.walk(clone):
        if isinstance(inner, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = inner.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                inner.body = body[1:] or [ast.Pass()]
    return ast.unparse(clone).lower()


def _reachable(loader: Loader, entry: str) -> dict[Node, ast.AST]:
    """Every repository function the entry point reaches by call, thread target or callback.

    An edge is any reference to a module-level function: a bare name of the same
    module, a name imported from another repository module, or an attribute of an
    imported repository module. References, not only calls, so ``Thread(target=f)``
    and a callback handed to an executor are followed.
    """
    modules: dict[str, _ParsedModule] = {}

    def parsed(name: str) -> _ParsedModule | None:
        if name not in modules:
            source = loader(name)
            if source is None:
                return None
            modules[name] = _ParsedModule(name, source, loader)
        return modules[name]

    reached: dict[Node, ast.AST] = {}
    pending: list[Node] = [(_ENTRY_MODULE, entry)]
    while pending:
        module_name, fn_name = pending.pop()
        if (module_name, fn_name) in reached:
            continue
        module = parsed(module_name)
        if module is None or fn_name not in module.functions:
            continue
        fn = module.functions[fn_name]
        reached[(module_name, fn_name)] = fn
        for node in ast.walk(fn):
            if isinstance(node, ast.Name):
                if node.id in module.functions:
                    pending.append((module_name, node.id))
                elif node.id in module.imports:
                    target, attr = module.imports[node.id]
                    if attr is not None:
                        pending.append((target, attr))
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in module.imports
            ):
                target, attr = module.imports[node.value.id]
                if attr is None:
                    pending.append((target, node.attr))
    return reached


def _violations(loader: Loader, entry: str) -> list[tuple[str, str, str]]:
    found = []
    for (module, fn_name), node in sorted(_reachable(loader, entry).items()):
        text = _executable_text(node)
        found.extend((module, fn_name, f) for f in _FORBIDDEN_FRAGMENTS if f in text)
    return found


def _after_function(source: str, fn_name: str, statement: str) -> str:
    """``source`` with ``statement`` appended to the module-level function's body."""
    fn = next(
        n
        for n in ast.parse(source).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name
    )
    lines = source.splitlines()
    lines.insert(fn.end_lineno, f"    {statement}")
    return "\n".join(lines) + "\n"


def _with_function(source: str, name: str, statement: str) -> str:
    return f"{source}\n\ndef {name}():\n    {statement}\n"


def _loader_with(replacements: dict[str, str]) -> Loader:
    return lambda module: replacements.get(module, _read_repo_module(module))


# ---------------------------------------------------------------------------
# The real tree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("entry", _ENTRY_POINTS)
def test_entry_point_is_defined_in_app_py(entry):
    assert entry in _ParsedModule("app", _read_repo_module("app"), _read_repo_module).functions, (
        f"app.py defines no module-level function {entry!r}: the warm-up would run unscanned"
    )


def test_the_walk_from_the_starter_follows_the_thread_target_into_the_worker():
    reached = _reachable(_read_repo_module, "_start_model_warmup")

    assert (_ENTRY_MODULE, "_model_warmup_worker") in reached, sorted(reached)


def test_the_walk_from_the_worker_enters_the_client_factory_and_the_resolver_in_other_modules():
    reached = _reachable(_read_repo_module, "_model_warmup_worker")

    assert ("ai_advisor", "_build_client") in reached, sorted(reached)
    assert ("model_config", "resolve") in reached, sorted(reached)


@pytest.mark.parametrize("entry", _ENTRY_POINTS)
def test_no_order_deploy_symphony_write_or_live_flag_is_reachable_from_the_entry_point(entry):
    assert _violations(_read_repo_module, entry) == []


# ---------------------------------------------------------------------------
# The scan can fail: a forbidden call planted into the app, a helper or another module
# ---------------------------------------------------------------------------

_APP = _read_repo_module("app")
_MODEL_CONFIG = _read_repo_module("model_config")
_AI_ADVISOR = _read_repo_module("ai_advisor")
_HELPER = "_planted_warmup_helper"

_PLANTS = {
    "symphony write in the worker": (
        {
            "app": lambda s: _after_function(
                s, "_model_warmup_worker", "composer_draft_client.save_symphony({})"
            )
        },
        "composer_draft_client",
        _ENTRY_POINTS,
    ),
    "symphony import in the worker": (
        {
            "app": lambda s: _after_function(
                s,
                "_model_warmup_worker",
                "from advisors.composer_draft_client import save_symphony",
            )
        },
        "save_symphony",
        _ENTRY_POINTS,
    ),
    "broker order in the starter": (
        {"app": lambda s: _after_function(s, "_start_model_warmup", 'submit_order("SPY", 1)')},
        "submit_order",
        ("_start_model_warmup",),
    ),
    "live flag read in the worker": (
        {
            "app": lambda s: _after_function(
                s, "_model_warmup_worker", 'os.environ.get("LIVE_EXECUTION")'
            )
        },
        "live_execution",
        _ENTRY_POINTS,
    ),
    "engine import in the worker": (
        {"app": lambda s: _after_function(s, "_model_warmup_worker", "import alpha_bot_execution")},
        "alpha_bot_execution",
        _ENTRY_POINTS,
    ),
    "liquidation in a helper the worker calls": (
        {
            "app": lambda s: _with_function(
                _after_function(s, "_model_warmup_worker", f"{_HELPER}()"),
                _HELPER,
                "liquidate_all()",
            )
        },
        "liquidat",
        _ENTRY_POINTS,
    ),
    "live-mode write in a helper the starter calls": (
        {
            "app": lambda s: _with_function(
                _after_function(s, "_start_model_warmup", f"{_HELPER}()"),
                _HELPER,
                'set_symphony_live_mode("x", True)',
            )
        },
        "set_symphony_live_mode",
        ("_start_model_warmup",),
    ),
    "go-to-cash in the resolver module": (
        {"model_config": lambda s: _after_function(s, "resolve", "go_to_cash()")},
        "go_to_cash",
        _ENTRY_POINTS,
    ),
    "broker order in the client factory": (
        {"ai_advisor": lambda s: _after_function(s, "_build_client", "place_order()")},
        "place_order",
        _ENTRY_POINTS,
    ),
}
_SOURCES = {"app": _APP, "model_config": _MODEL_CONFIG, "ai_advisor": _AI_ADVISOR}


def _planted_loader(edits: dict) -> Loader:
    return _loader_with({m: edit(_SOURCES[m]) for m, edit in edits.items()})


@pytest.mark.parametrize("label", list(_PLANTS))
def test_a_forbidden_call_planted_on_the_walked_graph_fails_the_scan(label):
    edits, fragment, entries = _PLANTS[label]
    loader = _planted_loader(edits)

    for entry in entries:
        found = _violations(loader, entry)
        assert any(f == fragment for _, _, f in found), (
            f"planting [{label}] was not detected from {entry}: {found}"
        )


def test_a_harmless_statement_planted_in_the_worker_passes_the_scan():
    loader = _loader_with(
        {"app": _after_function(_APP, "_model_warmup_worker", "_planted_flag = 1")}
    )

    assert _violations(loader, "_model_warmup_worker") == []


def test_a_forbidden_call_in_a_function_the_warmup_never_reaches_is_not_attributed_to_it():
    loader = _loader_with({"app": _with_function(_APP, "_planted_unreachable", "submit_order()")})

    for entry in _ENTRY_POINTS:
        assert _violations(loader, entry) == []
