"""RED tests -- Retirement Approval Lifecycle no-auto-trade boundary (AC-8).

Cycle 2c addendum (feature-plans/retirement-approval-polish.md,
DE-RETIRE-POLISH-001, ret3-review finding (b)): Groups A-D below are
byte-unchanged from the Cycle 2b (PR #139) round and continue to cover the
approve/reject routes + the 2 explainer/checklist modules. Cycle 2c added 4
NEW call paths this file did not previously structurally cover --
_retirement_recommender_tick_worker, _retirement_find_reusable_prior_
explanation, _join_retirement_approval_status, and ai_advisor_tab()'s own
name-resolution -- see Group E at the bottom. ret3-review hand-read the
diff and confirmed zero exec seam in all 4 (no defect today); Group E makes
that a STRUCTURAL, durable regression guard instead of a one-time review
finding -- GREEN-ON-ARRIVAL, not a RED-then-GREEN fix.

feature-plans/retirement-approval-lifecycle.md AC-8: "Extend/parametrize
tests/security/test_retirement_recommender_no_trade_boundary.py (or a sibling)
to source-scan EVERY new module (retirement_explainer.py,
retirement_checklist.py) for NO reachable exec/trade primitive [...] The new
routes are asserted NOT in _SETTINGS_WRITE_ALLOWLIST. [...] the scan forbids
executable trade CODE PATHS, NOT advisory string content [...] the safety
guarantee is 'no exec call path reachable,' proven by the scan + a call-graph
assertion."

This is a SIBLING file (never edits the Cycle-2a
test_retirement_recommender_no_trade_boundary.py, which stays byte-unchanged
and continues covering advisors/retirement_recommender.py only). The
docstring-stripping AST helper, forbidden-token set, and forbidden-URL-
fragment set are DELIBERATELY duplicated verbatim from that file rather than
imported -- security scanners in this codebase are self-contained, never
cross-import (a future edit to one module's scan must not silently change
another module's guarantee).

DESIGN NOTE (the AC-8 nuance, load-bearing -- read before "fixing" this
file): the identifier/public-symbol checks below scan for forbidden tokens
as whole underscore-delimited WORD COMPONENTS in *identifiers* (via
dir(module) and AST Name/Attribute node text), and the URL-fragment checks
scan for specific PATH fragments (e.g. "/sell", "/liquidate") -- neither
check bans the bare words "sell"/"liquidate"/"deploy" appearing inside a
plain string literal. This is intentional: advisors/retirement_checklist.py
necessarily emits advisory prose like "manually sell/liquidate the
following positions in Composer" as checklist STEP TEXT, a string value,
never an identifier or a URL. Do not "fix" these tests to also scan string
literal contents for the bare words -- that would make AC-6's own checklist
prose fail this file's tests despite containing zero executable trade code,
contradicting the plan's explicit AC-8 ruling.

Cycle 2e addendum (feature-plans/retirement-visibility.md AC-8,
DE-RETIRE-VISIBILITY-001): Group E gains the new Run-now route handler
(`ai_advisor_retirement_run`), the new last-run read helper
(`_fetch_retirement_last_run`) and the scheduler wrapper
(`_run_retirement_recommender_tick`) as entry points (the tick WORKER was
already covered). Group G (new) source-scans the NEW read-only Composer
client `advisors/composer_series_client.py`: no trade/deploy/draft
primitive, GET-only, and its only `alpha_bot_execution` import is the
precedent-allowed `COMPOSER_BASE_URL` / `get_composer_headers` pair (same
as advisors/composer_backtest_client.py). The Group G scan is a pure
function over source text so it is ALSO exercised against synthetic
mutated sources (non-vacuity) -- and was mutation-checked against a
scratch copy of the real module by the RED author.

Expected state: RED until advisors/retirement_explainer.py and
advisors/retirement_checklist.py exist, and until app.py gains the two new
retirement approve/reject routes with the pinned handler names below.
"""

from __future__ import annotations

import ast
import copy
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The advisory modules under the extended safety boundary (AC-8): the two
# retirement modules, plus the model-resolution and request-helper modules,
# which talk to the Anthropic API and so sit next to the explainer's LLM seam.
_NEW_MODULE_REL_PATHS = (
    "advisors/retirement_explainer.py",
    "advisors/retirement_checklist.py",
    "model_config.py",
    "llm_request.py",
)

# Pinned in .claude/tdd-handoff.md -- the two new Flask route handler
# function names (mirrors ai_advisor_proposal_approve/_reject's naming
# convention). The static call-graph tests below locate these functions by
# name inside app.py's AST.
_APPROVE_ROUTE_FN_NAME = "ai_advisor_retirement_approve"
_REJECT_ROUTE_FN_NAME = "ai_advisor_retirement_reject"
_APPROVE_ROUTE_PATH = "/ai-advisor/retirement/approve"
_REJECT_ROUTE_PATH = "/ai-advisor/retirement/reject"
_RUN_ROUTE_PATH = "/ai-advisor/retirement/run"  # Cycle 2e AC-7

# Verbatim duplicate of test_retirement_recommender_no_trade_boundary.py's
# own token set -- see this file's module docstring for why duplication
# (not import) is the deliberate choice for a security scanner.
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

_FORBIDDEN_URL_FRAGMENTS = ("/deploy/", "/invest", "/sell", "/liquidate")

# The single sanctioned LLM-client-construction seam in this codebase
# (ai_advisor._build_client). Neither new module, nor either new route
# handler, may reach it -- the operator ruling (Gate-2b) that the LLM is
# strictly OUT of the approve/reject/checklist action path.
_LLM_SEAM_ATTR = "_build_client"
_EXPLAINER_ENTRYPOINT = "explain_recommendation"


def _read_source(rel_path: str) -> str:
    path = REPO_ROOT / rel_path
    if not path.exists():
        pytest.fail(f"expected module source not found: {rel_path}")
    return path.read_text(encoding="utf-8")


def _read_executable_source(rel_path: str) -> str:
    """Docstrings/comments stripped via AST -- mirrors
    test_retirement_recommender_no_trade_boundary.py's identical helper so a
    module's own docstring may legitimately DOCUMENT an exclusion in prose
    without tripping the scan."""
    source = _read_source(rel_path)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        pytest.fail(f"{rel_path} has a syntax error -- cannot source-scan")

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

    stripped_lines = []
    for line in lines:
        stripped_lines.append("" if line.lstrip().startswith("#") else line)
    return "\n".join(stripped_lines)


def _app_py_source() -> str:
    """Raw app.py source text -- for Group F's scoped source-window scan
    (Cycle 2c). Distinct from _parse_app_py() below, which returns a parsed
    AST for Group C/E's transitive call-graph walk."""
    app_path = REPO_ROOT / "app.py"
    if not app_path.exists():
        pytest.fail("app.py not found -- cannot source-scan")
    return app_path.read_text(encoding="utf-8")


def _parse_app_py() -> ast.Module:
    app_path = REPO_ROOT / "app.py"
    if not app_path.exists():
        pytest.fail("app.py not found -- cannot source-scan route handlers")
    return ast.parse(app_path.read_text(encoding="utf-8"))


def _find_function_def(
    tree: ast.Module, fn_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            return node
    return None


def _find_module_level_function(
    tree: ast.Module, fn_name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            return node
    return None


def _references_name_or_attr(subtree: ast.AST, target: str) -> bool:
    """True iff `target` appears as a bare Name or as the trailing attribute
    of an Attribute chain (e.g. `ai_advisor._build_client` or a
    directly-imported `_build_client`) anywhere in `subtree`."""
    for node in ast.walk(subtree):
        if isinstance(node, ast.Name) and node.id == target:
            return True
        if isinstance(node, ast.Attribute) and node.attr == target:
            return True
        # An import that names the target (``from x import target as other``) is a reference.
        if isinstance(node, ast.alias) and (
            node.name.split(".")[-1] == target or node.asname == target
        ):
            return True
    return False


def _resolve_bare_name_callee(call_node: ast.Call) -> str | None:
    """Return the plain function name a Call node targets IFF it's a bare
    `some_name(...)` call (e.g. `_dispatch_retirement_decision(...)`).
    Attribute-target calls (`database.upsert_retirement_decision(...)`,
    `jsonify(...)` is itself a bare name but resolved-away below since it
    isn't a local app.py def) are deliberately not resolved here -- those
    target ANOTHER module's function, already caught directly by the plain
    substring/Name/Attribute checks on the calling subtree; we only need to
    recurse into functions actually DEFINED in app.py's own module scope."""
    func = call_node.func
    if isinstance(func, ast.Name):
        return func.id
    return None


def _stored_names(target: ast.AST) -> set[str]:
    """The plain names an assignment target stores (a tuple or list target stores each element)."""
    return {
        n.id for n in ast.walk(target) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }


def _module_level_binding_statements(body: list[ast.stmt]):
    """Every statement that binds a name at module level, including those inside a module-level
    ``if``, ``try``, ``with`` or loop (a conditional import is still an import). A def or class
    body is not entered: what it binds is local to it."""
    for stmt in body:
        yield stmt
        for field in ("body", "orelse", "finalbody"):
            inner = getattr(stmt, field, None)
            if inner and not isinstance(
                stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                yield from _module_level_binding_statements(inner)
        for handler in getattr(stmt, "handlers", None) or []:
            yield from _module_level_binding_statements(handler.body)


def _module_level_bindings(tree: ast.Module) -> dict[str, list[ast.stmt]]:
    """Name to the module-level statements that bind it by an import (``import a.b as c``,
    ``from a import b as c``), an assignment (plain, annotated, augmented, tuple), or a table (a
    dict, list or tuple literal assigned to a name). A function a def binds is not listed: the
    walk opens a def by name."""
    bindings: dict[str, list[ast.stmt]] = {}
    for stmt in _module_level_binding_statements(tree.body):
        names: set[str] = set()
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            names = {(a.asname or a.name.split(".")[0]) for a in stmt.names}
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                names |= _stored_names(target)
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
            names = _stored_names(stmt.target)
        for name in names:
            bindings.setdefault(name, []).append(stmt)
    return bindings


def _function_defs_by_name(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Each function name to the first def of it ``_find_function_def`` would find (the walk
    reads the whole tree, so the walk below builds this once instead of once per call)."""
    defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.setdefault(node.name, node)
    return defs


def _collect_transitive_local_call_subtrees(
    tree: ast.Module,
    fn_name: str,
    *,
    _visited: set | None = None,
    _context: tuple | None = None,
) -> list[ast.AST]:
    """Real (bounded) call-graph walk, fixing a review finding (2026-08-26,
    ret2-review) against the original Group C implementation: a single-hop
    ast.walk scoped to only the named route FunctionDef never descended
    into a thin wrapper's delegated-to helper (both
    ai_advisor_retirement_approve/_reject are one-line `return
    _dispatch_retirement_decision(...)` calls -- the ENTIRE route body,
    including the real candidate_id validation and the
    database.upsert_retirement_decision call, lives in that helper, which
    the original scan never touched).

    Returns [fn_node] PLUS every other module-level FunctionDef in app.py's
    own AST that fn_name's body transitively calls via a bare-Name Call --
    visited-set-guarded against infinite recursion on (mutual or self)
    recursion, so this remains a bounded, terminating walk regardless of
    app.py's real call shape. Only bare-Name calls that actually resolve to
    a real module-level FunctionDef in app.py are followed (a call to a
    builtin like `int(...)` or a Flask helper like `jsonify(...)` simply
    fails the `_find_function_def` lookup and is not recursed into -- correct,
    since those aren't local functions this scan needs to open up further).

    A name that is read and bound at module level by an import alias, an assignment or a table
    is followed too: the statement that binds it is added to the walk (and the names that
    statement reads are followed in turn), so ``from x import f as g`` then ``g()``, ``g = f``
    then ``g()``, and ``TABLE = {"k": f}`` then ``TABLE["k"]()`` reach what ``f`` is.
    """
    if _visited is None:
        _visited = set()
        _SCAN_TEXTS.clear()
    if _context is None:
        _context = (_function_defs_by_name(tree), _module_level_bindings(tree))
    if fn_name in _visited:
        return []
    _visited.add(fn_name)

    fn_node = _context[0].get(fn_name)
    if fn_node is None:
        return []
    return [fn_node, *_subtrees_reached_from(tree, fn_node, fn_name, _visited, _context)]


def _subtrees_reached_from(
    tree: ast.Module, root: ast.AST, own_name: str | None, visited: set, context: tuple
) -> list[ast.AST]:
    """What ``root`` reaches: the functions it calls by bare name, the module-level functions it
    hands over by reference, and the statements that bind a module-level name it reads."""
    defs, bindings = context
    module_level_functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    reached: list[ast.AST] = []
    for node in ast.walk(root):
        if isinstance(node, ast.Call):
            callees = []
            callee_name = _resolve_bare_name_callee(node)
            if callee_name and callee_name != own_name and callee_name in defs:
                reached.extend(
                    _collect_transitive_local_call_subtrees(
                        tree, callee_name, _visited=visited, _context=context
                    )
                )
        # A function handed over by reference is run by whoever receives it (a thread target, an
        # executor submit, a callback argument, a partial, a value in a table): follow a Name that
        # is read and names a module-level function of app.py, not only a call by bare name.
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            if node.id != own_name and node.id in module_level_functions:
                reached.extend(
                    _collect_transitive_local_call_subtrees(
                        tree, node.id, _visited=visited, _context=context
                    )
                )
            for statement in bindings.get(node.id, ()):
                key = ("binding", id(statement))
                if key in visited:
                    continue
                visited.add(key)
                reached.append(statement)
                reached.extend(_subtrees_reached_from(tree, statement, None, visited, context))
    return reached


def _unparse_without_docstrings(node: ast.AST) -> str:
    """Same rationale as this file's own _read_executable_source (see module
    docstring): a function's own docstring may legitimately DOCUMENT an
    exclusion in prose (e.g. "-- reaches no Composer/exec/LIVE_EXECUTION/
    trade primitive of any kind", which _dispatch_retirement_decision's real
    docstring says verbatim) without tripping a plain substring scan over
    its unparsed source. Deep-copies `node` first (never mutates the shared
    parsed tree other callers may still be walking), blanks out any
    docstring-shaped leading Expr(Constant(str)) statement from every
    Module/ClassDef/FunctionDef/AsyncFunctionDef body found anywhere inside
    it, then unparses. Falls back to "" on a Python without ast.unparse
    (matches this file's existing hasattr(ast, "unparse") guard elsewhere)."""
    if not hasattr(ast, "unparse"):
        return ""
    tree_copy = copy.deepcopy(node)
    for n in ast.walk(tree_copy):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(n, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                n.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree_copy)


# ===========================================================================
# Group A: source-scan parametrized over both new modules (mirrors 2a exactly)
# ===========================================================================


@pytest.mark.parametrize("module_rel_path", _NEW_MODULE_REL_PATHS)
class TestNoTradeSymbolsAcrossBothNewModules:
    def test_module_exposes_no_trade_shaped_public_symbols(self, module_rel_path):
        import importlib

        module_name = module_rel_path.replace("/", ".").removesuffix(".py")
        mod = importlib.import_module(module_name)

        public_names = [n for n in dir(mod) if not n.startswith("_")]
        offending = [
            n
            for n in public_names
            if any(word in n.lower().split("_") for word in _FORBIDDEN_ACTION_WORDS)
        ]
        assert offending == [], (
            f"{module_rel_path} exposes trade-shaped public symbols: {offending}"
        )

    def test_module_never_constructs_a_trade_url_fragment_in_executable_code(self, module_rel_path):
        source = _read_executable_source(module_rel_path)
        for fragment in _FORBIDDEN_URL_FRAGMENTS:
            assert fragment not in source, (
                f"{module_rel_path} contains the '{fragment}' fragment in EXECUTABLE "
                "code -- this module must never reference a trade/deploy endpoint."
            )

    def test_module_never_imports_alpha_bot_execution(self, module_rel_path):
        source = _read_source(module_rel_path)
        assert "alpha_bot_execution" not in source, (
            f"{module_rel_path} must never import alpha_bot_execution (the live "
            "execution engine) -- this is a purely advisory, off-execution-path module."
        )

    def test_module_never_calls_invest_in_symphony_or_composer_draft_client(self, module_rel_path):
        source = _read_executable_source(module_rel_path)
        assert "invest_in_symphony" not in source
        assert "composer_draft_client" not in source, (
            f"{module_rel_path} must never reference composer_draft_client (the only "
            "module in the repo permitted to create a real Composer symphony)."
        )

    def test_module_never_reads_or_sets_live_execution(self, module_rel_path):
        source = _read_executable_source(module_rel_path)
        assert "LIVE_EXECUTION" not in source

    def test_module_does_not_use_wildcard_imports(self, module_rel_path):
        source = _read_source(module_rel_path)
        assert "import *" not in source

    def test_module_never_overrides_is_advisory_only(self, module_rel_path):
        """Neither new module writes advisor_observations rows directly --
        both AC-1 (explainer, no DB write at all) and AC-6 (checklist, pure
        builder, no DB write at all) are read/compute-only. Mirrors the 2a
        scan's is_advisory_only guard."""
        source = _read_executable_source(module_rel_path)
        assert "is_advisory_only" not in source, (
            f"{module_rel_path} must not reference is_advisory_only -- neither the "
            "explainer nor the checklist builder writes advisor_observations rows "
            "directly (AC-1/AC-6: the producer persists explanation into raw_response, "
            "not this module)."
        )

    def test_module_exists(self, module_rel_path):
        path = REPO_ROOT / module_rel_path
        assert path.exists(), (
            f"{module_rel_path} does not exist -- the no-trade boundary is unverifiable"
        )


# ===========================================================================
# Group A non-vacuity: each check fails on a planted violation in a scratch copy
# ===========================================================================

_GROUP_A_PLANTS = {
    "composer draft save": (
        "composer_draft_client.save_symphony(tree)\n",
        "test_module_never_calls_invest_in_symphony_or_composer_draft_client",
    ),
    "invest call": (
        "invest_in_symphony(symphony_id, amount)\n",
        "test_module_never_calls_invest_in_symphony_or_composer_draft_client",
    ),
    "sell endpoint": (
        'ORDER_URL = BASE_URL + "/sell"\n',
        "test_module_never_constructs_a_trade_url_fragment_in_executable_code",
    ),
    "live execution read": (
        'LIVE = os.environ.get("LIVE_EXECUTION")\n',
        "test_module_never_reads_or_sets_live_execution",
    ),
    "engine import": (
        "import alpha_bot_execution\n",
        "test_module_never_imports_alpha_bot_execution",
    ),
}


class TestGroupAScanFailsOnAPlantedViolation:
    @staticmethod
    def _scratch_copy(tmp_path, monkeypatch, module_rel_path, plant):
        source = (REPO_ROOT / module_rel_path).read_text(encoding="utf-8")
        target = tmp_path / module_rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source + "\n" + plant, encoding="utf-8")
        monkeypatch.setattr(sys.modules[__name__], "REPO_ROOT", tmp_path)

    @pytest.mark.parametrize("module_rel_path", _NEW_MODULE_REL_PATHS)
    @pytest.mark.parametrize("label", list(_GROUP_A_PLANTS))
    def test_the_matching_check_fails_on_the_plant(
        self, tmp_path, monkeypatch, module_rel_path, label
    ):
        plant, check_name = _GROUP_A_PLANTS[label]
        self._scratch_copy(tmp_path, monkeypatch, module_rel_path, plant)
        check = getattr(TestNoTradeSymbolsAcrossBothNewModules(), check_name)

        with pytest.raises(AssertionError):
            check(module_rel_path)

    @pytest.mark.parametrize("module_rel_path", _NEW_MODULE_REL_PATHS)
    @pytest.mark.parametrize("check_name", sorted({c for _, c in _GROUP_A_PLANTS.values()}))
    def test_the_same_check_passes_on_an_unplanted_scratch_copy(
        self, tmp_path, monkeypatch, module_rel_path, check_name
    ):
        self._scratch_copy(tmp_path, monkeypatch, module_rel_path, "")

        getattr(TestNoTradeSymbolsAcrossBothNewModules(), check_name)(module_rel_path)

    def test_the_model_modules_are_in_the_scanned_set(self):
        assert {"model_config.py", "llm_request.py"} <= set(_NEW_MODULE_REL_PATHS)


# ===========================================================================
# Group B: retirement_checklist.py must NEVER reach the LLM seam at all
# ===========================================================================


class TestChecklistModuleNeverReachesLlm:
    """The operator ruling (Gate-2b): the checklist is a deterministic
    template, no LLM anywhere in its module. This is a stronger guarantee
    than "no trade primitive" -- the whole module must be LLM-free."""

    _MODULE_REL_PATH = "advisors/retirement_checklist.py"

    def test_module_does_not_import_ai_advisor(self):
        source = _read_source(self._MODULE_REL_PATH)
        assert "ai_advisor" not in source, (
            "retirement_checklist.py must never import ai_advisor (the LLM client "
            "seam lives there) -- the checklist is a deterministic template, no LLM."
        )

    def test_module_does_not_reference_anthropic(self):
        source = _read_source(self._MODULE_REL_PATH)
        assert "anthropic" not in source.lower(), (
            "retirement_checklist.py must never reference the anthropic SDK."
        )

    def test_module_does_not_reference_build_client_or_explainer_entrypoint(self):
        source = _read_source(self._MODULE_REL_PATH)
        assert _LLM_SEAM_ATTR not in source
        assert _EXPLAINER_ENTRYPOINT not in source

    def test_ast_call_graph_never_reaches_build_client(self):
        """Static (not runtime-mocked) proof: no Name/Attribute node anywhere
        in the module's AST resolves to the LLM seam. A runtime mock alone
        could miss a future refactor that reintroduces the call under a new
        code path this specific test's mock doesn't happen to cover; the AST
        walk is exhaustive over every function in the module regardless of
        which ones a behavioral test happens to exercise."""
        path = REPO_ROOT / self._MODULE_REL_PATH
        if not path.exists():
            pytest.fail(f"{self._MODULE_REL_PATH} does not exist")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not _references_name_or_attr(tree, _LLM_SEAM_ATTR), (
            "retirement_checklist.py's AST references the LLM client seam "
            f"({_LLM_SEAM_ATTR}) somewhere in the module -- the checklist builder "
            "must be entirely deterministic, no LLM call path of any kind."
        )


# ===========================================================================
# Group C: static AST call-graph proof for the two new Flask routes
# ===========================================================================


class TestApproveRejectRoutesNeverReachLlmOrComposerDraftClient:
    """Team-lead-flagged priority: a STATIC ast call-graph assertion, not
    just a runtime mock, proving neither new route handler's function body
    can reach ai_advisor._build_client, composer_draft_client, or
    alpha_bot_execution -- catches a future refactor a runtime-mock-only
    test could miss.

    [FIXED, review finding, 2026-08-26 (ret2-review)]: the original version
    of this class scoped its ast.walk to ONLY the named route FunctionDef
    (ast_advisor_retirement_approve/_reject). Both routes are thin one-line
    wrappers (`return _dispatch_retirement_decision(...)`) -- the ENTIRE
    real body (candidate_id validation, the database.upsert_retirement_
    decision call) lives in that shared helper, which the single-hop scan
    never touched. A future refactor adding an LLM-seam/composer_draft_
    client/alpha_bot_execution/LIVE_EXECUTION reference INSIDE
    _dispatch_retirement_decision (rather than directly in either named
    route function) would have sailed through every test in this class
    undetected -- exactly the "future refactor a runtime-mock-only test
    could miss" scenario this class's own docstring claims to guard
    against, except the AST scan itself didn't either, since it was a
    single-hop scan, not a real call-graph walk. Now uses
    _collect_transitive_local_call_subtrees (a bounded, visited-set-guarded
    walk following bare-Name calls to other app.py module-level functions)
    so an N-level-deep future wrapper chain is covered too, not just this
    specific 1-level case."""

    def _get_transitive_subtrees(self, fn_name: str) -> list[ast.AST]:
        tree = _parse_app_py()
        node = _find_function_def(tree, fn_name)
        if node is None:
            pytest.fail(
                f"app.py has no function named {fn_name!r} yet -- the retirement "
                "approve/reject route has not been added (pinned name per "
                ".claude/tdd-handoff.md)."
            )
        return _collect_transitive_local_call_subtrees(tree, fn_name)

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_transitive_walk_actually_descends_into_the_shared_dispatch_helper(self, fn_name):
        """Non-vacuity guard for the fix itself: prove the transitive walk
        is genuinely reaching beyond the single named route function --
        without this, a bug in _collect_transitive_local_call_subtrees
        (e.g. a resolution failure silently degrading back to [fn_node])
        would make every other test in this class pass for the WRONG
        reason again, identical to the exact defect class this fix exists
        to close."""
        subtrees = self._get_transitive_subtrees(fn_name)
        visited_names = {getattr(node, "name", None) for node in subtrees}
        assert "_dispatch_retirement_decision" in visited_names, (
            f"The transitive call-graph walk from {fn_name} did not reach "
            f"_dispatch_retirement_decision (only found {visited_names}) -- either "
            "the shared helper was renamed/removed, or the walk itself regressed "
            "back to a single-hop scan."
        )
        assert len(subtrees) >= 2, (
            f"Expected at least 2 subtrees ({fn_name} itself + the helper it "
            f"delegates to), got {len(subtrees)}."
        )

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_route_handler_never_references_llm_seam(self, fn_name):
        subtrees = self._get_transitive_subtrees(fn_name)
        for subtree in subtrees:
            assert not _references_name_or_attr(subtree, _LLM_SEAM_ATTR), (
                f"{fn_name}'s transitive call graph (via {getattr(subtree, 'name', '?')}) "
                f"references the LLM client seam ({_LLM_SEAM_ATTR}) -- approve/reject "
                "must be a deterministic status write only, never touching the LLM."
            )
            assert not _references_name_or_attr(subtree, _EXPLAINER_ENTRYPOINT), (
                f"{fn_name}'s transitive call graph (via {getattr(subtree, 'name', '?')}) "
                f"references {_EXPLAINER_ENTRYPOINT} -- the explainer must never run on "
                "the approve/reject action path (operator ruling, Gate-2b)."
            )

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_route_handler_never_references_composer_draft_client(self, fn_name):
        subtrees = self._get_transitive_subtrees(fn_name)
        for subtree in subtrees:
            source_segment = _unparse_without_docstrings(subtree)
            assert "composer_draft_client" not in source_segment, (
                f"{fn_name}'s transitive call graph (via {getattr(subtree, 'name', '?')}) "
                "references composer_draft_client -- retirement approve is a status-only "
                "DB write (AC-5), never a Composer symphony creation call (that is the "
                "frontrunner /proposal/approve route only)."
            )

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_route_handler_never_references_alpha_bot_execution(self, fn_name):
        subtrees = self._get_transitive_subtrees(fn_name)
        for subtree in subtrees:
            source_segment = _unparse_without_docstrings(subtree)
            assert "alpha_bot_execution" not in source_segment, (
                f"{fn_name}'s transitive call graph (via {getattr(subtree, 'name', '?')}) "
                "references alpha_bot_execution (the live execution engine)."
            )

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_route_handler_never_references_live_execution(self, fn_name):
        subtrees = self._get_transitive_subtrees(fn_name)
        for subtree in subtrees:
            source_segment = _unparse_without_docstrings(subtree)
            assert "LIVE_EXECUTION" not in source_segment, (
                f"{fn_name}'s transitive call graph (via {getattr(subtree, 'name', '?')}) "
                "reads/writes LIVE_EXECUTION."
            )


# ===========================================================================
# Group D: settings-write-allowlist exclusion for the two new routes
# ===========================================================================


class TestNewRoutesNotInSettingsWriteAllowlist:
    def test_retirement_approve_route_not_in_allowlist(self):
        import app as app_module

        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        if allowlist is None:
            pytest.skip("_SETTINGS_WRITE_ALLOWLIST not found on app module")
        assert _APPROVE_ROUTE_PATH not in allowlist

    def test_retirement_reject_route_not_in_allowlist(self):
        import app as app_module

        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        if allowlist is None:
            pytest.skip("_SETTINGS_WRITE_ALLOWLIST not found on app module")
        assert _REJECT_ROUTE_PATH not in allowlist

    def test_retirement_run_route_not_in_allowlist(self):
        """Cycle 2e AC-7: the Run-now route is advisory (re-runs the nightly
        read-only analysis); it must never become a settings-write path."""
        import app as app_module

        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        if allowlist is None:
            pytest.fail("_SETTINGS_WRITE_ALLOWLIST not found on app module")
        assert _RUN_ROUTE_PATH not in allowlist

    def test_allowlist_contains_no_retirement_or_live_execution_tokens(self):
        """Broader sweep (mirrors the frontrunner route test's own
        allowlist-forbidden-token set): neither route path fragment nor
        LIVE_EXECUTION may appear anywhere in the allowlist."""
        import app as app_module

        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        if allowlist is None:
            pytest.skip("_SETTINGS_WRITE_ALLOWLIST not found on app module")
        forbidden = {"retirement", "LIVE_EXECUTION", "candidate_id"}
        hit = forbidden & set(allowlist)
        assert not hit, (
            f"_SETTINGS_WRITE_ALLOWLIST must not contain retirement-related or "
            f"LIVE_EXECUTION keys. Found: {hit}"
        )


# ---------------------------------------------------------------------------
# The settings write allowlist stays exactly what it was: the tenure threshold is an operator
# setting in the environment file and is not writable from the dashboard, and neither the
# on-demand review route nor the review's API route is a settings write.
# ---------------------------------------------------------------------------

# The members at the base the review feature branched from: two engine settings and the
# eight tunable search-space keys. Written out here (not read from the module), so adding any
# key, the review's threshold included, fails this test and has to be decided on purpose.
_ALLOWLIST_MEMBERS_AT_THE_BASE = frozenset(
    {
        "EXECUTION_START_TIME",
        "EXIT_AUTHORITY",
        "MAX_PARABOLIC_SQUEEZE",
        "MAX_SQUEEZE_FLOOR",
        "PARABOLIC_VELOCITY_THRESHOLD",
        "TAKE_PROFIT_MC_PCT",
        "TRIGGER_THRESHOLD_PCT",
        "VWAP_BLEED_MULTIPLIER",
        "VWAP_BLEED_TICKS",
        "VWAP_CROSS_HWM_PCT",
    }
)


class TestTheSettingsWriteAllowlistIsUnchanged:
    def test_the_allowlist_holds_exactly_its_members_at_the_base(self):
        import app as app_module

        assert frozenset(app_module._SETTINGS_WRITE_ALLOWLIST) == _ALLOWLIST_MEMBERS_AT_THE_BASE

    @pytest.mark.parametrize(
        "key",
        [
            "TENURE_REVIEW_MIN_TRADING_DAYS",
            "ACCOUNT_INDIVIDUAL",
            "ACCOUNT_ROTH",
            "ACCOUNT_TRAD",
            "LIVE_EXECUTION",
        ],
    )
    def test_no_review_setting_or_account_key_is_dashboard_writable(self, key):
        import app as app_module

        assert key not in app_module._SETTINGS_WRITE_ALLOWLIST

    @pytest.mark.parametrize(
        "path", ["/ai-advisor/retirement/review/run", "/api/retirement-recommendations"]
    )
    def test_the_review_routes_are_not_settings_writes(self, path):
        import app as app_module

        assert path not in app_module._SETTINGS_WRITE_ALLOWLIST


# ===========================================================================
# Group E (Cycle 2c, DE-RETIRE-POLISH-001, ret3-review findings (b) and,
# later, (c)): static AST call-graph proof for the app.py HELPER call paths
# this cycle added -- the nightly tick-worker producer orchestration
# (AC-2/AC-4), its reuse-decision helper (AC-4), the approval-status
# live-join helper (AC-6), and (finding (c), added after the PR#140 review
# round that shipped findings 1/3) the shared fetch-time helper that resolves
# fresh display names for BOTH the API and the panel. ai_advisor_tab()'s own
# retirement name-resolution (AC-1) is covered SEPARATELY below (TestAiAdvisorTabRetirementBlockNeverReachesExecSeam)
# via a scoped source-window scan, NOT this transitive-walk pattern -- see
# that class's docstring for why (empirically: a full-function transitive
# walk of ai_advisor_tab() produces a FALSE POSITIVE via _build_meta, an
# unrelated shared page-header helper that legitimately reads LIVE_EXECUTION
# for a read-only "live mode" display badge, nothing to do with retirement
# at all -- team-lead's own conditional guidance ("include it as a 4th
# entry point IF the scan cleanly handles the breadth ... otherwise
# document...") anticipated exactly this, and the scoped-window approach
# closes the gap STRUCTURALLY rather than falling back to prose-only
# documentation).

# Deliberately does NOT check the LLM seam (_LLM_SEAM_ATTR/_EXPLAINER_
# ENTRYPOINT) for these 3 entry points -- unlike Group C's approve/reject
# routes, _retirement_recommender_tick_worker is EXPECTED and SUPPOSED to
# reach advisors.retirement_explainer.explain_recommendation (that is
# AC-2/AC-4's whole job, the nightly producer, not an operator action) --
# asserting its absence here would be checking the wrong property and could
# mislead a future reader about what this function is guarding. The
# Gate-2b operator ruling ("LLM stays out of the approve/reject/checklist
# action path") is about the ACTION path, already covered by Group C; this
# group covers the EXEC/TRADE boundary only, exactly as team-lead specified:
# "no save_symphony/composer_draft_client/alpha_bot_execution/LIVE_EXECUTION".

_RETIREMENT_TICK_WORKER_FN_NAME = "_retirement_recommender_tick_worker"
_RETIREMENT_REUSE_HELPER_FN_NAME = "_retirement_find_reusable_prior_explanation"
_RETIREMENT_APPROVAL_JOIN_FN_NAME = "_join_retirement_approval_status"

# PR#140 /code-review re-review finding (c) (2026-08-27, ret3-review):
# finding 3's refactor moved AC-1's name-resolution OUT of ai_advisor_tab()'s
# inline block (where Group F structurally covered it) into a new module-
# level function, _refresh_retirement_display_names, called from the shared
# _fetch_retirement_recommendations() (consumed by BOTH the API route and
# the panel). Neither was in Group E's entry-point list nor Group F's source
# window -- ret3-review PROVED the gap by injecting save_symphony(recs) into
# _refresh_retirement_display_names's body and confirming this file's suite
# still passed 57/57 (uncaught), then reverting.
#
# Fix: cover the PARENT _fetch_retirement_recommendations, not just the leaf
# _refresh_retirement_display_names -- empirically verified first (matching
# this file's own established practice, see Group F's docstring on why
# ai_advisor_tab() itself was rejected): the transitive closure from
# _fetch_retirement_recommendations is a tight 3 nodes (itself +
# _join_retirement_approval_status + _refresh_retirement_display_names, both
# reached via bare-Name calls in its own body) with zero forbidden-token
# hits -- no _build_meta-style false-positive risk. This is also the
# team-lead-preferred choice for a reason beyond just closing today's gap:
# it future-proofs against the NEXT refactor that adds a function to this
# shared fetch-time path (this gap class has now recurred 3 times as the
# code evolved -- covering the stable parent closes the whole class, not
# just today's instance).
_RETIREMENT_FETCH_FN_NAME = "_fetch_retirement_recommendations"

# Cycle 2e (DE-RETIRE-VISIBILITY-001, AC-8) additions: the Run-now route
# handler (starts the recommender worker on a daemon thread) and the last-run read
# helper (feeds the API + panel). The scheduler-facing tick wrapper is
# _RETIREMENT_TICK_FN_NAME below (one function, one name); the Cycle 2e spelling is kept as
# an alias so the Group H/I scans read as they did.
_RETIREMENT_RUN_ROUTE_FN_NAME = "ai_advisor_retirement_run"
_RETIREMENT_LAST_RUN_FETCH_FN_NAME = "_fetch_retirement_last_run"

# Symphony tenure review read surfaces (the review round of cycle C3): the shared
# fetch the panel and the API both call, and the API route that returns its answer.
# The decisions join they share is _RETIREMENT_APPROVAL_JOIN_FN_NAME above, so it is
# reached from both and covered once.
_TENURE_REVIEW_FETCH_FN_NAME = "_fetch_tenure_review"
_RETIREMENT_API_ROUTE_FN_NAME = "api_retirement_recommendations"

# Symphony tenure review run path: the job both run entry points share, the scheduler-facing
# tick that starts it, the route that submits it, and the step that prepares the night's
# recommendations for storage (moved out of the tick worker). The route submits the job as a
# function reference and the tick hands it to a thread the same way, so no transitive walk
# from either reaches it: each is an entry point of its own.
_TENURE_REVIEW_JOB_FN_NAME = "_tenure_review_job"
_RETIREMENT_TICK_FN_NAME = "_run_retirement_recommender_tick"
_RETIREMENT_TICK_WRAPPER_FN_NAME = _RETIREMENT_TICK_FN_NAME  # the Cycle 2e spelling
_TENURE_REVIEW_RUN_ROUTE_FN_NAME = "ai_advisor_retirement_review_run"
_RETIREMENT_PREPARE_FN_NAME = "_prepare_retirement_recs_for_persist"
# The thread body of the nightly tick: the review job, then the recommender run.
_RETIREMENT_NIGHTLY_BODY_FN_NAME = "_retirement_nightly_tick_body"
_TENURE_RUN_PATH_FN_NAMES = [
    _TENURE_REVIEW_JOB_FN_NAME,
    _RETIREMENT_TICK_FN_NAME,
    _TENURE_REVIEW_RUN_ROUTE_FN_NAME,
    _RETIREMENT_PREPARE_FN_NAME,
    _RETIREMENT_NIGHTLY_BODY_FN_NAME,
]

# The helpers the panel template calls while it renders: the Eastern-time formatter, the
# four text and number filters, and the two Jinja tests. They run on every AI Advisor page
# load and are reached from the template only, so no entry point's call graph includes
# them; each is an entry point of its own.
_TENURE_RENDER_HELPER_FN_NAMES = [
    "_format_eastern_run_time",
    "_tenure_text",
    "_tenure_number",
    "_tenure_whole",
    "_tenure_eastern",
    "_is_finite_number",
    "_is_whole_number",
    "_tenure_redundancy_missing",
]

# The Retirement tab's render helpers: the summary tiles, the Overview pointer and the
# exclusion-reason sentence, and the two text filters the tab and the observations section print
# through (a stored verdict or subject as words; a run's basis line). Each runs on every AI
# Advisor page load, is reached from ai_advisor_tab() or from the template alone, and none may
# reach a trade, a draft or a live-execution primitive.
_RETIREMENT_TILES_HELPER_FN_NAME = "_build_retirement_summary_tiles"
_RETIREMENT_POINTER_HELPER_FN_NAME = "_build_retirement_pointer"
_RETIREMENT_EXCLUSION_SENTENCE_FN_NAME = "_retirement_exclusion_reason_sentence"
_ADVISOR_WORDS_FILTER_FN_NAME = "advisor_words"
_RETIREMENT_BASIS_FILTER_FN_NAME = "retirement_basis"
_RETIREMENT_TAB_HELPER_FN_NAMES = [
    _RETIREMENT_TILES_HELPER_FN_NAME,
    _RETIREMENT_POINTER_HELPER_FN_NAME,
    _RETIREMENT_EXCLUSION_SENTENCE_FN_NAME,
    _ADVISOR_WORDS_FILTER_FN_NAME,
    _RETIREMENT_BASIS_FILTER_FN_NAME,
]

_CYCLE_2C_ENTRY_POINT_FN_NAMES = [
    _RETIREMENT_TICK_WORKER_FN_NAME,
    _RETIREMENT_REUSE_HELPER_FN_NAME,
    _RETIREMENT_APPROVAL_JOIN_FN_NAME,
    _RETIREMENT_FETCH_FN_NAME,
    # Cycle 2e:
    _RETIREMENT_RUN_ROUTE_FN_NAME,
    _RETIREMENT_LAST_RUN_FETCH_FN_NAME,
    # The Retirement tab:
    *_RETIREMENT_TAB_HELPER_FN_NAMES,
    # Symphony tenure review:
    _TENURE_REVIEW_FETCH_FN_NAME,
    _RETIREMENT_API_ROUTE_FN_NAME,
    *_TENURE_RUN_PATH_FN_NAMES,
    *_TENURE_RENDER_HELPER_FN_NAMES,
]


# Every spelling of "make this function a template helper" that Flask, Blueprints and Jinja
# accept. A helper registered by a form this guard does not read is reached by the template
# and by no scan entry point, so the guard is only as strong as the forms listed here.
_TEMPLATE_REGISTRATION_DECORATORS = frozenset(
    {
        "template_filter",
        "template_test",
        "template_global",
        "context_processor",
        "app_template_filter",
        "app_template_test",
        "app_template_global",
        "app_context_processor",
    }
)
_CONTEXT_PROCESSOR_ATTRS = frozenset({"context_processor", "app_context_processor"})
_TEMPLATE_REGISTRATION_TABLES = frozenset({"filters", "tests", "globals"})
# The list of context processors an application keeps; a function stored in it runs per render.
_TEMPLATE_PROCESSOR_LIST_ATTR = "template_context_processors"
# Calls that register their first argument, or their ``f`` keyword, as a template helper.
_TEMPLATE_REGISTRATION_CALLS = frozenset(
    {
        "add_template_filter",
        "add_template_test",
        "add_template_global",
        "add_app_template_filter",
        "add_app_template_test",
        "add_app_template_global",
        *_CONTEXT_PROCESSOR_ATTRS,
    }
)
# Methods of a filters, tests or globals table, or of the processor list, that store the
# values they are given.
_TEMPLATE_TABLE_STORING_METHODS = frozenset(
    {"update", "setdefault", "__setitem__", "__ior__", "append", "extend", "insert"}
)
# What a registered helper that has no name (a lambda) is reported as: it cannot be an entry
# point of the scan, so it is always unlisted.
_ANONYMOUS_HELPER = "<anonymous helper>"
# Builtins that only collect or pair the helpers they are given.
_COLLECTING_BUILTINS = frozenset({"dict", "list", "tuple", "set", "frozenset", "zip", "map"})


def _helper_names_in(node: ast.AST | None) -> set[str]:
    """The names of the functions a registration hands over, whatever shape it hands them in:
    a bare name, a lambda, the values of a dict literal or comprehension, the elements of a
    list, tuple, set, generator or comprehension (the key of a ``(key, function)`` pair is
    not a helper), either side of ``|`` or ``+``, a conditional's two branches, and the
    arguments of a call that wraps or collects them (``dict(...)``, ``zip(...)``,
    ``functools.partial(...)``)."""
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        # The table itself, as the left side of a union, is not a helper.
        return set() if node.id in _TEMPLATE_REGISTRATION_TABLES else {node.id}
    if isinstance(node, ast.Lambda):
        return {_ANONYMOUS_HELPER}
    if isinstance(node, ast.Starred):
        return _helper_names_in(node.value)
    if isinstance(node, ast.Dict):
        return {name for value in node.values for name in _helper_names_in(value)}
    if isinstance(node, ast.DictComp):
        return _helper_names_in(node.value)
    if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
        return _helper_names_in(node.elt)
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        names: set[str] = set()
        for index, element in enumerate(node.elts):
            is_key = (
                index == 0
                and isinstance(node, ast.Tuple)
                and isinstance(element, ast.Constant)
                and isinstance(element.value, str)
            )
            if not is_key:
                names |= _helper_names_in(element)
        return names
    if isinstance(node, ast.BinOp):
        return _helper_names_in(node.left) | _helper_names_in(node.right)
    if isinstance(node, ast.IfExp):
        return _helper_names_in(node.body) | _helper_names_in(node.orelse)
    if isinstance(node, ast.Call):
        names = set()
        if isinstance(node.func, ast.Name) and node.func.id not in _COLLECTING_BUILTINS:
            names.add(node.func.id)
        for argument in [*node.args, *(k.value for k in node.keywords)]:
            names |= _helper_names_in(argument)
        return names
    return set()


def _strip_subscripts(node: ast.AST) -> ast.AST:
    while isinstance(node, ast.Subscript):
        node = node.value
    return node


def _is_template_table(node: ast.AST) -> bool:
    """A ``filters``, ``tests`` or ``globals`` table, or the application's list of context
    processors, however it is reached and whether or not it is indexed."""
    node = _strip_subscripts(node)
    if isinstance(node, ast.Attribute):
        return (
            node.attr in _TEMPLATE_REGISTRATION_TABLES or node.attr == _TEMPLATE_PROCESSOR_LIST_ATTR
        )
    return isinstance(node, ast.Name) and node.id in _TEMPLATE_REGISTRATION_TABLES


def _called_attribute(call: ast.Call) -> str | None:
    """The attribute a call is made through: ``app.template_filter("x")`` is made through
    ``template_filter``."""
    return call.func.attr if isinstance(call.func, ast.Attribute) else None


def _is_a_mapping_of_names(node: ast.AST | None) -> bool:
    """Whether a context processor's return value is a dict literal or ``dict(...)``, the
    only shapes that hand the template names; any other return value is data."""
    return isinstance(node, ast.Dict) or (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict"
    )


def _registered_template_function_names(tree: ast.Module) -> set[str]:
    """The names of the functions a module registers for its templates, by every form Flask,
    Blueprints and Jinja accept: a ``template_filter``, ``template_test``, ``template_global``
    or ``context_processor`` decorator (and the Blueprint ``app_`` spellings), applied by
    ``@`` or by a call (``app.template_filter("x")(fn)``); a call of ``add_template_filter``,
    ``add_template_test``, ``add_template_global`` or ``context_processor`` (and the
    Blueprint spellings) with the function; a store into a ``filters``, ``tests`` or
    ``globals`` table by subscript, by ``.update(...)`` (a dict, keywords, a list or generator
    of pairs), ``.setdefault(...)``, ``|=``, or a replacement of the whole table; an append
    to the context processor list; and the names a context processor returns in a dict. The
    template runs each of them on a page load, from outside any call graph."""
    names: set[str] = set()
    processors: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                attribute = (
                    target.attr
                    if isinstance(target, ast.Attribute)
                    else target.id
                    if isinstance(target, ast.Name)
                    else None
                )
                if attribute not in _TEMPLATE_REGISTRATION_DECORATORS:
                    continue
                names.add(node.name)
                if attribute in _CONTEXT_PROCESSOR_ATTRS:
                    processors.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                # Binding a local that happens to be called ``filters`` registers nothing;
                # replacing the table on an object, or storing into it by index, does.
                if _is_template_table(target) and not isinstance(target, ast.Name):
                    names |= _helper_names_in(node.value)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            if _is_template_table(node.target):
                names |= _helper_names_in(node.value)
        elif isinstance(node, ast.Call):
            attribute = _called_attribute(node)
            if isinstance(node.func, ast.Call):
                # app.template_filter("x")(fn): the decorator is applied by a call.
                applied = _called_attribute(node.func)
                if applied in _TEMPLATE_REGISTRATION_DECORATORS:
                    handed = _helper_names_in(node.args[0]) if node.args else set()
                    names |= handed
                    if applied in _CONTEXT_PROCESSOR_ATTRS:
                        processors |= handed
            elif attribute in _TEMPLATE_REGISTRATION_CALLS:
                handed = set()
                for value in [*node.args[:1], *(k.value for k in node.keywords if k.arg == "f")]:
                    handed |= _helper_names_in(value)
                names |= handed
                if attribute in _CONTEXT_PROCESSOR_ATTRS:
                    processors |= handed
            elif (
                attribute in _TEMPLATE_TABLE_STORING_METHODS
                and isinstance(node.func, ast.Attribute)
                and _is_template_table(node.func.value)
            ):
                handed = set()
                for value in [*node.args, *(k.value for k in node.keywords)]:
                    handed |= _helper_names_in(value)
                names |= handed
                receiver = _strip_subscripts(node.func.value)
                if (
                    isinstance(receiver, ast.Attribute)
                    and receiver.attr == _TEMPLATE_PROCESSOR_LIST_ATTR
                ):
                    processors |= handed
    # A context processor hands its template the names it returns.
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in processors:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Return) and _is_a_mapping_of_names(inner.value):
                    names |= _helper_names_in(inner.value)
    return names


# Team-lead named this explicitly alongside composer_draft_client -- checked
# via _references_name_or_attr (Name/Attribute AST check) rather than a
# substring, since a direct `from advisors.composer_draft_client import
# save_symphony` + bare `save_symphony(...)` call would slip past a pure
# "composer_draft_client" substring check on the unparsed source; the
# Name/Attribute check catches both that shape AND the attribute-chain
# shape (`composer_draft_client.save_symphony(...)`, already also caught by
# the substring check below -- deliberate overlap, defense-in-depth).
_SAVE_SYMPHONY_ATTR = "save_symphony"


# The text of each subtree of the scan in progress, with the node itself held so its identity
# cannot be reused while the text is kept. The collector empties it at the start of every walk,
# and nothing changes a node between a walk and the predicates that read its result.
_SCAN_TEXTS: dict[int, tuple[ast.AST, str]] = {}


def _text_of(node: ast.AST) -> str:
    """``_unparse_without_docstrings`` of a subtree of the current scan, computed once."""
    held = _SCAN_TEXTS.get(id(node))
    if held is None:
        held = _SCAN_TEXTS[id(node)] = (node, _unparse_without_docstrings(node))
    return held[1]


def _hits_composer_draft_client(subtrees) -> list[str]:
    return [
        f"composer_draft_client via {getattr(t, 'name', '?')}"
        for t in subtrees
        if "composer_draft_client" in _text_of(t)
    ]


def _hits_save_symphony(subtrees) -> list[str]:
    return [
        f"{_SAVE_SYMPHONY_ATTR} via {getattr(t, 'name', '?')}"
        for t in subtrees
        if _references_name_or_attr(t, _SAVE_SYMPHONY_ATTR)
    ]


def _hits_alpha_bot_execution(subtrees) -> list[str]:
    return [
        f"alpha_bot_execution via {getattr(t, 'name', '?')}"
        for t in subtrees
        if "alpha_bot_execution" in _text_of(t)
    ]


def _hits_live_execution(subtrees) -> list[str]:
    return [
        f"LIVE_EXECUTION via {getattr(t, 'name', '?')}"
        for t in subtrees
        if "LIVE_EXECUTION" in _text_of(t)
    ]


# Functions this feature must never reach, besides a Composer symphony creation: the engine's
# per-account statistics reader (it answers an empty list for every failure, so it cannot tell a
# failure from an empty account), the panic-sell function and the go-to-cash sell function,
# and the go-to-cash URL fragment.
_FORBIDDEN_ENGINE_NAMES = (
    "fetch_symphony_stats",
    "perform_account_liquidation",
    "execute_sell_to_cash",
    # The codebase's own ways to create or move money, besides a bare save_symphony: the
    # frontrunner approval (the one route that creates a Composer symphony) and the function it
    # calls, the invest call, and the panic-sell route (it reads LIVE_EXECUTION and starts the
    # liquidation thread).
    "approve_frontrunner_proposal",
    "ai_advisor_proposal_approve",
    "invest_in_symphony",
    "sell_account",
    # The two other ways the application changes what is live: the per-symphony live-mode write
    # (the one place a symphony is switched to trade for real, besides the global flag) and the
    # settings route that calls it, and every order the Alpaca order module can submit (the
    # managed sleeves' module is the one approved order path, with its own arming ladder, and a
    # review path never touches it). Reached by any path: a call, a lazy import, an alias.
    "set_symphony_live_mode",
    "save_symphony_settings",
    "submit_order",
    "submit_bracket_order",
    "submit_trailing_stop_order",
    "alpaca_orders",
)
_GO_TO_CASH_FRAGMENT = "/go-to-cash"
# Every Composer write endpoint a review path must never name: deploy, invest, rebalance, sell,
# liquidate and go to cash.
_RUN_PATH_URL_FRAGMENTS = (*_FORBIDDEN_URL_FRAGMENTS, "/rebalance", _GO_TO_CASH_FRAGMENT)


def _hits_forbidden_engine_names(subtrees) -> list[str]:
    hits = []
    for t in subtrees:
        text = _text_of(t)
        where = getattr(t, "name", "?")
        for name in _FORBIDDEN_ENGINE_NAMES:
            if name in text or _references_name_or_attr(t, name):
                hits.append(f"{name} via {where}")
        for fragment in _RUN_PATH_URL_FRAGMENTS:
            if fragment in text:
                hits.append(f"{fragment} via {where}")
    return hits


# A review path reads Composer and never writes to it. Every method a Composer write goes through
# is named like an HTTP write, on whatever object makes the request (the requests module, a
# session, a client), so the scan reports the name wherever it appears on the path: called, handed
# over as a thread target or a partial, looked up by a string, or imported under another name. A
# raw write to ``{COMPOSER_BASE_URL}/symphonies`` is then reported whatever its address spells.
_HTTP_WRITE_NAMES = frozenset({"post", "put", "patch", "delete", "request"})
# The libraries a name of that kind can be imported from; ``request`` is also the name of the web
# framework's request object, so a bare import of it counts only from one of these.
_HTTP_CLIENT_MODULES = frozenset({"requests", "httpx", "urllib3", "aiohttp", "http", "urllib"})


def _http_write_reference(node: ast.AST) -> str | None:
    """The write method a node names, when it names one: an attribute of that name, a bare call of
    it, a ``getattr`` with it as a string, or an import of it."""
    if isinstance(node, ast.Attribute) and node.attr in _HTTP_WRITE_NAMES:
        return node.attr
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _HTTP_WRITE_NAMES
    ):
        return node.func.id
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "getattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in _HTTP_WRITE_NAMES
    ):
        return node.args[1].value
    if isinstance(node, ast.ImportFrom):
        from_client = (node.module or "").split(".")[0] in _HTTP_CLIENT_MODULES
        for alias in node.names:
            if alias.name in _HTTP_WRITE_NAMES - {"request"} or (
                from_client and alias.name in _HTTP_WRITE_NAMES
            ):
                return alias.name
    return None


def _hits_http_writes(subtrees) -> list[str]:
    hits = []
    for t in subtrees:
        where = getattr(t, "name", "?")
        for node in ast.walk(t):
            name = _http_write_reference(node)
            if name is not None:
                hits.append(f"HTTP write {name} via {where}")
    return hits


def _exec_seam_hits(tree: ast.Module, fn_name: str) -> list[str]:
    """Every forbidden reference in the transitive call graph of ``fn_name``, by the same
    predicates the entry point tests below apply one at a time."""
    subtrees = _collect_transitive_local_call_subtrees(tree, fn_name)
    return (
        _hits_composer_draft_client(subtrees)
        + _hits_save_symphony(subtrees)
        + _hits_alpha_bot_execution(subtrees)
        + _hits_live_execution(subtrees)
        + _hits_forbidden_engine_names(subtrees)
        + _hits_http_writes(subtrees)
    )


class TestCycle2cCallPathsNeverReachExecSeam:
    """GREEN-ON-ARRIVAL (ret3-review confirmed zero exec seam in all 4 by
    hand-reading the diff before this test existed) -- a durable structural
    regression guard against a FUTURE change adding an exec call to any of
    these paths, not a RED-then-GREEN fix for a defect that exists today.
    Mirrors Group C's transitive-walk pattern and helpers exactly (same
    _collect_transitive_local_call_subtrees/_references_name_or_attr/
    _unparse_without_docstrings -- no new helper machinery needed)."""

    def _get_transitive_subtrees(self, fn_name: str) -> list[ast.AST]:
        tree = _parse_app_py()
        node = _find_function_def(tree, fn_name)
        if node is None:
            pytest.fail(
                f"app.py has no function named {fn_name!r} -- has this Cycle 2c "
                "call path been renamed/removed? (DE-RETIRE-POLISH-001)"
            )
        return _collect_transitive_local_call_subtrees(tree, fn_name)

    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_entry_point_exists(self, fn_name):
        tree = _parse_app_py()
        assert _find_function_def(tree, fn_name) is not None, (
            f"app.py has no function named {fn_name!r} -- this Cycle 2c safety "
            "boundary is unverifiable without it."
        )

    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_transitive_walk_is_non_vacuous(self, fn_name):
        """Non-vacuity guard (team-lead's explicit requirement): the scan
        must ACTUALLY traverse the named function, not silently no-op from a
        lookup failure -- mirrors Group C's own
        test_transitive_walk_actually_descends_into_the_shared_dispatch_helper
        in spirit, adapted since these 4 entry points don't all delegate to
        a single named shared helper the way the approve/reject routes do."""
        subtrees = self._get_transitive_subtrees(fn_name)
        assert len(subtrees) >= 1, (
            f"_collect_transitive_local_call_subtrees({fn_name!r}) returned an "
            "empty walk -- the scan did not actually traverse anything."
        )
        visited_names = {getattr(node, "name", None) for node in subtrees}
        assert fn_name in visited_names, (
            f"The transitive walk for {fn_name!r} did not even include itself -- "
            f"got {visited_names}."
        )

    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_entry_point_never_references_composer_draft_client(self, fn_name):
        hits = _hits_composer_draft_client(self._get_transitive_subtrees(fn_name))
        assert hits == [], (
            f"{fn_name}'s transitive call graph references composer_draft_client ({hits}) "
            "-- no Cycle 2c retirement call path may create a real Composer symphony "
            "(that remains the frontrunner /proposal/approve route's exclusive province)."
        )

    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_entry_point_never_references_save_symphony(self, fn_name):
        hits = _hits_save_symphony(self._get_transitive_subtrees(fn_name))
        assert hits == [], (
            f"{fn_name}'s transitive call graph references {_SAVE_SYMPHONY_ATTR} ({hits}) "
            "-- no Cycle 2c retirement call path may create a real Composer symphony."
        )

    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_entry_point_never_references_alpha_bot_execution(self, fn_name):
        hits = _hits_alpha_bot_execution(self._get_transitive_subtrees(fn_name))
        assert hits == [], (
            f"{fn_name}'s transitive call graph references alpha_bot_execution "
            f"(the live execution engine): {hits}"
        )

    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_entry_point_never_references_live_execution(self, fn_name):
        hits = _hits_live_execution(self._get_transitive_subtrees(fn_name))
        assert hits == [], f"{fn_name}'s transitive call graph reads/writes LIVE_EXECUTION: {hits}"

    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_entry_point_never_reaches_the_lossy_state_reader_or_a_money_moving_function(
        self, fn_name
    ):
        hits = _hits_forbidden_engine_names(self._get_transitive_subtrees(fn_name))
        assert hits == [], (
            f"{fn_name}'s transitive call graph reaches an engine or dashboard function the "
            f"review path must never use, or a go-to-cash URL: {hits}"
        )

    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_entry_point_never_makes_an_http_write(self, fn_name):
        hits = _hits_http_writes(self._get_transitive_subtrees(fn_name))
        assert hits == [], (
            f"{fn_name}'s transitive call graph names an HTTP write method ({hits}): a review "
            "path reads Composer and never writes to it, whatever address it writes to."
        )


# ---------------------------------------------------------------------------
# Mutation proof for Group E: a scan that cannot fail proves nothing. Each test plants
# a Composer symphony creation call into one entry point of the real app.py syntax tree
# (in memory, the file is untouched) and shows the scan reports it; the same scan over
# the unplanted tree reports nothing, so the report is the plant's doing.
# ---------------------------------------------------------------------------

_PLANTED_CALLS = {
    "attribute call": "composer_draft_client.save_symphony(planted_argument)",
    "bare call": "save_symphony(planted_argument)",
}


def _tree_with_planted_call(fn_name: str, statement: str) -> ast.Module:
    tree = _parse_app_py()
    node = _find_function_def(tree, fn_name)
    if node is None:
        pytest.fail(f"app.py has no function named {fn_name!r}")
    node.body.extend(ast.parse(statement).body)
    return tree


class TestGroupEScanReportsAPlantedSymphonyCreation:
    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_the_unplanted_tree_is_clean_for_every_entry_point(self, fn_name):
        assert _exec_seam_hits(_parse_app_py(), fn_name) == []

    @pytest.mark.parametrize("form", list(_PLANTED_CALLS))
    @pytest.mark.parametrize("fn_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_a_creation_call_planted_in_an_entry_point_is_reported(self, fn_name, form):
        tree = _tree_with_planted_call(fn_name, _PLANTED_CALLS[form])
        hits = _exec_seam_hits(tree, fn_name)
        assert any(_SAVE_SYMPHONY_ATTR in hit for hit in hits), (
            f"a {form} planted in {fn_name} was not reported; hits: {hits}"
        )

    @pytest.mark.parametrize(
        "planted_in, entry_point",
        [
            (_TENURE_REVIEW_FETCH_FN_NAME, _RETIREMENT_API_ROUTE_FN_NAME),
            (_RETIREMENT_APPROVAL_JOIN_FN_NAME, _TENURE_REVIEW_FETCH_FN_NAME),
            (_RETIREMENT_APPROVAL_JOIN_FN_NAME, _RETIREMENT_API_ROUTE_FN_NAME),
        ],
        ids=[
            "fetch reached from the route",
            "decisions join reached from the review fetch",
            "decisions join reached from the route",
        ],
    )
    def test_a_creation_call_planted_in_a_callee_is_reported_from_the_entry_point(
        self, planted_in, entry_point
    ):
        tree = _tree_with_planted_call(planted_in, _PLANTED_CALLS["attribute call"])
        hits = _exec_seam_hits(tree, entry_point)
        assert any(_SAVE_SYMPHONY_ATTR in hit for hit in hits), (
            f"{planted_in} is not on the call graph of {entry_point}; hits: {hits}"
        )


_PLANTED_ENGINE_CALLS = {
    "stats reader": ("fetch_symphony_stats", "fetch_symphony_stats(planted_argument)"),
    "stats reader through the engine": (
        "fetch_symphony_stats",
        "alpha_bot_execution.fetch_symphony_stats(planted_argument)",
    ),
    "panic sell": ("perform_account_liquidation", "perform_account_liquidation(planted_argument)"),
    "go to cash sell": ("execute_sell_to_cash", "execute_sell_to_cash(planted_argument)"),
    "go to cash url": (_GO_TO_CASH_FRAGMENT, 'requests.post(planted_base + "/go-to-cash")'),
    "frontrunner approval": (
        "approve_frontrunner_proposal",
        "frontrunner_builder.approve_frontrunner_proposal(planted_argument)",
    ),
    "frontrunner approval by bare name": (
        "approve_frontrunner_proposal",
        "approve_frontrunner_proposal(planted_argument)",
    ),
    "frontrunner approval route": ("ai_advisor_proposal_approve", "ai_advisor_proposal_approve()"),
    "invest call": ("invest_in_symphony", "invest_in_symphony(planted_argument)"),
    "panic sell route": ("sell_account", "sell_account()"),
    "rebalance url": (
        "/rebalance",
        'requests.post(f"{COMPOSER_BASE_URL}/deploy/accounts/{planted_account}'
        '/symphonies/{planted_symphony}/rebalance")',
    ),
    "deploy url": (
        "/deploy/",
        'requests.post(f"{COMPOSER_BASE_URL}/deploy/accounts/{planted_account}")',
    ),
    "invest url": ("/invest", 'requests.post(planted_base + "/invest")'),
    "sell url": ("/sell", 'requests.post(planted_base + "/sell")'),
    "liquidate url": ("/liquidate", 'requests.post(planted_base + "/liquidate")'),
    # A raw write to the Composer symphonies address (the create call without the draft client,
    # and the update and delete that go with it), in every spelling the requests library has.
    "raw create post": (
        "HTTP write post",
        'requests.post(f"{COMPOSER_BASE_URL}/symphonies", json=planted_body)',
    ),
    "raw update put": (
        "HTTP write put",
        'requests.put(f"{COMPOSER_BASE_URL}/symphonies/{planted_id}", json=planted_body)',
    ),
    "raw update patch": (
        "HTTP write patch",
        'requests.patch(f"{COMPOSER_BASE_URL}/symphonies/{planted_id}", json=planted_body)',
    ),
    "raw delete": (
        "HTTP write delete",
        'requests.delete(f"{COMPOSER_BASE_URL}/symphonies/{planted_id}")',
    ),
    "generic request": (
        "HTTP write request",
        'requests.request("POST", planted_url, json=planted_body)',
    ),
    "session post": ("HTTP write post", "planted_session.post(planted_url, json=planted_body)"),
    "session built on the spot": ("HTTP write post", "requests.Session().post(planted_url)"),
    "post as a thread target": (
        "HTTP write post",
        "threading.Thread(target=requests.post, args=(planted_url,)).start()",
    ),
    "post in a partial": ("HTTP write post", "functools.partial(requests.post, planted_url)()"),
    "post looked up by name": ("HTTP write post", 'getattr(requests, "post")(planted_url)'),
    "bare post": ("HTTP write post", "post(planted_url)"),
    "post imported under another name": (
        "HTTP write post",
        "from requests import post as planted_post\nplanted_post(planted_url)",
    ),
    # The other two ways the application changes what is live: a symphony's own live mode and
    # an Alpaca order, each by a call, by name, by reference and through a lazy import.
    "live-mode write": (
        "set_symphony_live_mode",
        "database.set_symphony_live_mode(planted_name, 1, 'tenure')",
    ),
    "live-mode write by bare name": (
        "set_symphony_live_mode",
        "set_symphony_live_mode(planted_name, 1, 'tenure')",
    ),
    "live-mode write by reference": (
        "set_symphony_live_mode",
        "planted_executor.submit(database.set_symphony_live_mode, planted_name, 1, 'tenure')",
    ),
    "live-mode write imported lazily": (
        "set_symphony_live_mode",
        "from database import set_symphony_live_mode as planted_set\n"
        "planted_set(planted_name, 1, 'tenure')",
    ),
    "settings route": ("save_symphony_settings", "save_symphony_settings(planted_name)"),
    "settings route by reference": (
        "save_symphony_settings",
        "planted_executor.submit(save_symphony_settings, planted_name)",
    ),
    "alpaca order": (
        "submit_order",
        "sleeves.alpaca_orders.submit_order(planted_client, planted_order)",
    ),
    "alpaca order by bare name": ("submit_order", "submit_order(planted_order)"),
    "alpaca order imported lazily": (
        "submit_order",
        "from sleeves.alpaca_orders import submit_order as planted_submit\n"
        "planted_submit(planted_order)",
    ),
    "alpaca order through the module": (
        "submit_order",
        "from sleeves import alpaca_orders as planted_orders\n"
        "planted_orders.submit_order(planted_order)",
    ),
    "alpaca bracket order": (
        "submit_bracket_order",
        "alpaca_orders.submit_bracket_order(planted_client, planted_order)",
    ),
    "alpaca trailing stop order": (
        "submit_trailing_stop_order",
        "alpaca_orders.submit_trailing_stop_order(planted_client, planted_order)",
    ),
}


class TestGroupEScanReportsAPlantedLossyReaderOrMoneyMover:
    """The entry points of the review run path (and the fetch the panel and API share) are shown
    to fail when one of the functions the feature must never reach is planted in their body or in
    a function they call. Every entry point of the group is also checked clean, unplanted, by the
    tests above, with the same predicate."""

    @pytest.mark.parametrize("form", list(_PLANTED_ENGINE_CALLS))
    @pytest.mark.parametrize("fn_name", [*_TENURE_RUN_PATH_FN_NAMES, _TENURE_REVIEW_FETCH_FN_NAME])
    def test_a_planted_call_is_reported_from_the_entry_point_it_is_planted_in(self, fn_name, form):
        expected, statement = _PLANTED_ENGINE_CALLS[form]
        hits = _exec_seam_hits(_tree_with_planted_call(fn_name, statement), fn_name)
        assert any(expected in hit for hit in hits), (
            f"a {form} planted in {fn_name} was not reported; hits: {hits}"
        )

    @pytest.mark.parametrize("form", list(_PLANTED_ENGINE_CALLS))
    @pytest.mark.parametrize(
        "planted_in, entry_point",
        [
            (_RETIREMENT_PREPARE_FN_NAME, _TENURE_REVIEW_JOB_FN_NAME),
            (_TENURE_REVIEW_JOB_FN_NAME, _RETIREMENT_NIGHTLY_BODY_FN_NAME),
            (_RETIREMENT_PREPARE_FN_NAME, _RETIREMENT_TICK_WORKER_FN_NAME),
        ],
        ids=[
            "preparation step reached from the job",
            "job reached from the nightly tick body",
            "preparation step reached from the tick worker",
        ],
    )
    def test_a_planted_call_is_reported_from_an_entry_point_that_calls_the_function(
        self, planted_in, entry_point, form
    ):
        expected, statement = _PLANTED_ENGINE_CALLS[form]
        hits = _exec_seam_hits(_tree_with_planted_call(planted_in, statement), entry_point)
        assert any(expected in hit for hit in hits), (
            f"{planted_in} is not on the call graph of {entry_point}; hits: {hits}"
        )

    def test_the_run_path_entry_points_are_distinct_functions_with_distinct_bodies(self):
        """Non-vacuity of the list itself: each name is found and none is an alias of another."""
        tree = _parse_app_py()
        nodes = [_find_function_def(tree, name) for name in _TENURE_RUN_PATH_FN_NAMES]
        assert all(node is not None for node in nodes)
        assert len({id(node) for node in nodes}) == len(_TENURE_RUN_PATH_FN_NAMES)
        assert len({ast.dump(node) for node in nodes}) == len(_TENURE_RUN_PATH_FN_NAMES)


# The dashboard session check and the logout handler end sessions and nothing else. The auth hook
# runs before every route and the logout handler writes the revocation record, so neither may reach
# an order, a symphony creation, the engine or a LIVE_EXECUTION read, directly or through a helper.
_SESSION_ENTRY_POINT_FN_NAMES = ["_auth_before_request", "logout"]
_SESSION_PLANTED_FORMS = {
    "creation call": (_SAVE_SYMPHONY_ATTR, _PLANTED_CALLS["attribute call"]),
    "creation call by bare name": (_SAVE_SYMPHONY_ATTR, _PLANTED_CALLS["bare call"]),
    "panic sell": _PLANTED_ENGINE_CALLS["panic sell"],
    "panic sell route": _PLANTED_ENGINE_CALLS["panic sell route"],
}
# What each entry point must visibly walk into, so a scan that finds nothing is not a scan that
# never looked: the validity helpers behind the hook, the revocation writer behind the handler.
_SESSION_WALK_MUST_OPEN = {
    "_auth_before_request": [
        "_session_is_valid",
        "_session_issue_is_valid",
        "_revocation_state",
    ],
    "logout": ["_revoke_sessions_issued_before"],
}


class TestSessionAuthAndLogoutNeverReachAnOrderOrACreation:
    @pytest.mark.parametrize("fn_name", _SESSION_ENTRY_POINT_FN_NAMES)
    def test_the_unplanted_tree_is_clean(self, fn_name):
        assert _exec_seam_hits(_parse_app_py(), fn_name) == []

    @pytest.mark.parametrize("fn_name", _SESSION_ENTRY_POINT_FN_NAMES)
    def test_the_walk_opens_the_session_helpers_the_entry_point_depends_on(self, fn_name):
        opened = {
            getattr(node, "name", None)
            for node in _collect_transitive_local_call_subtrees(_parse_app_py(), fn_name)
        }
        missing = [name for name in _SESSION_WALK_MUST_OPEN[fn_name] if name not in opened]
        assert missing == [], (
            f"the walk from {fn_name} never opened {missing}: {sorted(n for n in opened if n)}"
        )

    @pytest.mark.parametrize("form", list(_SESSION_PLANTED_FORMS))
    @pytest.mark.parametrize("fn_name", _SESSION_ENTRY_POINT_FN_NAMES)
    def test_a_planted_order_or_creation_is_reported(self, fn_name, form):
        expected, statement = _SESSION_PLANTED_FORMS[form]
        hits = _exec_seam_hits(_tree_with_planted_call(fn_name, statement), fn_name)
        assert any(expected in hit for hit in hits), (
            f"a {form} planted in {fn_name} was not reported; hits: {hits}"
        )

    @pytest.mark.parametrize("form", list(_SESSION_PLANTED_FORMS))
    def test_a_planted_call_in_a_helper_the_hook_calls_is_reported_from_the_hook(self, form):
        expected, statement = _SESSION_PLANTED_FORMS[form]
        tree = _tree_with_planted_call("_session_is_valid", statement)
        hits = _exec_seam_hits(tree, "_auth_before_request")
        assert any(expected in hit for hit in hits), (
            f"_session_is_valid is not on the call graph of the hook; hits: {hits}"
        )


# A function the review path hands to something else to run is as reachable as one it calls. The
# route used to submit the job to an executor and now starts it as a thread target; the tick
# hands its worker to a thread. Each form below puts a reference to a function into an entry
# point's body, in memory, and the scan must report what that function does.
_REFERENCE_FORMS = {
    "executor submit": "planted_executor.submit({fn})",
    "executor submit with arguments": "planted_executor.submit({fn}, 'on_demand', planted_owner)",
    "thread target": "threading.Thread(target={fn}, daemon=True).start()",
    "thread target with arguments": (
        "threading.Thread(target={fn}, args=('on_demand', planted_owner), daemon=True).start()"
    ),
    "scheduled callback": "schedule.every().day.at('03:45').do({fn})",
    "callback argument": "register_planted_callback({fn})",
    "wrapped in a partial": "functools.partial({fn}, planted_argument)()",
    "value in a table": "planted_handlers = {{'planted': {fn}}}",
}
# What is referenced: two functions the feature must never reach (reported by name, and by what
# their bodies hold), and an innocent-named helper of the app whose body has a symphony creation
# planted in it (reported only by following the reference).
_REFERENCED_TARGETS = {
    "frontrunner approval route": (
        "ai_advisor_proposal_approve",
        "ai_advisor_proposal_approve",
        None,
    ),
    "panic sell route": ("sell_account", "sell_account", None),
    "innocent helper holding a creation call": (
        "_format_eastern_run_time",
        _SAVE_SYMPHONY_ATTR,
        "composer_draft_client.save_symphony(planted_argument)",
    ),
}
_REFERENCING_ENTRY_POINTS = [*_TENURE_RUN_PATH_FN_NAMES, _TENURE_REVIEW_FETCH_FN_NAME]


def _tree_with_planted_reference(
    entry_point: str, statement: str, planted_in: str | None, planted_call: str | None
) -> ast.Module:
    tree = _tree_with_planted_call(entry_point, statement)
    if planted_in is not None:
        node = _find_function_def(tree, planted_in)
        if node is None:
            pytest.fail(f"app.py has no function named {planted_in!r}")
        node.body.append(ast.parse(planted_call).body[0])
        ast.fix_missing_locations(tree)
    return tree


class TestGroupEScanFollowsAFunctionHandedOverByReference:
    @pytest.mark.parametrize("fn_name", _REFERENCING_ENTRY_POINTS)
    def test_the_unplanted_tree_is_clean_with_the_wider_walk(self, fn_name):
        assert _exec_seam_hits(_parse_app_py(), fn_name) == []

    @pytest.mark.parametrize("target", list(_REFERENCED_TARGETS))
    @pytest.mark.parametrize("form", list(_REFERENCE_FORMS))
    @pytest.mark.parametrize("fn_name", _REFERENCING_ENTRY_POINTS)
    def test_a_reference_planted_in_an_entry_point_is_reported(self, fn_name, form, target):
        referenced, expected, planted_call = _REFERENCED_TARGETS[target]
        statement = _REFERENCE_FORMS[form].format(fn=referenced)
        tree = _tree_with_planted_reference(
            fn_name, statement, referenced if planted_call else None, planted_call
        )

        hits = _exec_seam_hits(tree, fn_name)

        assert any(expected in hit for hit in hits), (
            f"a {form} of {referenced} planted in {fn_name} was not reported; hits: {hits}"
        )

    @pytest.mark.parametrize("form", list(_REFERENCE_FORMS))
    def test_the_walk_opens_the_function_that_is_referenced(self, form):
        statement = _REFERENCE_FORMS[form].format(fn="_format_eastern_run_time")
        tree = _tree_with_planted_call(_TENURE_REVIEW_JOB_FN_NAME, statement)

        opened = {
            getattr(node, "name", None)
            for node in _collect_transitive_local_call_subtrees(tree, _TENURE_REVIEW_JOB_FN_NAME)
        }

        assert "_format_eastern_run_time" in opened, f"the {form} was not followed: {opened}"

    def test_a_reference_in_a_callee_is_reported_from_the_entry_point_that_calls_it(self):
        tree = _tree_with_planted_reference(
            _RETIREMENT_PREPARE_FN_NAME,
            "planted_executor.submit(_format_eastern_run_time)",
            "_format_eastern_run_time",
            "composer_draft_client.save_symphony(planted_argument)",
        )

        hits = _exec_seam_hits(tree, _TENURE_REVIEW_JOB_FN_NAME)

        assert any(_SAVE_SYMPHONY_ATTR in hit for hit in hits), hits

    def test_a_name_that_is_stored_and_not_read_is_not_followed(self):
        """A write to a name is not a reference to a function: the walk stays bounded."""
        tree = _tree_with_planted_call(
            _TENURE_REVIEW_JOB_FN_NAME, "_format_eastern_run_time = planted_value"
        )
        node = _find_function_def(tree, "_format_eastern_run_time")
        node.body.append(ast.parse("composer_draft_client.save_symphony(planted_argument)").body[0])
        ast.fix_missing_locations(tree)

        assert _exec_seam_hits(tree, _TENURE_REVIEW_JOB_FN_NAME) == []


# A name bound at module level reaches what it is bound to. The walk used to open a module-level
# def and nothing else, so a call through an import alias, an assignment alias or a table of
# functions read as a call of an unknown name. Each form below binds ``planted_alias`` (or a
# table) at module level of the in-memory tree and uses it from an entry point; the scan must
# report what the binding holds.
_ALIAS_FORMS = {
    "import-from alias of the creation call": (
        "from advisors.composer_draft_client import save_symphony as planted_alias",
        "planted_alias(planted_argument)",
        "save_symphony",
    ),
    "import alias of the draft client module": (
        "import advisors.composer_draft_client as planted_alias",
        "planted_alias.save_symphony(planted_argument)",
        "composer_draft_client",
    ),
    "import-from alias of an order": (
        "from sleeves.alpaca_orders import submit_order as planted_alias",
        "planted_alias(planted_order)",
        "submit_order",
    ),
    "import-from alias of the live-mode write": (
        "from database import set_symphony_live_mode as planted_alias",
        "planted_alias(planted_name, 1, 'tenure')",
        "set_symphony_live_mode",
    ),
    "import-from alias of an HTTP write": (
        "from requests import post as planted_alias",
        "planted_alias(planted_url)",
        "HTTP write post",
    ),
    "assignment alias of a route": (
        "planted_alias = ai_advisor_proposal_approve",
        "planted_alias()",
        "ai_advisor_proposal_approve",
    ),
    "assignment alias of an HTTP write": (
        "planted_alias = requests.post",
        "planted_alias(planted_url)",
        "HTTP write post",
    ),
    "assignment alias of the live-mode write": (
        "planted_alias = database.set_symphony_live_mode",
        "planted_alias(planted_name, 1, 'tenure')",
        "set_symphony_live_mode",
    ),
    "annotated assignment alias": (
        "planted_alias: object = sell_account",
        "planted_alias()",
        "sell_account",
    ),
    "tuple assignment alias": (
        "planted_alias, planted_other = sell_account, len",
        "planted_alias()",
        "sell_account",
    ),
    "alias of an alias": (
        "planted_first = sell_account\nplanted_alias = planted_first",
        "planted_alias()",
        "sell_account",
    ),
    "dict table": ("PLANTED_TABLE = {'go': sell_account}", "PLANTED_TABLE['go']()", "sell_account"),
    "list table": (
        "PLANTED_TABLE = [('go', requests.post)]",
        "for planted_label, planted_fn in PLANTED_TABLE:\n    planted_fn(planted_url)",
        "HTTP write post",
    ),
    "table of an alias": (
        "planted_alias = ai_advisor_proposal_approve\nPLANTED_TABLE = {'go': planted_alias}",
        "PLANTED_TABLE['go']()",
        "ai_advisor_proposal_approve",
    ),
    "conditional import": (
        "try:\n"
        "    from advisors.composer_draft_client import save_symphony as planted_alias\n"
        "except ImportError:\n"
        "    planted_alias = None",
        "planted_alias(planted_argument)",
        "save_symphony",
    ),
}


def _tree_with_planted_binding(entry_point: str, binding: str, use: str) -> ast.Module:
    tree = _tree_with_planted_call(entry_point, use)
    tree.body.extend(ast.parse(binding).body)
    return tree


class TestGroupEScanFollowsAModuleLevelNameBoundToSomethingForbidden:
    @pytest.mark.parametrize("form", list(_ALIAS_FORMS))
    @pytest.mark.parametrize("fn_name", _REFERENCING_ENTRY_POINTS)
    def test_a_use_of_the_name_is_reported_from_the_entry_point(self, fn_name, form):
        binding, use, expected = _ALIAS_FORMS[form]

        hits = _exec_seam_hits(_tree_with_planted_binding(fn_name, binding, use), fn_name)

        assert any(expected in hit for hit in hits), (
            f"{form}: a use planted in {fn_name} was not reported; hits: {hits}"
        )

    @pytest.mark.parametrize("form", list(_ALIAS_FORMS))
    @pytest.mark.parametrize(
        "planted_in, entry_point",
        [
            (_RETIREMENT_PREPARE_FN_NAME, _TENURE_REVIEW_JOB_FN_NAME),
            (_TENURE_REVIEW_JOB_FN_NAME, _RETIREMENT_NIGHTLY_BODY_FN_NAME),
        ],
        ids=["preparation step reached from the job", "job reached from the nightly tick body"],
    )
    def test_a_use_in_a_callee_is_reported_from_the_entry_point_that_calls_it(
        self, planted_in, entry_point, form
    ):
        binding, use, expected = _ALIAS_FORMS[form]

        hits = _exec_seam_hits(_tree_with_planted_binding(planted_in, binding, use), entry_point)

        assert any(expected in hit for hit in hits), (
            f"{form}: {planted_in} is not on the call graph of {entry_point}; hits: {hits}"
        )

    @pytest.mark.parametrize("form", list(_ALIAS_FORMS))
    def test_the_walk_opens_the_statement_that_binds_the_name(self, form):
        binding, use, _ = _ALIAS_FORMS[form]
        tree = _tree_with_planted_binding(_TENURE_REVIEW_JOB_FN_NAME, binding, use)

        opened = _collect_transitive_local_call_subtrees(tree, _TENURE_REVIEW_JOB_FN_NAME)

        planted_statements = [
            node
            for node in opened
            if isinstance(node, ast.stmt) and "planted" in ast.unparse(node).lower()
        ]
        assert planted_statements, f"the {form} was not opened by the walk"

    def test_a_function_reached_through_an_alias_is_followed_into_its_body(self):
        tree = _tree_with_planted_binding(
            _TENURE_REVIEW_JOB_FN_NAME,
            "planted_alias = _format_eastern_run_time",
            "planted_alias(planted_value)",
        )
        node = _find_function_def(tree, "_format_eastern_run_time")
        node.body.append(ast.parse("composer_draft_client.save_symphony(planted_argument)").body[0])
        ast.fix_missing_locations(tree)

        hits = _exec_seam_hits(tree, _TENURE_REVIEW_JOB_FN_NAME)

        assert any(_SAVE_SYMPHONY_ATTR in hit for hit in hits), hits

    def test_a_binding_the_entry_point_never_reads_is_not_followed(self):
        tree = _tree_with_planted_binding(
            _TENURE_REVIEW_JOB_FN_NAME, "planted_alias = sell_account", "planted_other = 1"
        )

        assert _exec_seam_hits(tree, _TENURE_REVIEW_JOB_FN_NAME) == []

    def test_names_bound_to_each_other_end_the_walk(self):
        tree = _tree_with_planted_binding(
            _TENURE_REVIEW_JOB_FN_NAME,
            "planted_first = planted_second\nplanted_second = planted_first",
            "planted_first()",
        )

        assert _exec_seam_hits(tree, _TENURE_REVIEW_JOB_FN_NAME) == []


class TestEveryTemplateHelperIsAnEntryPointOfTheScan:
    """A helper the template calls is reached from no route's call graph, so the scan covers it
    only when it is listed. A helper added later and not listed would hold a trade call
    unseen; this names it."""

    def test_the_real_app_registers_the_helpers_this_file_lists_and_no_others(self):
        registered = _registered_template_function_names(_parse_app_py())
        called_by_a_registered_helper = {"_format_eastern_run_time"}
        assert set(_TENURE_RENDER_HELPER_FN_NAMES) - called_by_a_registered_helper <= registered, (
            "a listed helper is not registered"
        )
        unlisted = sorted(registered - set(_CYCLE_2C_ENTRY_POINT_FN_NAMES))
        assert unlisted == [], f"template helpers outside every scan entry point: {unlisted}"

    def test_the_check_reports_a_filter_a_test_and_a_global_that_are_not_listed(self):
        tree = ast.parse(
            "@app.template_filter('shout')\n"
            "def _shout(value):\n"
            "    return value\n"
            "def _is_odd(value):\n"
            "    return value % 2\n"
            "app.jinja_env.tests['odd'] = _is_odd\n"
            "@app.template_global()\n"
            "def _today():\n"
            "    return 0\n"
            "def _plain():\n"
            "    return 1\n"
        )
        assert _registered_template_function_names(tree) == {"_shout", "_is_odd", "_today"}


# Every way Flask, Blueprints and Jinja take a function for a template. The drift guard above
# is only as strong as the forms it recognizes, so each form is planted and shown to be seen,
# for a filter, a test and a global alike: a form that only one table's tests exercise leaves
# the other two unproven.
_REGISTRATION_KINDS = ("filter", "test", "global")
_TABLE_OF_KIND = {"filter": "filters", "test": "tests", "global": "globals"}


def _registration_forms() -> list:
    forms = [
        pytest.param("app.add_template_filter(f=_planted, name='planted')", id="add by keyword"),
        pytest.param("app.add_template_filter(_planted)", id="add_template_filter without a name"),
        pytest.param(
            "app.context_processor(_planted)", id="context_processor called with the function"
        ),
        pytest.param(
            "app.app_context_processor(_planted)", id="app_context_processor called with it"
        ),
        pytest.param(
            "blueprint.app_context_processor(_planted)", id="blueprint context processor call"
        ),
        pytest.param(
            "app.template_context_processors[None].append(_planted)",
            id="appended to the processor list",
        ),
        pytest.param(
            "app.template_context_processors[None].extend([_planted])",
            id="extended onto the processor list",
        ),
        pytest.param(
            "environment.tests['planted'] = _planted", id="tests subscript of another object"
        ),
    ]
    for kind in _REGISTRATION_KINDS:
        table = f"app.jinja_env.{_TABLE_OF_KIND[kind]}"
        forms += [
            pytest.param(
                f"app.add_template_{kind}(_planted, 'planted')", id=f"add_template_{kind}"
            ),
            pytest.param(
                f"blueprint.add_app_template_{kind}(_planted, 'planted')",
                id=f"add_app_template_{kind}",
            ),
            # a decorator applied by a call instead of by @
            pytest.param(
                f"app.template_{kind}('planted')(_planted)", id=f"template_{kind} applied by a call"
            ),
            pytest.param(
                f"app.template_{kind}()(_planted)", id=f"template_{kind} without a name applied"
            ),
            pytest.param(
                f"app.template_{kind}(name='planted')(_planted)",
                id=f"template_{kind} by keyword applied",
            ),
            pytest.param(
                f"blueprint.app_template_{kind}('planted')(_planted)",
                id=f"app_template_{kind} applied by a call",
            ),
            # a table updated, by every argument shape update takes
            pytest.param(f"{table}.update(planted=_planted)", id=f"{table} update by keyword"),
            pytest.param(f"{table}.update({{'planted': _planted}})", id=f"{table} update by dict"),
            pytest.param(
                f"{table}.update(dict(planted=_planted))", id=f"{table} update by dict call"
            ),
            pytest.param(
                f"{table}.update([('planted', _planted)])", id=f"{table} update by a list of pairs"
            ),
            pytest.param(
                f"{table}.update((('planted', _planted),))",
                id=f"{table} update by a tuple of pairs",
            ),
            pytest.param(
                f"{table}.update(('planted', _planted) for _ in range(1))",
                id=f"{table} update by a generator of pairs",
            ),
            pytest.param(
                f"{table}.update(zip(['planted'], [_planted]))", id=f"{table} update by zip"
            ),
            pytest.param(
                f"{table}.update(planted=functools.partial(_planted, 1))",
                id=f"{table} update by a wrapped function",
            ),
            pytest.param(f"{table}.setdefault('planted', _planted)", id=f"{table} setdefault"),
            pytest.param(f"{table}.__setitem__('planted', _planted)", id=f"{table} __setitem__"),
            pytest.param(f"{table}['planted'] = _planted", id=f"{table} subscript"),
            # the in-place union and the replacement of the whole table
            pytest.param(f"{table} |= {{'planted': _planted}}", id=f"{table} |= a dict"),
            pytest.param(f"{table} |= dict(planted=_planted)", id=f"{table} |= dict call"),
            pytest.param(
                f"{table} |= {{'a': _first, 'b': _planted}}", id=f"{table} |= two functions"
            ),
            pytest.param(f"{table} = {{**{table}, 'planted': _planted}}", id=f"{table} replaced"),
            pytest.param(
                f"{table} = {table} | {{'planted': _planted}}", id=f"{table} replaced by a union"
            ),
            pytest.param(f"{table}.__ior__({{'planted': _planted}})", id=f"{table} __ior__"),
        ]
    return forms


_REGISTRATION_FORMS = _registration_forms()
# Three registration shapes, each with a helper that creates a symphony the way a real one would.
_PLANTED_WITH_A_CALL_APPLIED_DECORATOR = (
    "def _planted_extra(value):\n"
    "    from advisors import composer_draft_client\n\n"
    "    return composer_draft_client.save_symphony(value)\n\n\n"
    "app.template_filter('planted_extra')(_planted_extra)\n"
)
_PLANTED_WITH_A_PAIRS_UPDATE = (
    "def _planted_extra(value):\n"
    "    from advisors import composer_draft_client\n\n"
    "    return composer_draft_client.save_symphony(value)\n\n\n"
    "app.jinja_env.filters.update([('planted_extra', _planted_extra)])\n"
)
_PLANTED_WITH_AN_IN_PLACE_UNION = (
    "def _planted_extra(value):\n"
    "    from advisors import composer_draft_client\n\n"
    "    return composer_draft_client.save_symphony(value)\n\n\n"
    "app.jinja_env.filters |= {'planted_extra': _planted_extra}\n"
)
_PLANTED_HELPER_SOURCE = (
    "def _first(value):\n    return value\n\n\n"
    "def _planted(value):\n    return composer_draft_client.save_symphony(value)\n\n\n"
)
_CONTEXT_PROCESSOR_FORMS = [
    pytest.param(
        "@app.context_processor\ndef _inject():\n    return {'planted': _planted}\n",
        id="context processor returning a dict literal",
    ),
    pytest.param(
        "@app.context_processor\ndef _inject():\n    return dict(planted=_planted)\n",
        id="context processor returning dict()",
    ),
    pytest.param(
        "def _inject():\n    return {'planted': _planted}\n\n\napp.context_processor(_inject)\n",
        id="context processor registered by a call",
    ),
    pytest.param(
        "@blueprint.app_context_processor\ndef _inject():\n    return {'planted': _planted}\n",
        id="blueprint context processor returning a dict literal",
    ),
    pytest.param(
        "def _inject():\n    return {'planted': _planted}\n\n\n"
        "app.template_context_processors[None].append(_inject)\n",
        id="context processor appended to the processor list",
    ),
]


def _unlisted_template_helpers(tree: ast.Module) -> list[str]:
    return sorted(_registered_template_function_names(tree) - set(_CYCLE_2C_ENTRY_POINT_FN_NAMES))


def _real_tree_with(source: str) -> ast.Module:
    tree = _parse_app_py()
    tree.body.extend(ast.parse(source).body)
    ast.fix_missing_locations(tree)
    return tree


class TestEveryRegistrationFormIsSeenByTheDriftGuard:
    @pytest.mark.parametrize("statement", _REGISTRATION_FORMS)
    def test_a_function_registered_by_the_form_is_found(self, statement):
        found = _registered_template_function_names(ast.parse(statement))
        assert "_planted" in found, f"the form is not read: {sorted(found)}"
        assert found <= {"_planted", "_first"}, f"the form reports something else: {sorted(found)}"

    @pytest.mark.parametrize("source", _CONTEXT_PROCESSOR_FORMS)
    def test_a_name_a_context_processor_returns_is_found_with_the_processor(self, source):
        assert _registered_template_function_names(ast.parse(source)) == {"_inject", "_planted"}

    def test_two_functions_stored_by_one_update_are_both_found(self):
        statement = "app.jinja_env.filters.update({'a': _first}, b=_second)"
        assert _registered_template_function_names(ast.parse(statement)) == {"_first", "_second"}

    @pytest.mark.parametrize("kind", _REGISTRATION_KINDS)
    def test_a_function_without_a_name_is_reported_as_unlisted(self, kind):
        table = f"app.jinja_env.{_TABLE_OF_KIND[kind]}"
        for statement in (
            f"app.template_{kind}('x')(lambda v: composer_draft_client.save_symphony(v))",
            f"{table}['x'] = lambda v: composer_draft_client.save_symphony(v)",
            f"{table}.update(x=lambda v: composer_draft_client.save_symphony(v))",
            f"{table}.update([('x', lambda v: composer_draft_client.save_symphony(v))])",
        ):
            found = _registered_template_function_names(ast.parse(statement))
            assert found == {_ANONYMOUS_HELPER}, (statement, found)
            assert _unlisted_template_helpers(_real_tree_with(statement)) == [_ANONYMOUS_HELPER]

    @pytest.mark.parametrize(
        "statement",
        [
            "app.add_template_filter('shout')",
            "app.template_filter('shout')",
            "app.template_filter('shout')()",
            "app.jinja_env.filters.update()",
            "app.jinja_env.filters.get('shout')",
            "app.jinja_env.filters['shout']",
            "copied = app.jinja_env.filters.copy()",
            "registry.update(planted=_planted)",
            "registry.update([('planted', _planted)])",
            "registry |= {'planted': _planted}",
            "registry['planted'] = _planted",
            "filters = {'planted': _planted}",
            "app.add_url_rule('/x', view_func=_planted)",
            "app.errorhandler(404)(_planted)",
            "functools.lru_cache(maxsize=1)(_planted)",
            "app.template_context_processors[None]",
            "handlers.append(_planted)",
        ],
        ids=[
            "a name and no function",
            "a decorator factory never applied",
            "a decorator factory applied to nothing",
            "an empty update",
            "a read of the table",
            "an index read of the table",
            "a copy of the table",
            "an update of another object",
            "a pairs update of another object",
            "a union into another object",
            "a store into another object",
            "a local that happens to be called filters",
            "a route registration",
            "an error handler applied by a call",
            "another decorator applied by a call",
            "a read of the processor list",
            "an append to another list",
        ],
    )
    def test_a_call_that_registers_no_template_function_is_not_reported(self, statement):
        assert _registered_template_function_names(ast.parse(statement)) == set()

    @pytest.mark.parametrize("statement", _REGISTRATION_FORMS)
    def test_a_helper_holding_a_symphony_creation_call_registered_by_the_form_is_unlisted(
        self, statement
    ):
        tree = _real_tree_with(_PLANTED_HELPER_SOURCE + statement + "\n")
        unlisted = _unlisted_template_helpers(tree)
        assert "_planted" in unlisted, f"the planted helper is not reported: {unlisted}"
        assert set(unlisted) <= {"_planted", "_first"}, unlisted

    @pytest.mark.parametrize("source", _CONTEXT_PROCESSOR_FORMS)
    def test_a_helper_returned_by_a_context_processor_is_unlisted_with_the_processor(self, source):
        tree = _real_tree_with(_PLANTED_HELPER_SOURCE + source)
        assert _unlisted_template_helpers(tree) == ["_inject", "_planted"]

    @pytest.mark.parametrize(
        "planted",
        [
            _PLANTED_WITH_A_CALL_APPLIED_DECORATOR,
            _PLANTED_WITH_A_PAIRS_UPDATE,
            _PLANTED_WITH_AN_IN_PLACE_UNION,
        ],
        ids=["decorator applied by a call", "filters update by a list of pairs", "filters |= dict"],
    )
    def test_each_of_these_forms_registers_a_symphony_creating_helper_the_scan_would_not_reach(
        self, planted
    ):
        assert _unlisted_template_helpers(_parse_app_py()) == []
        assert _unlisted_template_helpers(_real_tree_with(planted)) == ["_planted_extra"]

    def test_the_unplanted_app_has_no_unlisted_helper(self):
        assert _unlisted_template_helpers(_parse_app_py()) == []


# The static drift guard reads the registration spellings it knows; a helper registered by a
# spelling it does not know (a table bound to a local, setattr, operator.setitem, a loop over a
# local table) is reached by the template and by no scan entry point. This guard reads the
# running application instead: every filter, test and global the app added to its Jinja
# environment, by whatever spelling, must be a function of the app module that the scan lists.


def _runtime_unlisted_template_helpers(env, baseline_env, app_module) -> list[str]:
    """``table:name (what it is)`` for each filter, test and global of ``env`` that the
    framework did not put there and that is not a listed function of the app module.

    ``baseline_env`` is a fresh environment of the same framework, so the framework's own
    entries are excluded by what they are (the very object the baseline holds under that name,
    or an object of flask, jinja2, werkzeug or markupsafe that the baseline also has a name
    for), never by name alone: a name the framework owns can still be re-bound by the app to
    a function of its own.

    A helper is listed only when it IS the module-level function of that name in ``app_module``
    (the object the static scan reads), not merely a function that carries a listed name and
    claims the app as its module: a nested function or a copy would pass on name alone."""
    framework_roots = ("flask", "jinja2", "werkzeug", "markupsafe")
    unlisted = []
    for table in sorted(_TEMPLATE_REGISTRATION_TABLES):
        registered = getattr(env, table)
        baseline = getattr(baseline_env, table)
        for name, value in sorted(registered.items()):
            owner = getattr(value, "__module__", None) or type(value).__module__
            if name in baseline and (
                baseline[name] is value or owner.split(".")[0] in framework_roots
            ):
                continue
            if not _is_the_listed_function(value, app_module):
                described = getattr(value, "__qualname__", None) or type(value).__name__
                unlisted.append(f"{table}:{name} ({described})")
    return unlisted


def _is_the_listed_function(value, app_module) -> bool:
    """True when ``value`` IS the module-level function of app.py that the scan lists under its
    name (the object the static scan reads), not a nested function, a copy or a
    ``functools.wraps`` look-alike that carries the same name and module.

    One function outside app.py is accepted, by identity: the shared pure formatter
    ``analytics.format_et_stamp`` (a string in, a string out, no I/O), which the app registers
    as the ``et_stamp`` filter directly with no wrapper of its own. Any other object, a wrapper
    of it included, is still reported."""
    if value is _shared_pure_template_formatter():
        return True
    return (
        callable(value)
        and getattr(value, "__module__", None) == app_module.__name__
        and getattr(value, "__name__", None) in _CYCLE_2C_ENTRY_POINT_FN_NAMES
        and value is getattr(app_module, value.__name__, None)
    )


def _shared_pure_template_formatter():
    import analytics  # noqa: PLC0415

    return analytics.format_et_stamp


def _fresh_flask_app():
    import flask  # noqa: PLC0415

    return flask.Flask("template-context-baseline")


def _runtime_unlisted_context_processors(flask_app, baseline_app, app_module) -> list[str]:
    """``context_processors:<blueprint or None> (<what it is>)`` for each context processor of
    ``flask_app`` that a fresh Flask application does not have and that is not a listed function
    of the app module. A processor runs on every render and can hand any function to a
    template, so it counts as a template helper however it was added (a decorator, a call, an
    alias of the list, a blueprint's own list)."""
    baseline = baseline_app.template_context_processors
    unlisted = []
    for key, processors in sorted(
        flask_app.template_context_processors.items(), key=lambda item: str(item[0])
    ):
        known = baseline.get(key, [])
        for processor in processors:
            if any(processor is framework_owned for framework_owned in known):
                continue
            if not _is_the_listed_function(processor, app_module):
                described = getattr(processor, "__qualname__", None) or type(processor).__name__
                unlisted.append(f"context_processors:{key} ({described})")
    return unlisted


def _template_table_reads_inside_functions(tree: ast.Module) -> list[str]:
    """Every read, inside a function body (a lambda too), of a template table of the Jinja
    environment (``<...>.jinja_env.filters``, ``.tests`` or ``.globals``) or of the
    application's context processor lists, by attribute or by ``getattr``/``setattr``/
    ``delattr`` with the name as a literal. The runtime guard reads the tables once, after
    the module is imported; a helper stored from inside a function runs later and escapes it.
    Reads at module level are import-time registrations, which the runtime guard sees."""
    watched = _TEMPLATE_REGISTRATION_TABLES | {_TEMPLATE_PROCESSOR_LIST_ATTR}
    found: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        where = getattr(fn, "name", "<lambda>")
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute):
                is_table = node.attr in _TEMPLATE_REGISTRATION_TABLES and (
                    "jinja_env" in ast.unparse(node.value)
                )
                if is_table or node.attr == _TEMPLATE_PROCESSOR_LIST_ATTR:
                    found.add(f"{where}:{node.lineno} {ast.unparse(node)}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "setattr", "delattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in watched
                and (
                    "jinja_env" in ast.unparse(node.args[0])
                    or node.args[1].value == _TEMPLATE_PROCESSOR_LIST_ATTR
                )
            ):
                found.add(f"{where}:{node.lineno} {ast.unparse(node)}")
    return sorted(found)


@pytest.fixture()
def running_app():
    """The imported application, and a fresh environment of the same framework to compare it
    with."""
    import flask  # noqa: PLC0415

    import app as app_module  # noqa: PLC0415

    return app_module, flask.Flask("template-helper-baseline").jinja_env


class TestEveryTemplateHelperOfTheRunningAppIsAnEntryPointOfTheScan:
    def test_the_app_has_added_helpers_to_every_table_so_the_comparison_is_not_vacuous(
        self, running_app
    ):
        app_module, baseline = running_app
        env = app_module.app.jinja_env
        for table in sorted(_TEMPLATE_REGISTRATION_TABLES):
            added = set(getattr(env, table)) - set(getattr(baseline, table))
            if table != "globals":
                assert added, f"the app registers nothing in {table}"
        assert {"tenure_text", "finite_number"} <= set(env.filters) | set(env.tests)

    def test_every_filter_test_and_global_the_app_added_is_a_listed_function(self, running_app):
        app_module, baseline = running_app
        unlisted = _runtime_unlisted_template_helpers(
            app_module.app.jinja_env, baseline, app_module
        )
        assert unlisted == [], f"template helpers outside every scan entry point: {unlisted}"

    @pytest.mark.parametrize("table", sorted(_TEMPLATE_REGISTRATION_TABLES))
    def test_a_helper_registered_through_an_alias_of_the_table_is_reported(
        self, running_app, table
    ):
        app_module, baseline = running_app

        def _planted_through_an_alias(value):
            return value

        alias = getattr(app_module.app.jinja_env, table)
        alias["planted_through_an_alias"] = _planted_through_an_alias
        try:
            unlisted = _runtime_unlisted_template_helpers(
                app_module.app.jinja_env, baseline, app_module
            )
        finally:
            del alias["planted_through_an_alias"]
        assert len(unlisted) == 1 and unlisted[0].startswith(f"{table}:planted_through_an_alias"), (
            unlisted
        )
        assert (
            _runtime_unlisted_template_helpers(app_module.app.jinja_env, baseline, app_module) == []
        ), "the planted helper was not removed"

    @pytest.mark.parametrize("listed_name", _CYCLE_2C_ENTRY_POINT_FN_NAMES)
    def test_a_nested_function_that_carries_a_listed_name_and_the_app_module_is_reported(
        self, running_app, listed_name
    ):
        """The static scan reads only the module-level function of a listed name; a nested
        closure with that name (and ``__module__`` set to the app's) would pass on name alone
        while holding code the scan never reads."""
        app_module, baseline = running_app

        def make():
            def nested(value):
                return value

            nested.__name__ = listed_name
            nested.__qualname__ = f"make.<locals>.{listed_name}"
            nested.__module__ = app_module.__name__
            return nested

        planted = make()
        env = app_module.app.jinja_env
        assert planted is not getattr(app_module, listed_name), "premise: not the listed function"
        env.filters["planted_nested"] = planted
        try:
            unlisted = _runtime_unlisted_template_helpers(env, baseline, app_module)
        finally:
            del env.filters["planted_nested"]
        assert len(unlisted) == 1 and unlisted[0].startswith("filters:planted_nested"), unlisted

    @pytest.mark.parametrize("listed_name", ["_tenure_text", "_tenure_number", "_is_finite_number"])
    def test_a_functools_wraps_look_alike_of_a_listed_function_is_reported(
        self, running_app, listed_name
    ):
        """``functools.wraps`` copies the name, the module and the docstring, so a wrapper
        that does something else wears the listed function's whole identity; only identity
        with the module-level function tells them apart."""
        import functools  # noqa: PLC0415

        app_module, baseline = running_app
        listed = getattr(app_module, listed_name)

        @functools.wraps(listed)
        def wrapper(value):
            return value

        assert (wrapper.__name__, wrapper.__module__) == (listed.__name__, listed.__module__)
        env = app_module.app.jinja_env
        env.filters["planted_look_alike"] = wrapper
        try:
            unlisted = _runtime_unlisted_template_helpers(env, baseline, app_module)
        finally:
            del env.filters["planted_look_alike"]
        assert len(unlisted) == 1 and unlisted[0].startswith("filters:planted_look_alike"), unlisted

    def test_the_listed_function_itself_registered_under_another_name_is_not_reported(
        self, running_app
    ):
        app_module, baseline = running_app
        env = app_module.app.jinja_env
        env.filters["tenure_text_again"] = app_module._tenure_text
        try:
            unlisted = _runtime_unlisted_template_helpers(env, baseline, app_module)
        finally:
            del env.filters["tenure_text_again"]
        assert unlisted == []

    def test_a_lambda_a_builtin_and_a_function_named_like_a_listed_one_are_reported(
        self, running_app
    ):
        app_module, baseline = running_app

        def _tenure_text(value):  # the listed name, defined outside the app module
            return value

        planted = {
            "planted_lambda": lambda value: value,
            "planted_builtin": len,
            "planted_lookalike": _tenure_text,
            "tojson": _tenure_text,  # a name the framework owns, re-bound to a function
        }
        env = app_module.app.jinja_env
        saved = env.filters.get("tojson")
        env.filters.update(planted)
        try:
            unlisted = _runtime_unlisted_template_helpers(env, baseline, app_module)
        finally:
            for name in planted:
                env.filters.pop(name, None)
            if saved is not None:
                env.filters["tojson"] = saved
        assert sorted(item.split(" ")[0] for item in unlisted) == sorted(
            f"filters:{name}" for name in planted
        ), unlisted

    def test_the_shared_time_formatter_is_registered_as_the_function_itself(self, running_app):
        """Premise of the identity exception: the app hands the template analytics' own
        function, not a wrapper in app.py, so the exception names exactly one object."""
        import analytics  # noqa: PLC0415

        app_module, _ = running_app
        assert app_module.app.jinja_env.filters["et_stamp"] is analytics.format_et_stamp

    def test_the_shared_time_formatter_under_another_name_is_not_reported(self, running_app):
        import analytics  # noqa: PLC0415

        app_module, baseline = running_app
        env = app_module.app.jinja_env
        env.filters["et_stamp_again"] = analytics.format_et_stamp
        try:
            unlisted = _runtime_unlisted_template_helpers(env, baseline, app_module)
        finally:
            del env.filters["et_stamp_again"]
        assert unlisted == []

    def test_a_wrapper_of_the_shared_time_formatter_is_reported(self, running_app):
        """A wrapper wears the formatter's name and module through ``functools.wraps`` and can
        do anything; only the formatter itself is accepted."""
        import functools  # noqa: PLC0415

        import analytics  # noqa: PLC0415

        app_module, baseline = running_app

        @functools.wraps(analytics.format_et_stamp)
        def wrapper(value, **kwargs):
            return value

        env = app_module.app.jinja_env
        env.filters["planted_formatter_wrapper"] = wrapper
        try:
            unlisted = _runtime_unlisted_template_helpers(env, baseline, app_module)
        finally:
            del env.filters["planted_formatter_wrapper"]
        assert len(unlisted) == 1 and unlisted[0].startswith("filters:planted_formatter_wrapper"), (
            unlisted
        )

    @pytest.mark.parametrize(
        "table, name",
        [(table, "planted_analytics_helper") for table in sorted(_TEMPLATE_REGISTRATION_TABLES)],
    )
    def test_any_other_function_of_analytics_is_reported_in_every_table(
        self, running_app, table, name
    ):
        """The exception is one object, not the module: a different analytics function
        (here its dollar formatter) added to any table is reported like any other helper."""
        import analytics  # noqa: PLC0415

        app_module, baseline = running_app
        env = app_module.app.jinja_env
        other = analytics.format_dollar_saved
        assert other is not analytics.format_et_stamp, "premise: a different function"
        getattr(env, table)[name] = other
        try:
            unlisted = _runtime_unlisted_template_helpers(env, baseline, app_module)
        finally:
            del getattr(env, table)[name]
        assert len(unlisted) == 1 and unlisted[0].startswith(f"{table}:{name}"), unlisted


class TestEveryContextProcessorOfTheRunningAppIsAnEntryPointOfTheScan:
    """The helpers a template reaches also come through context processors. The runtime guard
    compares the app's processors with a fresh Flask application's, whatever spelling added
    them."""

    @pytest.fixture()
    def pair(self, running_app):
        app_module, _ = running_app
        return app_module, _fresh_flask_app()

    def test_the_real_app_has_only_the_framework_processors_and_listed_ones(self, pair):
        app_module, baseline = pair
        assert _runtime_unlisted_context_processors(app_module.app, baseline, app_module) == []

    def test_the_comparison_is_not_vacuous(self, pair):
        """The framework's own processor is in both lists, so it is excluded by what it is."""
        app_module, baseline = pair
        assert baseline.template_context_processors[None], "premise: Flask adds a processor"
        assert all(
            any(p is q for q in app_module.app.template_context_processors[None])
            for p in baseline.template_context_processors[None]
        )

    @pytest.mark.parametrize("how", ["alias append", "extend", "blueprint list", "insert"])
    def test_a_processor_added_by_any_spelling_is_reported(self, pair, how):
        app_module, baseline = pair
        flask_app = app_module.app

        def planted_processor():
            return {"planted": len}

        table = flask_app.template_context_processors
        key = None
        try:
            if how == "alias append":
                alias = table[None]
                alias.append(planted_processor)
            elif how == "extend":
                table[None].extend([planted_processor])
            elif how == "blueprint list":
                key = "planted_blueprint"
                table[key] = [planted_processor]
            else:
                table[None].insert(0, planted_processor)
            unlisted = _runtime_unlisted_context_processors(flask_app, baseline, app_module)
        finally:
            if key is not None:
                table.pop(key, None)
            while planted_processor in table[None]:
                table[None].remove(planted_processor)
        assert len(unlisted) == 1 and "planted_processor" in unlisted[0], unlisted
        assert _runtime_unlisted_context_processors(flask_app, baseline, app_module) == []

    @pytest.mark.parametrize("kind", ["lambda", "nested function with a listed name", "wraps"])
    def test_a_processor_that_is_not_a_listed_function_is_reported(self, pair, kind):
        import functools  # noqa: PLC0415

        app_module, baseline = pair
        listed = app_module._tenure_text
        if kind == "lambda":
            planted = lambda: {}  # noqa: E731
        elif kind == "wraps":

            @functools.wraps(listed)
            def planted():
                return {}

        else:

            def planted():
                return {}

            planted.__name__ = "_tenure_text"
            planted.__module__ = app_module.__name__
        table = app_module.app.template_context_processors
        table[None].append(planted)
        try:
            unlisted = _runtime_unlisted_context_processors(app_module.app, baseline, app_module)
        finally:
            table[None].remove(planted)
        assert len(unlisted) == 1 and unlisted[0].startswith("context_processors:None"), unlisted


_TABLE_READ_FORMS = {
    "globals alias": "_planted_table = app.jinja_env.globals",
    "filters store": "app.jinja_env.filters['planted'] = perform_account_liquidation",
    "tests update": "app.jinja_env.tests.update(planted=perform_account_liquidation)",
    "globals through the current app": "current_app.jinja_env.globals['planted'] = len",
    "filters through flask": "flask.current_app.jinja_env.filters.update(planted=len)",
    "processor list alias": "_planted_list = app.template_context_processors[None]",
    "processor append": "app.template_context_processors[None].append(planted_processor)",
    "processor list of another object": "current_app.template_context_processors.clear()",
    "getattr of a table": "getattr(app.jinja_env, 'filters')['planted'] = len",
    "setattr of a table": "setattr(app.jinja_env, 'globals', planted_table)",
    "getattr of the processor list": "getattr(app, 'template_context_processors')",
}


class TestTheStaticScanReportsATemplateTableReadInsideAFunction:
    """The runtime guard reads the tables once, after the import. A helper stored from inside
    a function (at request time) is stored later, so the scan reads the function bodies of
    app.py for a read of the tables."""

    def test_the_real_app_reads_no_template_table_inside_a_function(self):
        assert _template_table_reads_inside_functions(_parse_app_py()) == []

    @pytest.mark.parametrize("form", list(_TABLE_READ_FORMS))
    @pytest.mark.parametrize(
        "planted_in", ["ai_advisor_tab", "_tenure_review_job", "_format_eastern_run_time"]
    )
    def test_a_read_planted_in_a_function_body_is_reported(self, planted_in, form):
        tree = _tree_with_planted_call(planted_in, _TABLE_READ_FORMS[form])

        found = _template_table_reads_inside_functions(tree)

        assert any(hit.startswith(f"{planted_in}:") for hit in found), (found, form)

    def test_a_read_planted_in_a_nested_function_or_a_lambda_is_reported(self):
        tree = _tree_with_planted_call(
            "ai_advisor_tab",
            "def _inner():\n    return app.jinja_env.globals\n",
        )
        assert any(
            ":" in hit and "_inner" in hit for hit in _template_table_reads_inside_functions(tree)
        )
        tree = _tree_with_planted_call(
            "ai_advisor_tab", "_hook = lambda: app.jinja_env.filters.update(planted=len)"
        )
        assert any("<lambda>" in hit for hit in _template_table_reads_inside_functions(tree))

    def test_a_read_at_module_level_is_an_import_time_registration_and_is_not_reported(self):
        tree = _parse_app_py()
        tree.body.extend(ast.parse("app.jinja_env.filters['planted'] = len").body)
        ast.fix_missing_locations(tree)
        assert _template_table_reads_inside_functions(tree) == []

    @pytest.mark.parametrize(
        "statement",
        [
            "planted_cache.filters = {}",
            "planted_settings.tests.append(1)",
            "planted_module.globals()",
            "getattr(planted_object, 'filters')",
        ],
    )
    def test_an_attribute_of_something_else_with_the_same_name_is_not_reported(self, statement):
        tree = _tree_with_planted_call("ai_advisor_tab", statement)
        assert _template_table_reads_inside_functions(tree) == []


# ===========================================================================
# Group F (Cycle 2c): ai_advisor_tab()'s retirement name-resolution block
# (AC-1) -- a SCOPED SOURCE-WINDOW scan, not the Group E transitive-walk
# pattern.
# ===========================================================================


def _ai_advisor_tab_retirement_block_source() -> str:
    """Extract ONLY the retirement-panel prefetch/name-resolution/checklist
    block inside ai_advisor_tab() -- from its own opening comment marker to
    the Frontrunner Builder panel's opening comment marker that immediately
    follows it in app.py (verified adjacent by direct inspection; a
    misplacement would make this helper fail loudly via the assertions
    below rather than silently under/over-scope).

    Why a text window instead of Group E's AST transitive walk: an earlier
    attempt to add ai_advisor_tab() as a 4th Group E entry point FAILED --
    the whole function's transitive closure pulls in _build_meta, a shared
    page-header helper called by nearly every route in the app, which
    legitimately does `env_vars.get('LIVE_EXECUTION', ...)` to render a
    read-only "live mode" display badge -- entirely unrelated to retirement,
    and already outside this feature's blast radius. A scoped window keyed
    to the retirement block's own comment markers gives a STRUCTURAL,
    false-positive-free regression guard for the actual new code (AC-1's
    name-resolution + the pre-existing AC-10/AC-6 checklist glue it sits
    beside) without pulling in the rest of this large, multi-purpose render
    function.
    """
    source = _app_py_source()
    start_marker = "# Retirement Recommendations panel (Cycle 2a, AC-10): prefetch the"
    end_marker = "# Frontrunner Builder panel: prefetch pending frontrunner_proposals"
    start_idx = source.find(start_marker)
    if start_idx == -1:
        pytest.fail(
            f"app.py: start marker {start_marker!r} not found -- has the retirement "
            "panel prefetch block's leading comment been reworded? Update this "
            "helper's marker to match."
        )
    end_idx = source.find(end_marker, start_idx)
    if end_idx == -1:
        pytest.fail(
            f"app.py: end marker {end_marker!r} not found after the retirement block "
            "start -- has the Frontrunner Builder panel been reordered/reworded, or "
            "does the retirement block no longer end where this test assumes?"
        )
    return source[start_idx:end_idx]


class TestAiAdvisorTabRetirementBlockNeverReachesExecSeam:
    """GREEN-ON-ARRIVAL, scoped source-window sibling to Group E -- see the
    Group E section comment above for why ai_advisor_tab() as a whole isn't
    scanned via the transitive-walk pattern."""

    def test_block_is_non_trivially_sized(self):
        """Non-vacuity guard: the extracted window must be substantial
        (covers the real AC-1 resolution code, not an empty/near-empty
        string from a marker landing right next to its own end)."""
        block = _ai_advisor_tab_retirement_block_source()
        assert len(block) > 500, (
            f"The extracted retirement block is suspiciously small ({len(block)} "
            "chars) -- the markers may have matched the wrong location."
        )

    def test_block_actually_contains_the_ac1_resolution_code(self):
        """Further non-vacuity: prove the window genuinely captured AC-1's
        real resolution logic, not just its own leading comment."""
        block = _ai_advisor_tab_retirement_block_source()
        assert "retirement_recommendations" in block
        assert "load_state" in block, (
            "Expected the retirement block to reference load_state() (AC-1's "
            "bot_state resolution) -- the window may be mis-scoped."
        )

    def test_block_never_references_composer_draft_client(self):
        block = _ai_advisor_tab_retirement_block_source()
        assert "composer_draft_client" not in block, (
            "ai_advisor_tab()'s retirement block references composer_draft_client -- "
            "the Overview-tab render path must never create a real Composer symphony."
        )

    def test_block_never_references_save_symphony(self):
        block = _ai_advisor_tab_retirement_block_source()
        assert _SAVE_SYMPHONY_ATTR not in block, (
            "ai_advisor_tab()'s retirement block references save_symphony."
        )

    def test_block_never_references_alpha_bot_execution(self):
        block = _ai_advisor_tab_retirement_block_source()
        assert "alpha_bot_execution" not in block, (
            "ai_advisor_tab()'s retirement block references alpha_bot_execution."
        )

    def test_block_never_reads_or_writes_live_execution(self):
        block = _ai_advisor_tab_retirement_block_source()
        assert "LIVE_EXECUTION" not in block, (
            "ai_advisor_tab()'s retirement block references LIVE_EXECUTION -- this "
            "block must be a pure read-only name-resolution/checklist-assembly path, "
            "not a live-mode-conditional one."
        )

    def test_block_contains_the_cycle_2e_last_run_prefetch(self):
        """Cycle 2e non-vacuity: the new last-run prefetch must live INSIDE
        this scanned window (between the two marker comments), otherwise the
        exec-seam scan above would not cover it."""
        block = _ai_advisor_tab_retirement_block_source()
        assert _RETIREMENT_LAST_RUN_FETCH_FN_NAME in block, (
            f"ai_advisor_tab()'s retirement block must call {_RETIREMENT_LAST_RUN_FETCH_FN_NAME}() "
            "(the Cycle 2e last-run block's data source) between the two Group F marker comments."
        )


# ===========================================================================
# Group G (Cycle 2e, AC-8): advisors/composer_series_client.py -- the NEW
# read-only Composer client. A pure scan over source text so it can be (and
# is) exercised against synthetic mutated sources, proving each rule can fail.
# ===========================================================================

_SERIES_CLIENT_REL_PATH = "advisors/composer_series_client.py"

# Plan AC-8's explicit token list, plus this file's established URL-fragment
# set and the draft/exec symbols -- all forbidden in EXECUTABLE code.
_SERIES_CLIENT_FORBIDDEN_FRAGMENTS = (
    "/deploy/",
    "/trading/",
    "go-to-cash",
    "execute_sell_to_cash",
    "LIVE_EXECUTION",
    "composer_draft_client",
    "save_symphony",
    "invest_in_symphony",
    "/invest",
    "/sell",
    "/liquidate",
)

# Precedent: advisors/composer_backtest_client.py imports exactly this pair
# from alpha_bot_execution and nothing else.
_SERIES_CLIENT_ALLOWED_EXEC_IMPORTS = frozenset({"COMPOSER_BASE_URL", "get_composer_headers"})

# Attributes the client may touch on the `requests` module: the GET verb and
# exception types for its retry classification. Anything else (post, put,
# Session, request, ...) is a violation.
_SERIES_CLIENT_ALLOWED_REQUESTS_ATTRS = frozenset(
    {
        "get",
        "exceptions",
        "RequestException",
        "Timeout",
        "ReadTimeout",
        "ConnectTimeout",
        "ConnectionError",
        "HTTPError",
    }
)

# Mutating HTTP verbs on ANY receiver (session.post, client.put, ...).
_MUTATING_VERB_ATTRS = frozenset({"post", "put", "patch", "delete", "request", "send"})

# Other HTTP stacks the client has no business importing.
_OTHER_HTTP_MODULES = ("httpx", "urllib", "urllib3", "aiohttp", "http", "socket", "websocket")


def _executable_text(source: str) -> str:
    """Docstrings and full-line comments blanked (same rationale as this
    file's _read_executable_source, but over a source STRING)."""
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                const = body[0].value
                if isinstance(const.value, str):
                    for lineno in range(const.lineno, (const.end_lineno or const.lineno) + 1):
                        if 1 <= lineno <= len(lines):
                            lines[lineno - 1] = ""
    return "\n".join("" if ln.lstrip().startswith("#") else ln for ln in lines)


def _series_client_violations(source: str) -> list[str]:
    """Every no-trade-boundary violation in `source` (empty list = clean)."""
    found: list[str] = []
    executable = _executable_text(source)
    for fragment in _SERIES_CLIENT_FORBIDDEN_FRAGMENTS:
        if fragment in executable:
            found.append(f"forbidden fragment {fragment!r}")

    if "import *" in source:
        found.append("wildcard import")

    tree = ast.parse(source)
    # `import requests as r` -> the attribute scan below must follow the alias.
    requests_names = {"requests"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "requests" and alias.asname:
                    requests_names.add(alias.asname)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root == "alpha_bot_execution":
                    found.append("whole-module import of alpha_bot_execution")
                if root in _OTHER_HTTP_MODULES:
                    found.append(f"imports non-requests HTTP stack {alias.name!r}")
                if alias.name.startswith("requests.") and not (
                    alias.name == "requests.exceptions"
                    or alias.name.startswith("requests.exceptions.")
                ):
                    found.append(f"imports requests submodule {alias.name!r}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            root = module.split(".")[0]
            if root == "alpha_bot_execution":
                extra = {a.name for a in node.names} - _SERIES_CLIENT_ALLOWED_EXEC_IMPORTS
                if extra:
                    found.append(f"imports {sorted(extra)} from alpha_bot_execution")
            if root in _OTHER_HTTP_MODULES:
                found.append(f"imports from non-requests HTTP stack {module!r}")
            if root == "requests":
                # `from requests import post` / `Session` would dodge the requests.<attr>
                # scan below, so the imported NAMES are checked at the import site.
                if module == "requests":
                    extra = {a.name for a in node.names} - _SERIES_CLIENT_ALLOWED_REQUESTS_ATTRS
                    if extra:
                        found.append(f"imports {sorted(extra)} from requests")
                elif module != "requests.exceptions" and not module.startswith(
                    "requests.exceptions."
                ):
                    found.append(f"imports from requests submodule {module!r}")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in requests_names:
                if node.attr not in _SERIES_CLIENT_ALLOWED_REQUESTS_ATTRS:
                    found.append(f"uses requests.{node.attr}")
            if node.attr in _MUTATING_VERB_ATTRS:
                found.append(f"references a mutating HTTP verb .{node.attr}")
    return found


def _calls_requests_get(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "requests"
        ):
            return True
    return False


_CLEAN_CLIENT_SOURCE = '''"""Docstring may say: never touches /trading/ or save_symphony."""
import time
import requests
from alpha_bot_execution import COMPOSER_BASE_URL, get_composer_headers


def fetch(account_id, symphony_id):
    # comments may mention go-to-cash too
    url = f"{COMPOSER_BASE_URL}/portfolio/accounts/{account_id}/symphonies/{symphony_id}"
    try:
        return requests.get(url, headers=get_composer_headers(), timeout=5)
    except requests.exceptions.Timeout:
        time.sleep(1)
'''


def _violations_after_appending(extra: str) -> list[str]:
    """Scan the clean reference source with `extra` appended AFTER it. (Appending,
    never prepending: text placed before the reference's leading docstring would
    demote that docstring to executable code and trip the fragment rule for the
    wrong reason.)"""
    return _series_client_violations(_CLEAN_CLIENT_SOURCE + "\n" + extra + "\n")


def _assert_detected(extra: str, expected_substring: str) -> None:
    violations = _violations_after_appending(extra)
    assert any(expected_substring in v for v in violations), (
        f"expected a violation mentioning {expected_substring!r}; got {violations}"
    )


class TestSeriesClientScanIsNonVacuous:
    """Each rule of the Group G scan must demonstrably fire on a mutated
    source -- otherwise a green scan of the real module would prove nothing.
    Every case asserts the SPECIFIC rule that fired, not just 'something'."""

    def test_the_clean_reference_source_has_no_violations(self):
        assert _series_client_violations(_CLEAN_CLIENT_SOURCE) == []
        assert _calls_requests_get(_CLEAN_CLIENT_SOURCE)

    @pytest.mark.parametrize("fragment", _SERIES_CLIENT_FORBIDDEN_FRAGMENTS)
    def test_each_forbidden_fragment_in_executable_code_is_detected(self, fragment):
        _assert_detected(f"def sneaky():\n    return {fragment!r}\n", fragment)

    def test_a_post_call_is_detected(self):
        _assert_detected("def sneaky(u):\n    return requests.post(u, json={})\n", "requests.post")

    def test_a_mutating_verb_on_any_receiver_is_detected(self):
        _assert_detected("def sneaky(s, u):\n    return s.put(u)\n", ".put")

    def test_a_requests_session_is_detected(self):
        _assert_detected("def sneaky():\n    return requests.Session()\n", "requests.Session")

    def test_from_requests_import_a_mutating_verb_is_detected(self):
        """`from requests import post` then `post(u)` evades the requests.<attr> scan."""
        _assert_detected(
            "from requests import post\n\ndef sneaky(u):\n    return post(u)\n", "post"
        )

    def test_from_requests_import_session_is_detected(self):
        _assert_detected("from requests import Session\n", "Session")

    def test_from_a_requests_submodule_import_is_detected(self):
        _assert_detected("from requests.sessions import Session\n", "requests.sessions")
        _assert_detected("import requests.sessions\n", "requests.sessions")

    def test_an_aliased_requests_module_is_followed(self):
        _assert_detected(
            "import requests as r\n\ndef sneaky():\n    return r.Session()\n", "Session"
        )

    def test_allowed_requests_imports_are_not_violations(self):
        ok = (
            "from requests import get\n"
            "from requests.exceptions import Timeout, ConnectionError\n"
            "import requests.exceptions\n"
        )
        assert _violations_after_appending(ok) == []

    def test_another_http_stack_is_detected(self):
        _assert_detected("import httpx\n", "httpx")
        _assert_detected("from urllib.request import urlopen\n", "urllib")

    def test_a_whole_module_alpha_bot_execution_import_is_detected(self):
        _assert_detected(
            "import alpha_bot_execution\n", "whole-module import of alpha_bot_execution"
        )

    def test_importing_an_execution_function_from_alpha_bot_execution_is_detected(self):
        _assert_detected(
            "from alpha_bot_execution import execute_sell_to_cash\n", "execute_sell_to_cash"
        )

    def test_a_wildcard_import_is_detected(self):
        _assert_detected("from os import *\n", "wildcard")

    def test_prose_in_docstrings_and_comments_is_not_a_violation(self):
        # _CLEAN_CLIENT_SOURCE already carries /trading/, save_symphony and go-to-cash in
        # its docstring and a comment; the clean-source test above proves they are ignored.
        assert _series_client_violations(_CLEAN_CLIENT_SOURCE) == []


class TestComposerSeriesClientNoTradeBoundary:
    def _source(self) -> str:
        return _read_source(_SERIES_CLIENT_REL_PATH)

    def test_module_exists(self):
        assert (REPO_ROOT / _SERIES_CLIENT_REL_PATH).exists(), (
            f"{_SERIES_CLIENT_REL_PATH} does not exist -- its no-trade boundary is unverifiable"
        )

    def test_module_has_no_trade_deploy_or_draft_primitive_and_is_get_only(self):
        violations = _series_client_violations(self._source())
        assert violations == [], (
            f"{_SERIES_CLIENT_REL_PATH} violates the no-trade boundary: {violations}"
        )

    def test_module_actually_issues_a_requests_get(self):
        """Non-vacuity for the GET-only rule: the module must really call
        requests.get (so 'only GET' is a statement about real traffic)."""
        assert _calls_requests_get(self._source())

    def test_module_exposes_no_trade_shaped_public_symbols(self):
        import importlib

        mod = importlib.import_module("advisors.composer_series_client")
        offending = [
            n
            for n in dir(mod)
            if not n.startswith("_")
            and any(word in n.lower().split("_") for word in _FORBIDDEN_ACTION_WORDS)
        ]
        assert offending == [], f"trade-shaped public symbols: {offending}"

    def test_module_never_imports_the_llm_seam(self):
        source = self._source()
        assert "ai_advisor" not in source
        assert "anthropic" not in source.lower()


# ===========================================================================
# Group H (Cycle 2e, AC-7b): the market-hours gate belongs to the Run-now ROUTE
# only. The 03:45 scheduled tick (always outside the session) is never gated.
# ===========================================================================

_MARKET_STATE_FN_NAME = "get_market_state"


def _thread_target_name(tree: ast.Module, fn_name: str) -> str | None:
    """The thread body named by the `target=` of a `*.Thread(...)` call inside `fn_name`: a bare
    name, or the bare-name function a no-argument lambda calls (`target=lambda: body(owner)`, how the
    nightly tick binds the review slot it claimed into a target the scheduler calls as `target()`)."""
    fn_node = _find_function_def(tree, fn_name)
    if fn_node is None:
        return None
    for node in ast.walk(fn_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "Thread"
        ):
            for kw in node.keywords:
                if kw.arg != "target":
                    continue
                if isinstance(kw.value, ast.Name):
                    return kw.value.id
                if (
                    isinstance(kw.value, ast.Lambda)
                    and isinstance(kw.value.body, ast.Call)
                    and isinstance(kw.value.body.func, ast.Name)
                ):
                    return kw.value.body.func.id
    return None


class TestMarketGateScope:
    def test_the_run_now_route_consults_the_market_state(self):
        tree = _parse_app_py()
        node = _find_function_def(tree, _RETIREMENT_RUN_ROUTE_FN_NAME)
        assert node is not None
        assert _references_name_or_attr(node, _MARKET_STATE_FN_NAME), (
            f"{_RETIREMENT_RUN_ROUTE_FN_NAME} must call {_MARKET_STATE_FN_NAME}() (AC-7b gate)"
        )

    def test_the_scheduled_tick_thread_target_is_found_and_reaches_the_worker(self):
        """Non-vacuity for the scan below."""
        tree = _parse_app_py()
        target = _thread_target_name(tree, _RETIREMENT_TICK_WRAPPER_FN_NAME)
        assert target, (
            f"{_RETIREMENT_TICK_WRAPPER_FN_NAME} must start a thread with a bare-name target"
        )
        visited = {
            getattr(n, "name", None) for n in _collect_transitive_local_call_subtrees(tree, target)
        }
        assert _RETIREMENT_TICK_WORKER_FN_NAME in visited, (
            f"the scheduled tick's thread body {target!r} must reach the shared worker; got {visited}"
        )

    def test_the_scheduled_tick_path_never_references_the_market_state(self):
        tree = _parse_app_py()
        target = _thread_target_name(tree, _RETIREMENT_TICK_WRAPPER_FN_NAME)
        assert target
        for subtree in _collect_transitive_local_call_subtrees(tree, target):
            assert not _references_name_or_attr(subtree, _MARKET_STATE_FN_NAME), (
                f"{getattr(subtree, 'name', '?')} (reachable from the scheduled tick) references "
                f"{_MARKET_STATE_FN_NAME}: the 03:45 tick must never be market-gated"
            )
        wrapper = _find_function_def(tree, _RETIREMENT_TICK_WRAPPER_FN_NAME)
        assert not _references_name_or_attr(wrapper, _MARKET_STATE_FN_NAME)

    def test_the_tenure_review_route_consults_the_market_state(self):
        tree = _parse_app_py()
        node = _find_function_def(tree, _TENURE_REVIEW_RUN_ROUTE_FN_NAME)
        assert node is not None
        assert _references_name_or_attr(node, _MARKET_STATE_FN_NAME), (
            f"{_TENURE_REVIEW_RUN_ROUTE_FN_NAME} must call {_MARKET_STATE_FN_NAME}() "
            "(the on-demand review is refused while the market is open)"
        )

    def test_the_route_check_fails_on_a_route_without_the_reference(self):
        """Non-vacuity for the check above: a route that never asks for the market state."""
        route = _TENURE_REVIEW_RUN_ROUTE_FN_NAME
        gate = _MARKET_STATE_FN_NAME
        without = ast.parse(f"def {route}(): return 202")
        with_it = ast.parse(f"def {route}(): return 409 if {gate}(now) == 'open' else 202")
        assert not _references_name_or_attr(
            _find_function_def(without, _TENURE_REVIEW_RUN_ROUTE_FN_NAME), _MARKET_STATE_FN_NAME
        )
        assert _references_name_or_attr(
            _find_function_def(with_it, _TENURE_REVIEW_RUN_ROUTE_FN_NAME), _MARKET_STATE_FN_NAME
        )

    def test_a_market_check_planted_in_the_review_job_is_found_on_the_tick_path(self):
        """The check belongs in the route. Put in the job the tick also runs, it would gate the
        03:45 review; the tick-path scan has to see it there."""
        tree = _parse_app_py()
        target = _thread_target_name(tree, _RETIREMENT_TICK_WRAPPER_FN_NAME)
        assert target

        def tick_path_hits(candidate: ast.Module) -> list[str]:
            return [
                getattr(subtree, "name", "?")
                for subtree in _collect_transitive_local_call_subtrees(candidate, target)
                if _references_name_or_attr(subtree, _MARKET_STATE_FN_NAME)
            ]

        assert tick_path_hits(tree) == [], "premise: the tick path is clean"
        planted = copy.deepcopy(tree)
        job = _find_function_def(planted, _TENURE_REVIEW_JOB_FN_NAME)
        assert job is not None
        job.body.insert(0, ast.parse(f"{_MARKET_STATE_FN_NAME}(None)").body[0])
        assert _TENURE_REVIEW_JOB_FN_NAME in tick_path_hits(planted)


# ===========================================================================
# Group I (Cycle 2e, ret5-doc finding): name-pattern SWEEP over every
# module-level app.py function with "retirement" in its name. Groups E/F cover
# explicit entry points + their call closure, which let three new Cycle-2e
# helpers through (two thread bodies reachable only via Thread(target=...), and
# a helper called from ai_advisor_tab(), which is deliberately not an entry
# point). This gap CLASS has now recurred three times, so instead of one more
# hand-maintained name list the sweep covers whatever retirement function exists
# -- present or future -- by name pattern.
# ===========================================================================


def _retirement_function_names_in_app() -> list[str]:
    tree = _parse_app_py()
    return sorted(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "retirement" in node.name
    )


_ALL_RETIREMENT_FN_NAMES = _retirement_function_names_in_app()

# Known members the sweep must contain (non-vacuity: guards the name pattern itself).
_SWEEP_MUST_INCLUDE = (
    "_retirement_recommender_tick_worker",
    "_run_retirement_recommender_tick",
    "_fetch_retirement_last_run",
    "_fetch_retirement_recommendations",
    "_dispatch_retirement_decision",
    "ai_advisor_retirement_run",
    "ai_advisor_retirement_approve",
    "ai_advisor_retirement_reject",
    "api_retirement_recommendations",
)


class TestEveryRetirementFunctionNeverReachesExecSeam:
    def test_the_sweep_is_non_vacuous(self):
        assert len(_ALL_RETIREMENT_FN_NAMES) >= len(_SWEEP_MUST_INCLUDE)
        missing = [n for n in _SWEEP_MUST_INCLUDE if n not in _ALL_RETIREMENT_FN_NAMES]
        assert missing == [], f"the retirement name sweep lost known functions: {missing}"

    def test_thread_bodies_are_followed_by_the_group_e_walker(self):
        """The walker fix itself: the run-now route and the scheduled-tick wrapper
        must reach their thread bodies and, through them, the shared worker."""
        tree = _parse_app_py()
        for entry in (_RETIREMENT_RUN_ROUTE_FN_NAME, _RETIREMENT_TICK_WRAPPER_FN_NAME):
            target = _thread_target_name(tree, entry)
            assert target, f"{entry} must start a thread with a bare-name target"
            visited = {
                getattr(n, "name", None)
                for n in _collect_transitive_local_call_subtrees(tree, entry)
            }
            assert target in visited, (
                f"the walk from {entry} never reached its thread body {target}"
            )
            assert _RETIREMENT_TICK_WORKER_FN_NAME in visited

    @pytest.mark.parametrize("fn_name", _ALL_RETIREMENT_FN_NAMES)
    def test_function_references_no_exec_or_trade_primitive(self, fn_name):
        tree = _parse_app_py()
        node = _find_function_def(tree, fn_name)
        assert node is not None
        source_segment = _unparse_without_docstrings(node)
        for token in ("composer_draft_client", "alpha_bot_execution", "LIVE_EXECUTION"):
            assert token not in source_segment, f"{fn_name} references {token}"
        assert not _references_name_or_attr(node, _SAVE_SYMPHONY_ATTR), (
            f"{fn_name} references {_SAVE_SYMPHONY_ATTR}"
        )


# ===========================================================================
# Group J (dashboard refresh, the panels the poll carries): the read that gives
# ``/api/state`` its Sleeves rows, CVaR diagnostic and locked-variables count.
# The Sleeves rows come from ``sleeves.ledger`` and ``sleeves.rules.limits``,
# the package that holds the Managed Sleeves order path; ``limits`` also holds
# ``_post_bench_note``, which posts to a webhook, and ``sleeves.alpaca_orders``
# holds the order functions. This read must reach none of them, and each check
# below is shown to fail on a planted call.
# ===========================================================================

_PANELS_BUILDER_FN_NAME = "_dashboard_panels_payload"
_PANELS_ENTRY_POINT_FN_NAMES = (_PANELS_BUILDER_FN_NAME, "get_state")
# Names this read must never reference, beyond Group E's set (orders, symphony creation, the
# live flag): cancelling an order, and the bench note's webhook post.
_PANELS_FORBIDDEN_EXTRA_NAMES = ("cancel_order", "_post_bench_note")
_SLEEVES_LEDGER_REL_PATH = "sleeves/ledger.py"
_SLEEVES_LIMITS_REL_PATH = "sleeves/rules/limits.py"
_BENCH_READ_FN_NAME = "is_rule_benched"
_ORDER_MODULE_NAME = "alpaca_orders"


# The state route and the meta it builds already read the live flag's value to report the mode; that
# read is a display of the flag, not a write, and exists apart from the panels. The state route's
# walk is held to exactly these two hits, so any other reference reachable from it is reported.
_STATE_ROUTE_BASE_HITS = frozenset(
    {"LIVE_EXECUTION via get_state", "LIVE_EXECUTION via _build_meta"}
)


def _panels_hits(tree: ast.Module, fn_name: str) -> list[str]:
    """Group E's hits for the call graph of ``fn_name``, plus the extra names above."""
    hits = _exec_seam_hits(tree, fn_name)
    for node in _collect_transitive_local_call_subtrees(tree, fn_name):
        for name in _PANELS_FORBIDDEN_EXTRA_NAMES:
            if _references_name_or_attr(node, name) or name in _text_of(node):
                hits.append(f"{name} via {getattr(node, 'name', '?')}")
    if fn_name == "get_state":
        hits = [h for h in hits if h not in _STATE_ROUTE_BASE_HITS]
    return hits


def _imported_module_names(tree: ast.Module) -> set[str]:
    """Every module a tree imports, at any depth, whole dotted names and their last parts."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.update({alias.name, *alias.name.split(".")})
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.update({module, *module.split(".")})
            names.update(alias.name for alias in node.names)
    return names


class TestThePanelsReadNeverReachesAnOrderOrACreation:
    def test_the_builder_exists_and_the_state_route_reaches_it(self):
        tree = _parse_app_py()
        assert _find_function_def(tree, _PANELS_BUILDER_FN_NAME) is not None, (
            f"app.py has no function named {_PANELS_BUILDER_FN_NAME!r}: the poll's panels have no builder"
        )
        reached = {
            getattr(n, "name", None)
            for n in _collect_transitive_local_call_subtrees(tree, "get_state")
        }
        assert _PANELS_BUILDER_FN_NAME in reached, "get_state never calls the panels builder"

    @pytest.mark.parametrize("fn_name", _PANELS_ENTRY_POINT_FN_NAMES)
    def test_the_call_graph_reaches_no_order_creation_or_live_flag(self, fn_name):
        tree = _parse_app_py()
        assert _find_function_def(tree, fn_name) is not None, f"app.py has no {fn_name!r}"
        assert _panels_hits(tree, fn_name) == []

    def test_the_ledger_module_imports_no_order_module(self):
        tree = ast.parse(_read_source(_SLEEVES_LEDGER_REL_PATH))
        assert _ORDER_MODULE_NAME not in _imported_module_names(tree)

    def test_the_bench_read_never_reaches_the_bench_note_or_an_http_write(self):
        tree = ast.parse(_read_source(_SLEEVES_LIMITS_REL_PATH))
        subtrees = _collect_transitive_local_call_subtrees(tree, _BENCH_READ_FN_NAME)
        reached = {getattr(n, "name", None) for n in subtrees}
        assert "_post_bench_note" not in reached
        assert _hits_http_writes(subtrees) == []


_PANELS_PLANTS = {
    "an order cancel": "cancel_order(planted_argument)",
    "an order submit": "submit_order(planted_argument)",
    "a sell to cash": "sell_account(planted_argument)",
    "a symphony creation": "save_symphony(planted_argument)",
    "an attribute symphony creation": "composer_draft_client.save_symphony(planted_argument)",
    "the bench note": "_post_bench_note(planted_argument)",
    "a write of the live flag": "LIVE_EXECUTION = True",
}


class TestThePanelsScanReportsAPlantedCall:
    """Mutation proof: each plant goes into the builder, and the scan must fail on it."""

    @pytest.mark.parametrize("plant", list(_PANELS_PLANTS))
    def test_a_call_planted_in_the_builder_is_reported_from_the_builder_and_from_the_state_route(
        self, plant
    ):
        statement = _PANELS_PLANTS[plant]
        for entry_point in _PANELS_ENTRY_POINT_FN_NAMES:
            tree = _tree_with_planted_call(_PANELS_BUILDER_FN_NAME, statement)
            assert _panels_hits(tree, entry_point), (
                f"{plant} planted in {_PANELS_BUILDER_FN_NAME} was not reported from {entry_point}"
            )

    @pytest.mark.parametrize("plant", [p for p in _PANELS_PLANTS if "live flag" not in p])
    def test_a_call_planted_in_the_state_route_itself_is_reported(self, plant):
        """The scan's own machinery fails on a plant today, whatever the builder's state. (The live
        flag is excluded: the state route already names it to report the mode, so a second
        reference there is not told apart by name.)"""
        tree = _tree_with_planted_call("get_state", _PANELS_PLANTS[plant])
        assert _panels_hits(tree, "get_state"), f"{plant} planted in get_state was not reported"

    def test_an_order_import_planted_in_the_ledger_module_is_reported(self):
        tree = ast.parse(_read_source(_SLEEVES_LEDGER_REL_PATH))
        tree.body.extend(ast.parse("from sleeves import alpaca_orders").body)
        assert _ORDER_MODULE_NAME in _imported_module_names(tree)

    def test_a_bench_note_call_planted_in_the_bench_read_is_reported(self):
        tree = ast.parse(_read_source(_SLEEVES_LIMITS_REL_PATH))
        node = _find_module_level_function(tree, _BENCH_READ_FN_NAME)
        assert node is not None
        node.body.extend(ast.parse("_post_bench_note(rule_id)").body)
        subtrees = _collect_transitive_local_call_subtrees(tree, _BENCH_READ_FN_NAME)
        assert "_post_bench_note" in {getattr(n, "name", None) for n in subtrees}


# ===========================================================================
# The reads this work adds never reach an order, a symphony creation or the live flag
# (header status, the Frontrunner build outcome, the armed-symphonies list)
# ===========================================================================

_HEADER_READ_ENTRY_POINT_FN_NAMES = [
    "header_status",
    "frontrunner_build_last_run",
    "armed_symphonies",
    "health",
]
# `_build_meta` renders the mode word from the live flag it READS; that read is a display of the
# flag, not a write, and exists apart from these routes (the state route's walk allows the same hit).
_HEADER_READ_BASE_HITS = frozenset({"LIVE_EXECUTION via _build_meta"})


def _header_read_hits(tree: ast.Module, fn_name: str) -> list[str]:
    """The panels scan's hits (Group E's set plus an order cancel and the bench note's webhook post)."""
    return [h for h in _panels_hits(tree, fn_name) if h not in _HEADER_READ_BASE_HITS]


class TestTheHeaderAndOutcomeReadsNeverReachAnOrderOrACreation:
    @pytest.mark.parametrize("fn_name", _HEADER_READ_ENTRY_POINT_FN_NAMES)
    def test_the_handler_exists(self, fn_name):
        assert _find_function_def(_parse_app_py(), fn_name) is not None, (
            f"app.py has no function named {fn_name!r}: this read's boundary is unverifiable without it"
        )

    @pytest.mark.parametrize("fn_name", _HEADER_READ_ENTRY_POINT_FN_NAMES)
    def test_the_call_graph_reaches_no_order_creation_or_live_flag_write(self, fn_name):
        tree = _parse_app_py()
        assert _find_function_def(tree, fn_name) is not None, f"app.py has no {fn_name!r}"
        assert _header_read_hits(tree, fn_name) == []

    def test_the_header_read_does_reach_the_health_and_phase_helpers(self):
        """Non-vacuity: the walk is genuinely descending past the handler."""
        tree = _parse_app_py()
        reached = {
            getattr(n, "name", None)
            for n in _collect_transitive_local_call_subtrees(tree, "header_status")
        }
        assert {"_health_status", "_build_meta"} <= reached, f"the walk reached only {reached}"


class TestTheHeaderAndOutcomeReadScanReportsAPlantedCall:
    """Mutation proof: each plant goes into each handler, and the scan must fail on it."""

    @pytest.mark.parametrize("plant", list(_PANELS_PLANTS))
    @pytest.mark.parametrize("fn_name", _HEADER_READ_ENTRY_POINT_FN_NAMES)
    def test_a_call_planted_in_the_handler_is_reported(self, fn_name, plant):
        tree = _tree_with_planted_call(fn_name, _PANELS_PLANTS[plant])
        assert _header_read_hits(tree, fn_name), f"{plant} planted in {fn_name} was not reported"
