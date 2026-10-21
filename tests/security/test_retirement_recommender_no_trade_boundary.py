"""Retirement and tenure review modules: no-trade, no-engine boundary.

Mirrors tests/security/test_frontrunner_no_trade_boundary.py's structural, not-policy approach:
source-scans the REAL modules (not mocked stand-ins) so the guarantee holds regardless of future
refactors inside a module body. The scan covers the Retirement Recommender and every module of the
symphony tenure review: the review compute, the runner, the read-only Composer client and the
backtest client (the run's only Composer POST, and it is a backtest request, never a write to an
account).

The rule: a module contains NO trade, order, liquidation, deploy or LIVE_EXECUTION primitive and
never writes settings. Persistence is advisory-only (insert_advisor_observation forces
is_advisory_only=1). The recommender, the review compute and the runner never import the execution
engine at all; the Composer client imports exactly its base URL constant and its credential header
builder from it and nothing else (the backtest client takes the same two names). No module names the engine's per-account statistics reader, the
panic-sell function, the go-to-cash sell function or a go-to-cash URL: the first reads Composer
state through a path that cannot tell a failure from an empty account, and the others move money.

Every check is a function of a source text, so each is also run over small planted modules (below)
to show it fails when it should: a scan that cannot fail proves nothing.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import pathlib
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

_RECOMMENDER = "advisors/retirement_recommender.py"
_REVIEW = "advisors/tenure_review.py"
_RUNNER = "advisors/tenure_review_runner.py"
_CLIENT = "advisors/composer_portfolio_client.py"
_BACKTEST_CLIENT = "advisors/composer_backtest_client.py"
_COMPOSER_CLIENTS = (_CLIENT, _BACKTEST_CLIENT)
_MODULE_REL_PATHS = (_RECOMMENDER, _REVIEW, _RUNNER, _CLIENT, _BACKTEST_CLIENT)
_NEVER_IMPORT_THE_ENGINE = (_RECOMMENDER, _REVIEW, _RUNNER)

_ENGINE_MODULE = "alpha_bot_execution"
# The only two names a Composer client may take from the engine: the Composer base URL
# constant and the credential header builder.
_CLIENT_ALLOWED_ENGINE_NAMES = frozenset({"COMPOSER_BASE_URL", "get_composer_headers"})

# Trade/order/liquidation/deploy action tokens that must never appear as a whole
# underscore-delimited symbol/word component in a public name. Checked word-boundary (split on
# "_"), not bare substring, so e.g. "undeployed" or "deployment_target" would not trip it.
_FORBIDDEN_ACTION_WORDS = {
    "invest",
    "deploy",
    "sell",
    "buy",
    "order",
    "liquidate",
    "liquidation",
    "trade",
    "execute",
}
# Public names a module may carry although a component matches the list above, by module and
# exact name. The review compute's display sequence of its six evidence items is the only one:
# it orders rows on a page and places no order of any kind.
_PUBLIC_SYMBOL_EXEMPTIONS = {_REVIEW: frozenset({"EVIDENCE_ITEM_ORDER"})}

_FORBIDDEN_URL_FRAGMENTS = ("/deploy/", "/invest", "/sell", "/liquidate", "/go-to-cash")

# Engine and dashboard functions that read account state through a lossy path or move money.
_FORBIDDEN_NAMES = (
    "fetch_symphony_stats",
    "perform_account_liquidation",
    "execute_sell_to_cash",
    # The two other ways the application changes what is live: a symphony's own live mode (and
    # the settings route that sets it), and every order the Alpaca order module can submit.
    "set_symphony_live_mode",
    "save_symphony_settings",
    "submit_order",
    "submit_bracket_order",
    "submit_trailing_stop_order",
    "alpaca_orders",
)


def _source_of(rel_path: str) -> str:
    path = REPO_ROOT / rel_path
    if not path.exists():
        pytest.fail(f"expected module source not found: {rel_path}")
    return path.read_text(encoding="utf-8")


def _executable_source(source: str, label: str = "module") -> str:
    """Docstrings and comment lines removed via AST, so a module's own docstring may
    legitimately DOCUMENT an exclusion in prose (e.g. 'this module never calls
    invest_in_symphony') without tripping the scan."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        pytest.fail(f"{label} has a syntax error -- cannot source-scan")

    docstring_ranges: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                const = body[0].value
                if isinstance(const.value, str):
                    docstring_ranges.append(
                        (const.lineno, const.end_lineno if const.end_lineno else const.lineno)
                    )

    lines = source.splitlines()
    for start, end in docstring_ranges:
        for lineno in range(start, end + 1):
            if 1 <= lineno <= len(lines):
                lines[lineno - 1] = ""
    return "\n".join("" if line.lstrip().startswith("#") else line for line in lines)


# ---------------------------------------------------------------------------
# The checks, each a function of a source text (or of the names a module holds)
# ---------------------------------------------------------------------------


def _trade_shaped_public_names(names, exempt=frozenset()) -> list[str]:
    return [
        n
        for n in names
        if not n.startswith("_")
        and n not in exempt
        and any(word in n.lower().split("_") for word in _FORBIDDEN_ACTION_WORDS)
    ]


def _trade_url_fragments(source: str) -> list[str]:
    executable = _executable_source(source)
    return [fragment for fragment in _FORBIDDEN_URL_FRAGMENTS if fragment in executable]


def _forbidden_names_used(source: str) -> list[str]:
    executable = _executable_source(source)
    return [name for name in _FORBIDDEN_NAMES if name in executable]


def _engine_imports(source: str) -> list[tuple[str, tuple[str, ...]]]:
    """Every way the source reaches the execution engine module: ``import alpha_bot_execution``,
    ``from alpha_bot_execution import ...`` (at any depth, lazy imports included) and an
    ``importlib.import_module`` or ``__import__`` call with the module name as a literal.
    Returns (how, imported names) pairs; a whole-module import has no names."""
    tree = ast.parse(source)
    found: list[tuple[str, tuple[str, ...]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _ENGINE_MODULE or alias.name.startswith(_ENGINE_MODULE + "."):
                    found.append(("import", ()))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _ENGINE_MODULE or module.startswith(_ENGINE_MODULE + "."):
                found.append(("from", tuple(alias.name for alias in node.names)))
        elif isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == _ENGINE_MODULE:
                    found.append(("dynamic", ()))
    return found


def _engine_import_violations(source: str, allowed: frozenset[str] = frozenset()) -> list:
    """Engine imports that are not a ``from`` import of only the ``allowed`` names."""
    return [
        entry
        for entry in _engine_imports(source)
        if not (entry[0] == "from" and entry[1] and set(entry[1]) <= allowed)
    ]


def _has_wildcard_import(source: str) -> bool:
    return any(
        isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
        for node in ast.walk(ast.parse(source))
    )


_HTTP_WRITE_METHODS = frozenset({"post", "put", "patch", "delete", "request"})
_BACKTEST_URL_SOURCE = "f'{COMPOSER_BASE_URL}/backtest'"


def _http_write_calls(source: str) -> list[tuple[str, str]]:
    """``(method, first argument as source)`` of every call to a method named like an HTTP write
    (post, put, patch, delete, or the generic request), on any object: ``requests.post(url)``,
    ``session.put(url)``, ``requests.request("POST", url)``."""
    return [
        (node.func.attr, ast.unparse(node.args[0]) if node.args else "")
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _HTTP_WRITE_METHODS
    ]


def _assigned_values(source: str, name: str) -> list[str]:
    """The source of every value assigned to the plain name ``name``."""
    return [
        ast.unparse(node.value)
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    ]


def _binds(node: ast.AST, name: str) -> bool:
    """Whether ``node`` binds ``name``: a store or delete of the name, a parameter, an import
    alias, an exception name, a def or class of that name, a pattern capture, or a ``global`` or
    ``nonlocal`` declaration."""
    if isinstance(node, ast.Name):
        return node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))
    if isinstance(node, ast.arg):
        return node.arg == name
    if isinstance(node, ast.alias):
        return (node.asname or node.name.split(".")[0]) == name
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return node.name == name
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return name in node.names
    if isinstance(node, ast.MatchMapping):
        return node.rest == name
    return False


def _binding_sites(source: str, name: str) -> list[ast.AST]:
    """Every place the module binds ``name``, by any means: an assignment (plain, annotated,
    augmented, chained, unpacked, or the walrus), a loop or comprehension variable, a context
    manager or exception name, an import alias, a parameter, a def or class of that name, a
    pattern capture, or a ``global`` or ``nonlocal`` declaration."""
    return [node for node in ast.walk(ast.parse(source)) if _binds(node, name)]


def _backtest_client_problems(source: str) -> list:
    """What differs from the shape the backtest client must keep: one POST of the address it
    built, and that address the backtest endpoint only, bound once, by a plain assignment."""
    problems = []
    if _http_write_calls(source) != [("post", "url")]:
        problems.append(("writes", _http_write_calls(source)))
    if _assigned_values(source, "url") != [_BACKTEST_URL_SOURCE]:
        problems.append(("addresses", _assigned_values(source, "url")))
    if len(_binding_sites(source, "url")) != 1:
        problems.append(("bindings of the address", len(_binding_sites(source, "url"))))
    return problems


def _advisory_flag_mentions(source: str) -> bool:
    return "is_advisory_only" in _executable_source(source)


def _live_execution_mentions(source: str) -> bool:
    return "LIVE_EXECUTION" in _executable_source(source)


def _trade_module_mentions(source: str) -> list[str]:
    executable = _executable_source(source)
    return [t for t in ("invest_in_symphony", "composer_draft_client") if t in executable]


# ---------------------------------------------------------------------------
# The real modules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_rel_path", _MODULE_REL_PATHS)
class TestNoTradeSymbols:
    def test_module_exists(self, module_rel_path):
        assert (REPO_ROOT / module_rel_path).exists(), (
            f"{module_rel_path} does not exist -- the no-trade boundary is unverifiable"
        )

    def test_module_exposes_no_trade_shaped_public_symbols(self, module_rel_path):
        mod = importlib.import_module(module_rel_path.replace("/", ".").removesuffix(".py"))
        offending = _trade_shaped_public_names(
            dir(mod), _PUBLIC_SYMBOL_EXEMPTIONS.get(module_rel_path, frozenset())
        )
        assert offending == [], (
            f"{module_rel_path} exposes trade-shaped public symbols: {offending}"
        )

    def test_module_never_constructs_a_trade_url_fragment_in_executable_code(self, module_rel_path):
        assert _trade_url_fragments(_source_of(module_rel_path)) == [], (
            f"{module_rel_path} references a trade, deploy or go-to-cash endpoint in executable code"
        )

    def test_module_never_names_the_lossy_state_reader_or_a_money_moving_function(
        self, module_rel_path
    ):
        assert _forbidden_names_used(_source_of(module_rel_path)) == [], (
            f"{module_rel_path} names an engine or dashboard function this feature must never use"
        )

    def test_module_never_calls_invest_in_symphony_or_composer_draft_client(self, module_rel_path):
        assert _trade_module_mentions(_source_of(module_rel_path)) == []

    def test_module_never_reads_or_sets_live_execution(self, module_rel_path):
        assert not _live_execution_mentions(_source_of(module_rel_path))

    def test_module_does_not_use_wildcard_imports(self, module_rel_path):
        """Even an indirect `from alpha_bot_execution import *` (or from any other module)
        would make the absence of trade primitives unverifiable by source-scan."""
        assert not _has_wildcard_import(_source_of(module_rel_path))

    def test_module_never_overrides_is_advisory_only(self, module_rel_path):
        """database.insert_advisor_observation forces is_advisory_only=1 in its own SQL
        regardless of caller kwargs; none of these modules writes a row or passes the flag."""
        assert not _advisory_flag_mentions(_source_of(module_rel_path))


@pytest.mark.parametrize("module_rel_path", _NEVER_IMPORT_THE_ENGINE)
class TestNoEngineImport:
    def test_module_never_imports_the_execution_engine(self, module_rel_path):
        """An AST check, not a substring one: a docstring may name the engine, no import
        statement (top-level, lazy, aliased or dynamic) may reach it."""
        assert _engine_import_violations(_source_of(module_rel_path)) == [], (
            f"{module_rel_path} must never import alpha_bot_execution (the live execution "
            "engine) -- it is a purely advisory, off-execution-path module."
        )


@pytest.mark.parametrize("client_rel_path", _COMPOSER_CLIENTS)
class TestEachComposerClientTakesTwoNamesFromTheEngine:
    def test_it_imports_exactly_the_base_url_and_the_header_builder(self, client_rel_path):
        imports = _engine_imports(_source_of(client_rel_path))
        assert [kind for kind, _ in imports] == ["from"], imports
        assert set(imports[0][1]) == _CLIENT_ALLOWED_ENGINE_NAMES, (
            "the client takes COMPOSER_BASE_URL and get_composer_headers from the engine, "
            f"nothing more and nothing less; it imports {imports[0][1]}"
        )
        assert (
            _engine_import_violations(_source_of(client_rel_path), _CLIENT_ALLOWED_ENGINE_NAMES)
            == []
        )


class TestTheBacktestClientMakesOnlyTheBacktestPost:
    """Every request of a review run is a read or a backtest. The reads are GETs by the portfolio
    client; the backtest client makes the run's only POST, and its only address is the backtest
    endpoint. The checks read the source for the HTTP write calls and for every address the
    module builds."""

    def test_its_only_write_call_is_one_post_to_the_address_it_built(self):
        source = _source_of(_BACKTEST_CLIENT)
        assert _http_write_calls(source) == [("post", "url")]

    def test_the_address_it_builds_is_the_base_url_and_the_backtest_path_and_nothing_else(self):
        source = _source_of(_BACKTEST_CLIENT)
        assert _assigned_values(source, "url") == [_BACKTEST_URL_SOURCE]

    def test_the_address_is_bound_once_and_never_rebound_in_any_way(self):
        """A plain assignment is the only binding of ``url``: an augmented or annotated
        assignment, a walrus, a loop variable or a parameter after it would change the address
        the one POST goes to, and a check of plain assignments alone would not see it."""
        assert len(_binding_sites(_source_of(_BACKTEST_CLIENT), "url")) == 1

    def test_the_portfolio_client_makes_no_write_call_at_all(self):
        assert _http_write_calls(_source_of(_CLIENT)) == []


class TestRoutesAreNotSettingsWrites:
    @pytest.mark.parametrize(
        "path", ["/api/retirement-recommendations", "/ai-advisor/retirement/review/run"]
    )
    def test_route_not_in_allowlist(self, path):
        import app as app_module  # noqa: PLC0415

        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        assert allowlist is not None, "app._SETTINGS_WRITE_ALLOWLIST is missing"
        assert path not in allowlist


# ---------------------------------------------------------------------------
# Mutation proof: each check run over planted modules
# ---------------------------------------------------------------------------

_CLEAN = '''"""This module never calls fetch_symphony_stats, perform_account_liquidation or
execute_sell_to_cash and never visits /go-to-cash; it imports nothing from alpha_bot_execution."""
import math

def read(x):
    # a comment may say fetch_symphony_stats too
    return math.floor(x)
'''


def _plant(statement: str, *, head: str = "") -> str:
    return f"{head}\ndef planted(account):\n    {statement}\n"


@pytest.mark.parametrize(
    "statement",
    [
        "fetch_symphony_stats(account)",
        "alpha_bot_execution.fetch_symphony_stats(account)",
        "return fetch_symphony_stats",
        "perform_account_liquidation(account)",
        "app.perform_account_liquidation(account)",
        "execute_sell_to_cash(account)",
        "alpha_bot_execution.execute_sell_to_cash(account)",
        'getattr(module, "fetch_symphony_stats")(account)',
        "database.set_symphony_live_mode(account, 1, 'tenure')",
        "return set_symphony_live_mode",
        "save_symphony_settings(account)",
        "sleeves.alpaca_orders.submit_order(client, account)",
        "submit_order(account)",
        "alpaca_orders.submit_bracket_order(client, account)",
        "alpaca_orders.submit_trailing_stop_order(client, account)",
        "from sleeves.alpaca_orders import submit_order as go",
        'getattr(module, "submit_order")(account)',
    ],
    ids=[
        "stats reader call",
        "stats reader attribute call",
        "stats reader reference",
        "liquidation call",
        "liquidation attribute call",
        "sell to cash call",
        "sell to cash attribute call",
        "stats reader by string",
        "live-mode write",
        "live-mode write reference",
        "settings route call",
        "alpaca order attribute call",
        "alpaca order call",
        "alpaca bracket order",
        "alpaca trailing stop order",
        "alpaca order import",
        "alpaca order by string",
    ],
)
class TestAPlantedForbiddenNameIsReported:
    def test_the_clean_module_is_not_reported(self, statement):
        assert _forbidden_names_used(_CLEAN) == []

    def test_a_planted_use_is_reported(self, statement):
        assert _forbidden_names_used(_plant(statement)) != []

    def test_a_planted_use_in_a_docstring_or_a_comment_is_not_reported(self, statement):
        prose = f'def f():\n    """Never {statement}."""\n    # not {statement}\n    return 1\n'
        assert _forbidden_names_used(prose) == []


@pytest.mark.parametrize(
    "statement",
    [
        'requests.post(BASE + "/go-to-cash")',
        'url = f"{BASE}/portfolio/accounts/{account}/sell"',
        'url = BASE + "/deploy/" + account',
        'url = BASE + "/invest"',
        'url = BASE + "/liquidate"',
    ],
    ids=["go to cash", "sell", "deploy", "invest", "liquidate"],
)
class TestAPlantedTradeEndpointIsReported:
    def test_the_clean_module_is_not_reported(self, statement):
        assert _trade_url_fragments(_CLEAN) == []

    def test_a_planted_endpoint_is_reported(self, statement):
        assert _trade_url_fragments(_plant(statement)) != []


class TestAPlantedEngineImportIsReported:
    @pytest.mark.parametrize(
        "head",
        [
            "import alpha_bot_execution",
            "import alpha_bot_execution as engine",
            "from alpha_bot_execution import COMPOSER_BASE_URL",
            "from alpha_bot_execution import fetch_symphony_stats",
            "from alpha_bot_execution import *",
        ],
        ids=["plain", "aliased", "base url", "stats reader", "wildcard"],
    )
    def test_a_module_that_must_not_import_the_engine_is_flagged_for_any_import(self, head):
        assert _engine_import_violations(_CLEAN) == []
        assert _engine_import_violations(_plant("return 1", head=head)) != []

    @pytest.mark.parametrize(
        "statement",
        [
            "import alpha_bot_execution",
            "from alpha_bot_execution import COMPOSER_BASE_URL",
            'importlib.import_module("alpha_bot_execution")',
            '__import__("alpha_bot_execution")',
        ],
        ids=["lazy plain", "lazy from", "importlib", "dunder import"],
    )
    def test_a_lazy_or_dynamic_import_inside_a_function_is_flagged(self, statement):
        assert _engine_import_violations(_plant(statement)) != []

    def test_the_client_form_passes_and_any_other_form_does_not(self):
        two = "from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers"
        one = "from alpha_bot_execution import COMPOSER_BASE_URL"
        assert (
            _engine_import_violations(_plant("return 1", head=two), _CLIENT_ALLOWED_ENGINE_NAMES)
            == []
        )
        assert (
            _engine_import_violations(_plant("return 1", head=one), _CLIENT_ALLOWED_ENGINE_NAMES)
            == []
        )
        for head in (
            "from alpha_bot_execution import COMPOSER_BASE_URL, fetch_symphony_stats",
            "from alpha_bot_execution import get_composer_headers, execute_sell_to_cash",
            "import alpha_bot_execution",
            "from alpha_bot_execution import *",
        ):
            assert (
                _engine_import_violations(
                    _plant("return 1", head=head), _CLIENT_ALLOWED_ENGINE_NAMES
                )
                != []
            ), head
        assert (
            _engine_import_violations(
                _plant("import alpha_bot_execution"), _CLIENT_ALLOWED_ENGINE_NAMES
            )
            != []
        )

    def test_the_client_check_requires_both_names_to_be_present(self):
        """The real-module test above pins the exact pair; this shows the pair check itself can
        tell a missing name from a full pair."""
        one = _plant("return 1", head="from alpha_bot_execution import COMPOSER_BASE_URL")
        imports = _engine_imports(one)
        assert set(imports[0][1]) != _CLIENT_ALLOWED_ENGINE_NAMES


class TestAPlantedTradeShapedPublicNameIsReported:
    @pytest.mark.parametrize(
        "name",
        ["place_order", "sell_all", "PANIC_SELL_LIMIT", "liquidate_position", "trade_rate"],
    )
    def test_a_trade_shaped_public_name_is_reported_unless_private(self, name):
        assert _trade_shaped_public_names(["read", name]) == [name]
        assert _trade_shaped_public_names(["read", "_" + name]) == []

    def test_an_exemption_covers_exactly_the_name_it_lists(self):
        exempt = _PUBLIC_SYMBOL_EXEMPTIONS[_REVIEW]
        assert exempt == frozenset({"EVIDENCE_ITEM_ORDER"})
        assert _trade_shaped_public_names(["EVIDENCE_ITEM_ORDER"], exempt) == []
        assert _trade_shaped_public_names(["EVIDENCE_ITEM_ORDER_X", "place_order"], exempt) == [
            "EVIDENCE_ITEM_ORDER_X",
            "place_order",
        ]
        assert _trade_shaped_public_names(["EVIDENCE_ITEM_ORDER"]) == ["EVIDENCE_ITEM_ORDER"]
        assert set(_PUBLIC_SYMBOL_EXEMPTIONS) == {_REVIEW}, "no other module is exempt"

    def test_a_planted_public_function_is_found_through_dir_of_a_loaded_module(self, tmp_path):
        planted = tmp_path / "planted_module.py"
        planted.write_text(_CLEAN + "\ndef place_order(x):\n    return x\n", encoding="utf-8")
        spec = importlib.util.spec_from_file_location("planted_module_for_scan", planted)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert _trade_shaped_public_names(dir(module)) == ["place_order"]


class TestOtherPlantedPrimitivesAreReported:
    def test_live_execution_composer_draft_client_and_the_advisory_flag(self):
        assert not _live_execution_mentions(_CLEAN)
        assert _live_execution_mentions(_plant('return os.environ.get("LIVE_EXECUTION")'))
        assert _trade_module_mentions(_CLEAN) == []
        assert _trade_module_mentions(_plant("composer_draft_client.save_symphony(1)")) != []
        assert _trade_module_mentions(_plant("invest_in_symphony(1)")) != []
        assert not _advisory_flag_mentions(_CLEAN)
        assert _advisory_flag_mentions(_plant("insert(is_advisory_only=0)"))
        assert not _has_wildcard_import(_CLEAN)
        assert _has_wildcard_import("from somewhere import *\n")


_BACKTEST_CLIENT_SHAPE = (
    "import requests\n"
    "def run_backtest(tree):\n"
    '    url = f"{COMPOSER_BASE_URL}/backtest"\n'
    "    return requests.post(url, json=tree)\n"
)


class TestAPlantedSecondRequestInTheBacktestClientIsReported:
    def test_the_unplanted_shape_is_clean(self):
        assert _backtest_client_problems(_BACKTEST_CLIENT_SHAPE) == []

    @pytest.mark.parametrize(
        "statement",
        [
            "requests.put(url, json=tree)",
            "requests.patch(url, json=tree)",
            "requests.delete(url)",
            "requests.post(other_url, json=tree)",
            'requests.post(COMPOSER_BASE_URL + "/deploy/accounts", json=tree)',
            'requests.request("POST", url, json=tree)',
            "session.post(url, json=tree)",
            "client.put(url)",
        ],
        ids=[
            "put",
            "patch",
            "delete",
            "post to another address",
            "post to a deploy address",
            "generic request",
            "session post",
            "client put",
        ],
    )
    def test_a_second_write_call_is_reported(self, statement):
        planted = _BACKTEST_CLIENT_SHAPE + f"def planted(tree):\n    {statement}\n"
        assert _backtest_client_problems(planted) != []

    @pytest.mark.parametrize(
        "assignment",
        [
            'url = f"{COMPOSER_BASE_URL}/deploy/accounts"',
            'url = f"{COMPOSER_BASE_URL}/portfolio/accounts/x/go-to-cash"',
            'url = COMPOSER_BASE_URL + "/invest"',
            'url = "https://other.example/backtest"',
        ],
        ids=["deploy", "go to cash", "invest", "another host"],
    )
    def test_a_second_address_is_reported(self, assignment):
        planted = _BACKTEST_CLIENT_SHAPE + f"def planted():\n    {assignment}\n"
        assert _backtest_client_problems(planted) != []

    @pytest.mark.parametrize(
        "rebinding",
        [
            'url += "/../symphonies"',
            'url: str = f"{COMPOSER_BASE_URL}/symphonies"',
            '(url := f"{COMPOSER_BASE_URL}/symphonies")',
            'url, planted_other = (f"{COMPOSER_BASE_URL}/symphonies", 1)',
            'url = planted_other = f"{COMPOSER_BASE_URL}/symphonies"',
            'for url in (f"{COMPOSER_BASE_URL}/symphonies",):\n    pass',
            "with planted_open() as url:\n    pass",
            "import planted_module as url",
            "try:\n    pass\nexcept Exception as url:\n    pass",
            '[url for url in (f"{COMPOSER_BASE_URL}/symphonies",)]',
            "global url",
            'url = f"{COMPOSER_BASE_URL}/symphonies"',
        ],
        ids=[
            "augmented assignment",
            "annotated assignment",
            "walrus",
            "tuple unpacking",
            "chained assignment",
            "loop variable",
            "context manager",
            "import alias",
            "exception name",
            "comprehension variable",
            "global declaration",
            "second plain assignment",
        ],
    )
    @pytest.mark.parametrize("where", ["between the address and the post", "in another function"])
    def test_a_rebinding_of_the_address_in_any_form_is_reported(self, rebinding, where):
        body = textwrap.indent(rebinding, "    ")
        if where == "between the address and the post":
            planted = _BACKTEST_CLIENT_SHAPE.replace(
                "    return requests.post", body + "\n    return requests.post"
            )
        else:
            planted = _BACKTEST_CLIENT_SHAPE + "def planted(tree):\n" + body + "\n"
        assert planted != _BACKTEST_CLIENT_SHAPE
        assert _backtest_client_problems(planted) != []

    @pytest.mark.parametrize(
        "planted",
        [
            _BACKTEST_CLIENT_SHAPE.replace("run_backtest(tree)", "run_backtest(tree, url=None)"),
            _BACKTEST_CLIENT_SHAPE + "def planted(url):\n    return url\n",
            _BACKTEST_CLIENT_SHAPE + "planted = lambda url: url\n",
            _BACKTEST_CLIENT_SHAPE + "def url():\n    return None\n",
        ],
        ids=["parameter with a default", "parameter of another function", "lambda", "def"],
    )
    def test_a_parameter_def_or_lambda_named_like_the_address_is_reported(self, planted):
        assert _backtest_client_problems(planted) != []

    def test_the_unplanted_shape_binds_the_address_once(self):
        assert len(_binding_sites(_BACKTEST_CLIENT_SHAPE, "url")) == 1

    def test_the_post_to_a_different_address_than_the_backtest_one_is_reported(self):
        planted = _BACKTEST_CLIENT_SHAPE.replace("/backtest", "/deploy/accounts")
        assert _backtest_client_problems(planted) != []
