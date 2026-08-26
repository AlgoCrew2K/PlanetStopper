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


def _find_function_def(tree: ast.Module, fn_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
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
    test could miss."""

    def _get_handler(self, fn_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
        tree = _parse_app_py()
        node = _find_function_def(tree, fn_name)
        if node is None:
            pytest.fail(
                f"app.py has no function named {fn_name!r} yet -- the retirement "
                "approve/reject route has not been added (pinned name per "
                ".claude/tdd-handoff.md)."
            )
        return node

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_route_handler_never_references_llm_seam(self, fn_name):
        handler = self._get_handler(fn_name)
        assert not _references_name_or_attr(handler, _LLM_SEAM_ATTR), (
            f"{fn_name}'s function body references the LLM client seam "
            f"({_LLM_SEAM_ATTR}) -- approve/reject must be a deterministic status "
            "write only, never touching the LLM."
        )
        assert not _references_name_or_attr(handler, _EXPLAINER_ENTRYPOINT), (
            f"{fn_name}'s function body references {_EXPLAINER_ENTRYPOINT} -- "
            "the explainer must never run on the approve/reject action path "
            "(operator ruling, Gate-2b)."
        )

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_route_handler_never_references_composer_draft_client(self, fn_name):
        handler = self._get_handler(fn_name)
        source_segment = ast.unparse(handler) if hasattr(ast, "unparse") else ""
        assert "composer_draft_client" not in source_segment, (
            f"{fn_name} must never reference composer_draft_client -- retirement "
            "approve is a status-only DB write (AC-5), never a Composer symphony "
            "creation call (that is the frontrunner /proposal/approve route only)."
        )

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_route_handler_never_references_alpha_bot_execution(self, fn_name):
        handler = self._get_handler(fn_name)
        source_segment = ast.unparse(handler) if hasattr(ast, "unparse") else ""
        assert "alpha_bot_execution" not in source_segment, (
            f"{fn_name} must never reference alpha_bot_execution (the live "
            "execution engine)."
        )

    @pytest.mark.parametrize("fn_name", [_APPROVE_ROUTE_FN_NAME, _REJECT_ROUTE_FN_NAME])
    def test_route_handler_never_references_live_execution(self, fn_name):
        handler = self._get_handler(fn_name)
        source_segment = ast.unparse(handler) if hasattr(ast, "unparse") else ""
        assert "LIVE_EXECUTION" not in source_segment, (
            f"{fn_name} must never read/write LIVE_EXECUTION."
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
