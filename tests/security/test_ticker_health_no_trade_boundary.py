"""No-trade, no-route and no-write boundary of the dead-ticker check.

The check reads live symphony trees from Composer, asks Alpaca for one asset record per ticker,
stores one advisory row per run and posts to Discord. It starts from the 01:30 scheduler slot and
from the daemon start only. It may never place or cancel an order, liquidate, write or deploy a
Composer symphony, change a symphony's live mode, read the live-trading flag or import the engine
(beyond the Composer client's two allowed names), and no Flask route may start it.

Scans, each a pure function over module sources so the same scan also runs against sources with a
forbidden statement planted at several depths: a scan that cannot fail proves nothing.

  - the call graph walked from every start, helper and entry point of the check holds no forbidden
    fragment, and the walk really reaches the helpers it is meant to cover;
  - the only engine function the walk may enter is the Composer credential header builder;
  - the only database writer the check reaches is the advisory observation insert;
  - no route function reaches a start of the check or the runner's run function; the dashboard
    route reaches only the runner's stored-run read;
  - the two new modules import no engine, order, Composer write, advisor or reporting module, and
    the app imports them lazily;
  - no URL rule exposes the check;
  - the threads the check waits for are named by literal ``name=`` arguments in the app, so a
    rename fails here and not silently in production.

The forbidden fragment set, the import list and the docstring stripping are kept in this file on
purpose and are not imported from the sibling scanners, so an edit to one scanner never changes
another's guarantee.
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
    "perform_account_liquidation",
    "execute_sell_to_cash",
    "/orders",
    "/deploy",
    "/invest",
    "/sell",
    "/backtest",
)

# Modules the two new modules must not import, at module level or lazily. The runner reaches the
# engine only through the Composer portfolio client, which takes its two allowed names.
_FORBIDDEN_IMPORT_ROOTS = (
    "alpha_bot_execution",
    "app",
    "ai_advisor",
    "anthropic",
    "autotuner",
    "sleeves",
    "reporting",
    "composer_draft_client",
)
_FORBIDDEN_ADVISOR_MODULES = (
    "advisors.composer_draft_client",
    "advisors.composer_backtest_client",
    "advisors.frontrunner_builder",
    "advisors.strategy_builder_engine",
    "advisors.asset_swap_engine",
    "advisors.logic_change_engine",
    "advisors.retirement_explainer",
    "advisors.tenure_review_runner",
)

# The one engine function the walk may enter: the Composer client's credential header builder.
_ALLOWED_ENGINE_FUNCTIONS = frozenset({"get_composer_headers"})
_ENGINE = "alpha_bot_execution"

# Database functions that change a table. The check may reach exactly one.
_WRITE_PREFIXES = (
    "save_",
    "set_",
    "upsert_",
    "insert_",
    "record_",
    "register_",
    "append_",
    "delete_",
    "update_",
    "write_",
    "mark_",
    "init_",
    "run_migrations",
    "repair_",
)
_ALLOWED_DATABASE_WRITERS = frozenset({"insert_advisor_observation"})

Loader = Callable[[str], str | None]
Node = tuple[str, str]  # (module name, function name)

_APP = "app"
_MODULE = "advisors.ticker_health"
_RUNNER = "advisors.ticker_health_runner"

_APP_ENTRIES: tuple[Node, ...] = (
    (_APP, "_run_ticker_health_tick"),
    (_APP, "_start_ticker_health_catch_up"),
    (_APP, "_ticker_health_run_body"),
    (_APP, "_ticker_health_other_reader_running"),
    (_APP, "_ticker_health_settings"),
    (_APP, "_env_file_settings"),
    (_APP, "_ticker_health_catch_up_due"),
    (_APP, "_ticker_health_page_view"),
)
_RUNNER_ENTRIES: tuple[Node, ...] = (
    (_RUNNER, "run_ticker_health_check"),
    (_RUNNER, "read_latest_runs"),
)
_MODULE_ENTRIES: tuple[Node, ...] = (
    (_MODULE, "tree_ticker_uses"),
    (_MODULE, "classify_asset_answer"),
    (_MODULE, "carry_warnings"),
    (_MODULE, "page_view"),
    (_MODULE, "discord_messages"),
)
_ENTRIES = (*_APP_ENTRIES, *_RUNNER_ENTRIES, *_MODULE_ENTRIES)

# What no route may reach: every start of the check and the run function itself.
_ROUTE_FORBIDDEN_TARGETS: tuple[Node, ...] = (
    (_APP, "_run_ticker_health_tick"),
    (_APP, "_start_ticker_health_catch_up"),
    (_APP, "_ticker_health_run_body"),
    (_RUNNER, "run_ticker_health_check"),
)
# What the dashboard route reaches of the runner: the stored-run read and its record helper.
_ROUTE_ALLOWED_RUNNER_NODES = frozenset({"read_latest_runs", "_record_of"})


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
        # Module-level classes: a reference to the class reaches every one of its methods, which
        # are registered under "Class.method" (the run object's gate and clock live in methods).
        self.classes: dict[str, list[str]] = {}
        for node in self.tree.body:
            if isinstance(node, ast.ClassDef):
                self.classes[node.name] = []
                for member in node.body:
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        qualified = f"{node.name}.{member.name}"
                        self.functions[qualified] = member
                        self.classes[node.name].append(qualified)
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
            if module is None:
                continue
            if fn_name in module.classes:
                pending.extend((module_name, method) for method in module.classes[fn_name])
                continue
            if fn_name not in module.functions:
                continue
            fn = module.functions[fn_name]
            reached[(module_name, fn_name)] = fn
            for node in ast.walk(fn):
                if isinstance(node, ast.Name):
                    if node.id in module.functions or node.id in module.classes:
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
    """Forbidden fragments in every reached function; the engine's own functions are judged by
    the allowance below and by the engine's content pin, not by this scan."""
    found = []
    for (module, fn_name), node in sorted(_Graph(loader).reach(entries).items()):
        if module == _ENGINE:
            continue
        text = _executable_text(node)
        found.extend((module, fn_name, f) for f in _FORBIDDEN_FRAGMENTS if f in text)
    return found


def _engine_functions_entered(loader: Loader, entries) -> list[str]:
    reached = _Graph(loader).reach(entries)
    return sorted(fn for module, fn in reached if module == _ENGINE)


def _database_writers_reached(loader: Loader, entries) -> list[str]:
    reached = _Graph(loader).reach(entries)
    return sorted(
        fn
        for module, fn in reached
        if module == "database" and any(fn.startswith(p) for p in _WRITE_PREFIXES)
    )


def _imported_modules(source: str) -> set[str]:
    """Every imported dotted module name, module-level or lazy; ``from a import b`` adds ``a`` and
    ``a.b``."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _forbidden_imports(source: str) -> list[str]:
    names = _imported_modules(source)
    roots = {name.split(".")[0] for name in names}
    return sorted(
        (roots & set(_FORBIDDEN_IMPORT_ROOTS)) | (names & set(_FORBIDDEN_ADVISOR_MODULES))
    )


def _module_level_imports(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


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
        f"{module} defines no module-level function {fn_name!r}: the check would run unscanned"
    )


class TestTheWalkCoversTheCheck:
    def test_the_slot_reaches_the_body_which_reaches_the_runner_the_settings_and_the_wait(self):
        reached = _Graph(_read_repo_module).reach([(_APP, "_run_ticker_health_tick")])

        for target in (
            (_APP, "_ticker_health_run_body"),
            (_APP, "_ticker_health_settings"),
            (_APP, "_env_file_settings"),
            (_APP, "_ticker_health_other_reader_running"),
            (_RUNNER, "run_ticker_health_check"),
            ("advisors.tenure_review_runner", "configured_account_ids"),
        ):
            assert target in reached, f"the slot does not reach {target}: {sorted(reached)}"

    def test_the_catch_up_start_reaches_the_same_body_and_the_calendar(self):
        reached = _Graph(_read_repo_module).reach([(_APP, "_start_ticker_health_catch_up")])

        assert (_APP, "_ticker_health_run_body") in reached, sorted(reached)
        assert (_APP, "_ticker_health_catch_up_due") in reached, sorted(reached)
        assert any(module == "market_calendar" for module, _ in reached), (
            f"the catch-up decision reads no calendar function: {sorted(reached)}"
        )

    def test_the_runner_reaches_its_reads_its_one_write_and_the_market_gate(self):
        reached = _Graph(_read_repo_module).reach([(_RUNNER, "run_ticker_health_check")])

        for target in (
            ("advisors.composer_portfolio_client", "read_symphony_statistics"),
            ("advisors.composer_portfolio_client", "read_symphony_definition"),
            ("database", "insert_advisor_observation"),
            ("database", "get_advisor_observations_for_role"),
            ("market_calendar", "get_market_state"),
            (_MODULE, "classify_asset_answer"),
            (_MODULE, "discord_messages"),
        ):
            assert target in reached, f"the runner does not reach {target}: {sorted(reached)}"

    def test_the_dashboard_view_reaches_the_stored_run_read_and_the_page_text(self):
        reached = _Graph(_read_repo_module).reach([(_APP, "_ticker_health_page_view")])

        assert (_RUNNER, "read_latest_runs") in reached, sorted(reached)
        assert (_MODULE, "page_view") in reached, sorted(reached)


@pytest.mark.parametrize(("module", "fn_name"), _ENTRIES)
def test_no_order_deploy_symphony_write_or_live_flag_is_reachable_from_the_entry_point(
    module, fn_name
):
    reached = _Graph(_read_repo_module).reach([(module, fn_name)])
    assert (module, fn_name) in reached, f"{module}.{fn_name} is not defined, nothing was scanned"

    assert _violations(_read_repo_module, [(module, fn_name)]) == []


class TestTheCheckTouchesOnlyWhatItIsAllowedTo:
    def test_the_only_engine_function_any_entry_reaches_is_the_composer_header_builder(self):
        entered = _engine_functions_entered(_read_repo_module, _ENTRIES)

        assert set(entered) <= _ALLOWED_ENGINE_FUNCTIONS, entered

    def test_the_only_database_writer_any_entry_reaches_is_the_advisory_observation_insert(self):
        writers = _database_writers_reached(_read_repo_module, _ENTRIES)

        assert set(writers) <= _ALLOWED_DATABASE_WRITERS, writers
        assert "insert_advisor_observation" in writers, (
            "the walk does not reach the run row write: it is blind"
        )

    @pytest.mark.parametrize("module", [_MODULE, _RUNNER])
    def test_the_new_modules_import_no_engine_order_composer_write_app_or_advisor_module(
        self, module
    ):
        source = _read_repo_module(module)
        assert source is not None, f"{module} is missing"

        assert _forbidden_imports(source) == []

    @pytest.mark.parametrize("module", [_MODULE, _RUNNER])
    def test_the_new_modules_never_name_the_live_flag_or_a_live_mode_write(self, module):
        text = _executable_text(ast.parse(_read_repo_module(module)))

        for fragment in ("live_execution", "set_symphony_live_mode", "is_live", "live_mode"):
            assert fragment not in text, f"{module} names {fragment}"

    def test_the_runner_takes_from_the_universe_provider_only_the_paper_host_constant(self):
        source = _read_repo_module(_RUNNER)
        taken = [
            alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.module == "advisors.universe_provider"
            for alias in node.names
        ]
        provider_imports = [
            name
            for name in _imported_modules(source)
            if name == "advisors.universe_provider"
            or name.startswith("advisors.universe_provider.")
        ]

        assert taken == ["ALPACA_TRADING_BASE_URL"], taken
        assert set(provider_imports) <= {
            "advisors.universe_provider",
            "advisors.universe_provider.ALPACA_TRADING_BASE_URL",
        }, provider_imports

    def test_the_app_imports_the_two_new_modules_lazily_never_at_module_level(self):
        source = _read_repo_module(_APP)

        top = _module_level_imports(source)

        assert not any("ticker_health" in name for name in top), sorted(
            n for n in top if "ticker_health" in n
        )


# ---------------------------------------------------------------------------
# No route starts the check (the scheduler does; the dashboard only reads)
# ---------------------------------------------------------------------------


class TestNoRouteStartsTheCheck:
    def test_the_scheduler_registration_reaches_the_slot_and_through_it_the_runner(self):
        reached = _Graph(_read_repo_module).reach([(_APP, "run_scheduler")])

        for target in (
            (_APP, "_run_ticker_health_tick"),
            (_APP, "_ticker_health_run_body"),
            (_RUNNER, "run_ticker_health_check"),
        ):
            assert target in reached, f"run_scheduler does not reach {target}: the walk is blind"

    def test_no_route_function_reaches_a_start_of_the_check_or_the_run_function(self):
        graph = _Graph(_read_repo_module)
        routes = graph.route_functions()
        assert len(routes) > 50, "the route scan found too few routes to mean anything"

        reached = graph.reach(routes)

        offending = sorted(t for t in reached if t in _ROUTE_FORBIDDEN_TARGETS)
        assert offending == [], f"a route function starts the dead-ticker check: {offending}"

    def test_the_routes_reach_only_the_stored_run_read_of_the_runner(self):
        graph = _Graph(_read_repo_module)
        reached = graph.reach(graph.route_functions())

        runner_nodes = sorted(fn for module, fn in reached if module == _RUNNER)

        assert (_RUNNER, "read_latest_runs") in reached, (
            "the dashboard route does not reach the stored run read: the page shows nothing"
        )
        assert set(runner_nodes) <= _ROUTE_ALLOWED_RUNNER_NODES, runner_nodes

    def test_no_url_rule_exposes_the_check(self):
        import app as app_module

        rules = [
            (rule.rule, rule.endpoint)
            for rule in app_module.app.url_map.iter_rules()
            if "ticker_health" in rule.rule
            or "ticker-health" in rule.rule
            or "ticker_health" in rule.endpoint
        ]

        assert rules == []

    def test_a_route_that_calls_the_slot_callable_fails_the_route_scan(self):
        source = _read_repo_module(_APP)
        edit = _after_function(source, "dashboard", "_run_ticker_health_tick()")
        graph = _Graph(_loader_with({_APP: edit}))
        routes = graph.route_functions()
        assert (_APP, "dashboard") in routes, "test premise: dashboard is a registered route"

        reached = graph.reach(routes)

        assert (_APP, "_run_ticker_health_tick") in reached

    def test_a_route_that_calls_the_run_function_directly_fails_the_route_scan(self):
        source = _read_repo_module(_APP)
        edit = _after_function(
            source, "dashboard", "ticker_health_runner.run_ticker_health_check()"
        )
        edit = "from advisors import ticker_health_runner\n" + edit
        graph = _Graph(_loader_with({_APP: edit}))

        reached = graph.reach(graph.route_functions())

        assert (_RUNNER, "run_ticker_health_check") in reached


class TestTheThreadsTheCheckWaitsForAreNamedInTheApp:
    def test_the_waited_set_is_exactly_the_four_jobs_the_plan_names(self):
        import app as app_module

        waited = getattr(app_module, "_TICKER_HEALTH_WAITS_FOR_THREADS", None)
        assert waited is not None, "app._TICKER_HEALTH_WAITS_FOR_THREADS is missing"

        assert set(waited) == {
            "incubation-tick",
            "retirement-recommender-tick",
            "retirement-run-now",
            "tenure-review-on-demand",
        }

    def test_each_waited_name_is_the_literal_name_of_a_thread_the_app_builds(self):
        import app as app_module

        waited = getattr(app_module, "_TICKER_HEALTH_WAITS_FOR_THREADS", None)
        assert waited is not None, "app._TICKER_HEALTH_WAITS_FOR_THREADS is missing"
        names = _thread_name_literals(_read_repo_module(_APP))

        assert sorted(set(waited) - names) == [], (
            f"a waited thread name is not the name= of any threading.Thread call: {sorted(names)}"
        )

    def test_the_scan_names_a_thread_that_is_renamed(self):
        source = _read_repo_module(_APP)
        renamed = source.replace('name="incubation-tick"', 'name="incubation-job"')
        assert renamed != source, "test premise: the incubation thread carries that literal name"

        assert "incubation-tick" not in _thread_name_literals(renamed)


def _thread_name_literals(source: str) -> set[str]:
    """The string literal of every ``name=`` keyword of a ``threading.Thread(...)`` call."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr != "Thread":
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                found.add(keyword.value.value)
    return found


# ---------------------------------------------------------------------------
# The scans can fail: a forbidden statement planted into the app, a module, or a helper
# ---------------------------------------------------------------------------

_SOURCES = {
    name: _read_repo_module(name)
    for name in (
        _APP,
        _MODULE,
        _RUNNER,
        "database",
        "market_calendar",
        "advisors.composer_portfolio_client",
        "advisors.tenure_review_runner",
    )
}
_HELPER = "_planted_check_helper"
_START_ENTRIES = (
    (_APP, "_run_ticker_health_tick"),
    (_APP, "_start_ticker_health_catch_up"),
)


def _plant(module: str, fn_name: str, statement: str):
    return {module: lambda s: _after_function(s, fn_name, statement)}


def _plant_via_helper(module: str, fn_name: str, statement: str):
    return {
        module: lambda s: _with_function(
            _after_function(s, fn_name, f"{_HELPER}()"), _HELPER, statement
        )
    }


_PLANTS = {
    "symphony write in the body": (
        _plant(_APP, "_ticker_health_run_body", "composer_draft_client.save_symphony({})"),
        "save_symphony",
        _START_ENTRIES,
    ),
    "broker order in the slot callable": (
        _plant(_APP, "_run_ticker_health_tick", 'submit_order("SPY", 1)'),
        "submit_order",
        ((_APP, "_run_ticker_health_tick"),),
    ),
    "liquidation in the catch-up start": (
        _plant(_APP, "_start_ticker_health_catch_up", "perform_account_liquidation()"),
        "perform_account_liquidation",
        ((_APP, "_start_ticker_health_catch_up"),),
    ),
    "live flag read in the settings reader": (
        _plant(_APP, "_ticker_health_settings", 'os.environ.get("LIVE_EXECUTION")'),
        "live_execution",
        ((_APP, "_ticker_health_settings"), *_START_ENTRIES),
    ),
    "live-mode write in the other-reader predicate": (
        _plant(_APP, "_ticker_health_other_reader_running", 'set_symphony_live_mode("x", True)'),
        "set_symphony_live_mode",
        ((_APP, "_ticker_health_other_reader_running"), *_START_ENTRIES),
    ),
    "go-to-cash post in the page view": (
        _plant(_APP, "_ticker_health_page_view", 'requests.post("https://x/go-to-cash")'),
        "go-to-cash",
        ((_APP, "_ticker_health_page_view"),),
    ),
    "deploy post in the runner entry point": (
        _plant(_RUNNER, "run_ticker_health_check", 'requests.post("https://x/deploy/abc")'),
        "/deploy",
        ((_RUNNER, "run_ticker_health_check"), *_START_ENTRIES),
    ),
    "engine import in the runner entry point": (
        _plant(_RUNNER, "run_ticker_health_check", "import alpha_bot_execution"),
        "alpha_bot_execution",
        ((_RUNNER, "run_ticker_health_check"), *_START_ENTRIES),
    ),
    "backtest post in a helper the runner calls": (
        _plant_via_helper(_RUNNER, "run_ticker_health_check", 'requests.post(URL + "/backtest")'),
        "/backtest",
        ((_RUNNER, "run_ticker_health_check"), *_START_ENTRIES),
    ),
    "liquidation in the pure module's page view": (
        _plant(_MODULE, "page_view", "liquidate_all()"),
        "liquidat",
        ((_MODULE, "page_view"), (_APP, "_ticker_health_page_view")),
    ),
    "order in the observation insert the runner reaches": (
        _plant("database", "insert_advisor_observation", "place_order()"),
        "place_order",
        ((_RUNNER, "run_ticker_health_check"), *_START_ENTRIES),
    ),
    "close-position call in the Composer read the runner reaches": (
        _plant(
            "advisors.composer_portfolio_client", "read_symphony_definition", "close_position('X')"
        ),
        "close_position",
        ((_RUNNER, "run_ticker_health_check"), *_START_ENTRIES),
    ),
    "order in the calendar read the runner reaches": (
        _plant("market_calendar", "get_market_state", "cancel_order()"),
        "cancel_order",
        ((_RUNNER, "run_ticker_health_check"), *_START_ENTRIES),
    ),
    "order in the account id reader the body reaches": (
        _plant("advisors.tenure_review_runner", "configured_account_ids", "replace_order()"),
        "replace_order",
        ((_APP, "_ticker_health_run_body"), *_START_ENTRIES),
    ),
}


def _planted_loader(edits: dict) -> Loader:
    return _loader_with({m: edit(_SOURCES[m]) for m, edit in edits.items()})


_PLANT_CASES = [
    pytest.param(label, entry, id=f"{label} / from {entry[1]}")
    for label, (_, _, entries) in _PLANTS.items()
    for entry in entries
]


@pytest.mark.parametrize(("label", "entry"), _PLANT_CASES)
def test_a_forbidden_statement_planted_on_the_walked_graph_fails_the_scan(label, entry):
    edits, fragment, _ = _PLANTS[label]
    loader = _planted_loader(edits)

    found = _violations(loader, [entry])

    assert any(f == fragment for _, _, f in found), (
        f"planting [{label}] was not detected from {entry}: {found}"
    )


def test_a_harmless_statement_planted_in_the_runner_entry_point_passes_the_scan():
    loader = _loader_with(
        {_RUNNER: _after_function(_SOURCES[_RUNNER], "run_ticker_health_check", "_x = 1")}
    )

    assert _violations(loader, [(_RUNNER, "run_ticker_health_check")]) == []


def test_a_forbidden_call_in_a_function_the_check_never_reaches_is_not_attributed_to_it():
    loader = _loader_with(
        {_RUNNER: _with_function(_SOURCES[_RUNNER], "_planted_unreachable", "submit_order()")}
    )

    assert _violations(loader, [(_RUNNER, "run_ticker_health_check")]) == []


def test_a_second_engine_function_reached_by_the_runner_fails_the_engine_allowance():
    planted = _after_function(
        _SOURCES["advisors.composer_portfolio_client"],
        "read_symphony_definition",
        "alpha_bot_execution.fetch_symphony_stats(1)",
    )
    planted = "import alpha_bot_execution\n" + planted
    loader = _loader_with({"advisors.composer_portfolio_client": planted})

    entered = _engine_functions_entered(loader, [(_RUNNER, "run_ticker_health_check")])

    assert "fetch_symphony_stats" in entered
    assert not set(entered) <= _ALLOWED_ENGINE_FUNCTIONS


def test_a_second_database_writer_reached_by_the_runner_fails_the_writer_allowance():
    planted = _after_function(
        _SOURCES[_RUNNER], "run_ticker_health_check", 'database.save_state({"x": 1})'
    )
    loader = _loader_with({_RUNNER: planted})

    writers = _database_writers_reached(loader, [(_RUNNER, "run_ticker_health_check")])

    assert "save_state" in writers
    assert not set(writers) <= _ALLOWED_DATABASE_WRITERS


def test_the_import_scan_fails_on_a_planted_engine_import():
    planted = _SOURCES[_RUNNER] + "\nimport alpha_bot_execution\n"

    assert _forbidden_imports(planted)
    lazy = _after_function(_SOURCES[_RUNNER], "run_ticker_health_check", "from app import x")
    assert _forbidden_imports(lazy)


def test_the_import_scan_fails_on_a_planted_backtest_or_draft_client_import():
    backtest = _SOURCES[_RUNNER] + "\nfrom advisors import composer_backtest_client\n"
    draft = _SOURCES[_MODULE] + "\nimport advisors.composer_draft_client\n"

    assert "advisors.composer_backtest_client" in _forbidden_imports(backtest)
    assert "advisors.composer_draft_client" in _forbidden_imports(draft)


def test_the_lazy_import_scan_fails_on_a_module_level_import_of_the_runner():
    planted = "from advisors import ticker_health_runner\n" + _SOURCES[_APP]

    assert any("ticker_health" in name for name in _module_level_imports(planted))
