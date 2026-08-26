"""RED tests -- Retirement Approval Lifecycle no-auto-trade boundary (AC-8).

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

Expected state: RED until advisors/retirement_explainer.py and
advisors/retirement_checklist.py exist, and until app.py gains the two new
retirement approve/reject routes with the pinned handler names below.
"""

from __future__ import annotations

import ast
import copy
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The two new advisory modules under the extended safety boundary (AC-8).
_NEW_MODULE_REL_PATHS = (
    "advisors/retirement_explainer.py",
    "advisors/retirement_checklist.py",
)

# Pinned in .claude/tdd-handoff.md -- the two new Flask route handler
# function names (mirrors ai_advisor_proposal_approve/_reject's naming
# convention). The static call-graph tests below locate these functions by
# name inside app.py's AST.
_APPROVE_ROUTE_FN_NAME = "ai_advisor_retirement_approve"
_REJECT_ROUTE_FN_NAME = "ai_advisor_retirement_reject"
_APPROVE_ROUTE_PATH = "/ai-advisor/retirement/approve"
_REJECT_ROUTE_PATH = "/ai-advisor/retirement/reject"

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


def _references_name_or_attr(subtree: ast.AST, target: str) -> bool:
    """True iff `target` appears as a bare Name or as the trailing attribute
    of an Attribute chain (e.g. `ai_advisor._build_client` or a
    directly-imported `_build_client`) anywhere in `subtree`."""
    for node in ast.walk(subtree):
        if isinstance(node, ast.Name) and node.id == target:
            return True
        if isinstance(node, ast.Attribute) and node.attr == target:
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


def _collect_transitive_local_call_subtrees(
    tree: ast.Module, fn_name: str, *, _visited: set[str] | None = None
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
    """
    if _visited is None:
        _visited = set()
    if fn_name in _visited:
        return []
    _visited.add(fn_name)

    fn_node = _find_function_def(tree, fn_name)
    if fn_node is None:
        return []

    subtrees: list[ast.AST] = [fn_node]
    for node in ast.walk(fn_node):
        if isinstance(node, ast.Call):
            callee_name = _resolve_bare_name_callee(node)
            if callee_name and callee_name != fn_name and _find_function_def(tree, callee_name):
                subtrees.extend(
                    _collect_transitive_local_call_subtrees(tree, callee_name, _visited=_visited)
                )
    return subtrees


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
