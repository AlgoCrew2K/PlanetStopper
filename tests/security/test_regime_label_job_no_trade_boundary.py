"""No-trade and no-route boundary of the daily regime label job.

The job writes one table (``regime_label_cache``) that the live engine reads, and it runs only from
the 04:30 scheduler slot. It may read the stored bot state, call the market-data API for daily
closes, classify, and write the cache row. It must never be able to place or cancel an order,
liquidate, write or deploy a Composer symphony, flip a symphony's live mode, read the live-trading
flag, or import the engine, and no Flask route may reach it or the cache write.

Four scans, each a pure function over module sources so the same scan also runs against sources
with a forbidden statement planted at several depths: a scan that cannot fail proves nothing.

  - the call graph walked from the slot callable, the worker, the state loader, the job entry point
    and the price fetch holds no forbidden fragment;
  - the walk really reaches the helpers it is meant to cover (otherwise an empty walk would pass);
  - no route function reaches the job, the worker, the slot callable or ``save_regime_label``,
    while the scheduler registration does (so the route result is not an artefact of a blind walker);
  - the job module imports no engine, order, Composer-write or advisor module.

The forbidden fragment set and the docstring stripping are kept in this file on purpose and are not
imported from the sibling scanners, so an edit to one scanner never changes another's guarantee.
"""

from __future__ import annotations

import ast
import copy
import pathlib
from collections.abc import Callable

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# Lower-cased fragments: a hit anywhere in an executable (docstring-free) function source, as an
# identifier, an attribute, an import or a string, is a violation.
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

# Modules the job module must not import, at module level or lazily.
_FORBIDDEN_IMPORT_ROOTS = (
    "alpha_bot_execution",
    "app",
    "ai_advisor",
    "anthropic",
    "advisors",
    "autotuner",
    "sleeves",
    "reporting",
)

Loader = Callable[[str], str | None]
Node = tuple[str, str]  # (module name, function name)

_JOB = "regime_label_job"
_APP = "app"

# The slot, its worker and its state loader live in app.py; the entry point and the price fetch
# live in the job module.
_ENTRIES: tuple[Node, ...] = (
    (_APP, "_run_regime_label_tick"),
    (_APP, "_regime_label_job_worker"),
    (_APP, "_regime_label_symphony_entries"),
    (_JOB, "run_regime_label_job"),
    (_JOB, "fetch_sip_daily_closes"),
)

# What no route may reach: the job in any form and the cache write.
_ROUTE_FORBIDDEN_TARGETS: tuple[Node, ...] = (
    (_APP, "_run_regime_label_tick"),
    (_APP, "_regime_label_job_worker"),
    ("database", "save_regime_label"),
)


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


class _Graph:
    """Call graph over the repository modules a loader serves, parsed once per graph."""

    def __init__(self, loader: Loader):
        self._loader = loader
        self._modules: dict[str, _ParsedModule | None] = {}

    def module(self, name: str) -> _ParsedModule | None:
        if name not in self._modules:
            source = self._loader(name)
            self._modules[name] = (
                None if source is None else _ParsedModule(name, source, self._loader)
            )
        return self._modules[name]

    def reach(self, entries) -> dict[Node, ast.AST]:
        """Every repository function the entries reach by call, thread target or callback.

        An edge is any reference to a module-level function: a bare name of the same module, a name
        imported from another repository module, or an attribute of an imported repository module.
        References, not only calls, so ``Thread(target=f)`` and a callback handed to a scheduler or
        an executor are followed.
        """
        reached: dict[Node, ast.AST] = {}
        pending: list[Node] = list(entries)
        while pending:
            module_name, fn_name = pending.pop()
            if (module_name, fn_name) in reached:
                continue
            module = self.module(module_name)
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

    def route_functions(self) -> list[Node]:
        """Every app.py function registered with an ``@app.route(...)`` decorator."""
        module = self.module(_APP)
        routes = []
        for name, fn in module.functions.items():
            for decorator in fn.decorator_list:
                call = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (
                    isinstance(call, ast.Attribute)
                    and call.attr in {"route", "get", "post", "put", "patch", "delete"}
                    and isinstance(call.value, ast.Name)
                    and call.value.id == "app"
                ):
                    routes.append((_APP, name))
        return routes


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


def _violations(loader: Loader, entries) -> list[tuple[str, str, str]]:
    found = []
    for (module, fn_name), node in sorted(_Graph(loader).reach(entries).items()):
        text = _executable_text(node)
        found.extend((module, fn_name, f) for f in _FORBIDDEN_FRAGMENTS if f in text)
    return found


def _imported_roots(source: str) -> set[str]:
    """First dotted component of every import in the source, module-level or lazy."""
    roots: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _after_function(source: str, fn_name: str, statement: str) -> str:
    """``source`` with ``statement`` appended to the module-level function's body."""
    fn = next(
        (
            n
            for n in ast.parse(source).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name
        ),
        None,
    )
    assert fn is not None, f"no module-level function {fn_name!r} to plant a statement into"
    lines = source.splitlines()
    lines.insert(fn.end_lineno, f"    {statement}")
    return "\n".join(lines) + "\n"


def _with_function(source: str, name: str, statement: str) -> str:
    return f"{source}\n\ndef {name}():\n    {statement}\n"


def _loader_with(replacements: dict[str, str]) -> Loader:
    return lambda module: replacements.get(module, _read_repo_module(module))


def _defined(module: str, fn_name: str) -> bool:
    parsed = _Graph(_read_repo_module).module(module)
    return parsed is not None and fn_name in parsed.functions


# ---------------------------------------------------------------------------
# The real tree
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("module", "fn_name"), _ENTRIES)
def test_every_scanned_entry_point_is_defined(module, fn_name):
    assert _defined(module, fn_name), (
        f"{module}.py defines no module-level function {fn_name!r}: the job would run unscanned"
    )


class TestTheWalkCoversTheJob:
    def test_the_slot_callable_reaches_its_worker_which_reaches_the_job_and_the_loader(self):
        reached = _Graph(_read_repo_module).reach([(_APP, "_run_regime_label_tick")])

        assert (_APP, "_regime_label_job_worker") in reached, sorted(reached)
        assert (_JOB, "run_regime_label_job") in reached, sorted(reached)
        assert (_APP, "_regime_label_symphony_entries") in reached, sorted(reached)

    def test_the_job_reaches_its_price_fetch_its_cache_write_and_the_classifier(self):
        reached = _Graph(_read_repo_module).reach([(_JOB, "run_regime_label_job")])

        assert (_JOB, "fetch_sip_daily_closes") in reached, sorted(reached)
        assert ("database", "save_regime_label") in reached, sorted(reached)
        assert ("regime_classifier", "classify_regime") in reached, sorted(reached)
        assert ("synthetic_history", "get_alpaca_headers") in reached, sorted(reached)

    def test_the_state_loader_reaches_the_read_path_and_the_symphony_shape_check(self):
        reached = _Graph(_read_repo_module).reach([(_APP, "_regime_label_symphony_entries")])

        assert ("database", "load_state") in reached, sorted(reached)
        assert (_APP, "_is_symphony_state_entry") in reached, sorted(reached)


@pytest.mark.parametrize(("module", "fn_name"), _ENTRIES)
def test_no_order_deploy_symphony_write_or_live_flag_is_reachable_from_the_entry_point(
    module, fn_name
):
    reached = _Graph(_read_repo_module).reach([(module, fn_name)])
    assert (module, fn_name) in reached, f"{module}.{fn_name} is not defined, nothing was scanned"

    assert _violations(_read_repo_module, [(module, fn_name)]) == []


def test_the_job_module_imports_no_engine_order_composer_write_app_or_advisor_module():
    source = _read_repo_module(_JOB)
    assert source is not None, "regime_label_job.py is missing"

    assert sorted(_imported_roots(source) & set(_FORBIDDEN_IMPORT_ROOTS)) == []


# ---------------------------------------------------------------------------
# No route reaches the job or the cache write (the scheduler does)
# ---------------------------------------------------------------------------


class TestNoRouteReachesTheJob:
    def test_the_scheduler_registration_reaches_the_job_and_the_write(self):
        reached = _Graph(_read_repo_module).reach([(_APP, "run_scheduler")])

        for target in (*_ROUTE_FORBIDDEN_TARGETS, (_JOB, "run_regime_label_job")):
            assert target in reached, f"run_scheduler does not reach {target}: the walk is blind"

    def test_no_route_function_reaches_the_job_the_worker_the_slot_or_the_cache_write(self):
        graph = _Graph(_read_repo_module)
        routes = graph.route_functions()
        assert len(routes) > 50, "the route scan found too few routes to mean anything"

        reached = graph.reach(routes)

        offending = sorted(t for t in reached if t in _ROUTE_FORBIDDEN_TARGETS or t[0] == _JOB)
        assert offending == [], f"a route function reaches the regime label job: {offending}"

    def test_no_url_rule_exposes_the_job(self):
        import app as app_module

        rules = [
            (rule.rule, rule.endpoint)
            for rule in app_module.app.url_map.iter_rules()
            if "regime_label" in rule.rule or "regime_label" in rule.endpoint
        ]
        assert rules == []


# ---------------------------------------------------------------------------
# The scans can fail: a forbidden statement planted into the app, the job, or a helper
# ---------------------------------------------------------------------------

_SOURCES = {
    name: _read_repo_module(name)
    for name in (_APP, _JOB, "database", "synthetic_history", "regime_classifier")
}
_HELPER = "_planted_job_helper"
_ALL_ENTRIES = _ENTRIES
_APP_ENTRIES = ((_APP, "_run_regime_label_tick"), (_APP, "_regime_label_job_worker"))


def _plant(module: str, fn_name: str, statement: str):
    return {module: lambda s: _after_function(s, fn_name, statement)}


def _plant_via_helper(module: str, fn_name: str, statement: str):
    return {
        module: lambda s: _with_function(
            _after_function(s, fn_name, f"{_HELPER}()"), _HELPER, statement
        )
    }


_PLANTS = {
    "symphony write in the worker": (
        _plant(_APP, "_regime_label_job_worker", "composer_draft_client.save_symphony({})"),
        "composer_draft_client",
        _APP_ENTRIES,
    ),
    "broker order in the slot callable": (
        _plant(_APP, "_run_regime_label_tick", 'submit_order("SPY", 1)'),
        "submit_order",
        ((_APP, "_run_regime_label_tick"),),
    ),
    "live flag read in the state loader": (
        _plant(_APP, "_regime_label_symphony_entries", 'os.environ.get("LIVE_EXECUTION")'),
        "live_execution",
        ((_APP, "_regime_label_symphony_entries"), *_APP_ENTRIES),
    ),
    "engine import in the job entry point": (
        _plant(_JOB, "run_regime_label_job", "import alpha_bot_execution"),
        "alpha_bot_execution",
        ((_JOB, "run_regime_label_job"), *_APP_ENTRIES),
    ),
    "symphony write in the job entry point": (
        _plant(_JOB, "run_regime_label_job", "composer_draft_client.save_symphony({})"),
        "save_symphony",
        ((_JOB, "run_regime_label_job"), *_APP_ENTRIES),
    ),
    "go-to-cash post in the price fetch": (
        _plant(_JOB, "fetch_sip_daily_closes", 'requests.post("https://x/go-to-cash")'),
        "go-to-cash",
        ((_JOB, "run_regime_label_job"), (_JOB, "fetch_sip_daily_closes"), *_APP_ENTRIES),
    ),
    "liquidation in a helper the job calls": (
        _plant_via_helper(_JOB, "run_regime_label_job", "liquidate_all()"),
        "liquidat",
        ((_JOB, "run_regime_label_job"), *_APP_ENTRIES),
    ),
    "live-mode write in a helper the worker calls": (
        _plant_via_helper(_APP, "_regime_label_job_worker", 'set_symphony_live_mode("x", True)'),
        "set_symphony_live_mode",
        _APP_ENTRIES,
    ),
    "order in the cache write the job reaches": (
        _plant("database", "save_regime_label", "place_order()"),
        "place_order",
        ((_JOB, "run_regime_label_job"), *_APP_ENTRIES),
    ),
    "order in the credential helper the fetch reaches": (
        _plant("synthetic_history", "get_alpaca_headers", "cancel_order()"),
        "cancel_order",
        ((_JOB, "run_regime_label_job"), *_APP_ENTRIES),
    ),
    "close-position call in the classifier the job reaches": (
        _plant("regime_classifier", "classify_regime", "close_position('SPY')"),
        "close_position",
        ((_JOB, "run_regime_label_job"), *_APP_ENTRIES),
    ),
}


def _planted_loader(edits: dict) -> Loader:
    return _loader_with({m: edit(_SOURCES[m]) for m, edit in edits.items()})


@pytest.mark.parametrize("label", list(_PLANTS))
def test_a_forbidden_statement_planted_on_the_walked_graph_fails_the_scan(label):
    edits, fragment, entries = _PLANTS[label]
    loader = _planted_loader(edits)

    for entry in entries:
        found = _violations(loader, [entry])
        assert any(f == fragment for _, _, f in found), (
            f"planting [{label}] was not detected from {entry}: {found}"
        )


def test_a_harmless_statement_planted_in_the_job_entry_point_passes_the_scan():
    loader = _loader_with({_JOB: _after_function(_SOURCES[_JOB], "run_regime_label_job", "_x = 1")})

    assert _violations(loader, [(_JOB, "run_regime_label_job")]) == []


def test_a_forbidden_call_in_a_function_the_job_never_reaches_is_not_attributed_to_it():
    loader = _loader_with(
        {_JOB: _with_function(_SOURCES[_JOB], "_planted_unreachable", "submit_order()")}
    )

    assert _violations(loader, [(_JOB, "run_regime_label_job")]) == []


def test_a_route_that_calls_the_slot_callable_fails_the_route_scan():
    edit = _after_function(_SOURCES[_APP], "dashboard", "_run_regime_label_tick()")
    graph = _Graph(_loader_with({_APP: edit}))
    routes = graph.route_functions()
    assert (_APP, "dashboard") in routes, "test premise: dashboard is a registered route"

    reached = graph.reach(routes)

    assert (_APP, "_run_regime_label_tick") in reached


def test_a_route_that_writes_the_cache_directly_fails_the_route_scan():
    edit = _after_function(
        _SOURCES[_APP], "dashboard", 'database.save_regime_label("x", "trending", "d")'
    )
    graph = _Graph(_loader_with({_APP: edit}))

    reached = graph.reach(graph.route_functions())

    assert ("database", "save_regime_label") in reached


def test_the_import_scan_fails_on_a_planted_engine_import():
    planted = _SOURCES[_JOB] + "\nimport alpha_bot_execution\n"

    assert _imported_roots(planted) & set(_FORBIDDEN_IMPORT_ROOTS)
    lazy = _after_function(_SOURCES[_JOB], "run_regime_label_job", "from advisors import x")
    assert _imported_roots(lazy) & set(_FORBIDDEN_IMPORT_ROOTS)
