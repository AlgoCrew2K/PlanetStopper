"""RED tests -- Retirement Recommender no-auto-trade boundary (AC-7).

Mirrors tests/security/test_frontrunner_no_trade_boundary.py's structural,
not-policy approach: source-scans the REAL module (not a mocked stand-in) so
the guarantee holds regardless of future refactors inside the module body.

feature-plans/retirement-recommender-core.md AC-7: "The module contains NO
trade / order / liquidation / deploy / LIVE_EXECUTION primitive and never
writes settings. Recommendations persist advisory-only (insert_advisor_
observation forces is_advisory_only=1). Structurally enforced by an
adversarial source-scan test."

Expected state: RED until advisors/retirement_recommender.py exists.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODULE_REL_PATH = "advisors/retirement_recommender.py"

# Trade/order/liquidation/deploy action tokens that must never appear as a
# whole underscore-delimited symbol/word component in this module. Checked
# word-boundary (split on "_"), not bare substring -- so e.g. a hypothetical
# "undeployed" or "deployment_target" identifier would not itself trip this
# (word-boundary discipline mirrors the frontrunner precedent's own
# 'undeployed' vs 'deploy' distinction).
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


def _read_source() -> str:
    path = REPO_ROOT / _MODULE_REL_PATH
    if not path.exists():
        pytest.fail(f"expected module source not found: {_MODULE_REL_PATH}")
    return path.read_text(encoding="utf-8")


def _read_executable_source() -> str:
    """Docstrings/comments stripped via AST so this module's own docstring
    may legitimately DOCUMENT the exclusion in prose (e.g. 'this module never
    calls invest_in_symphony') without tripping the scan. Mirrors
    tests/security/test_frontrunner_no_trade_boundary.py's identical helper."""
    source = _read_source()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        pytest.fail(f"{_MODULE_REL_PATH} has a syntax error -- cannot source-scan")

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


class TestNoTradeSymbols:
    def test_module_exposes_no_trade_shaped_public_symbols(self):
        import advisors.retirement_recommender as rr  # noqa: PLC0415

        public_names = [n for n in dir(rr) if not n.startswith("_")]
        offending = [
            n
            for n in public_names
            if any(word in n.lower().split("_") for word in _FORBIDDEN_ACTION_WORDS)
        ]
        assert offending == [], (
            f"retirement_recommender exposes trade-shaped public symbols: {offending}"
        )

    def test_module_never_constructs_a_trade_url_fragment_in_executable_code(self):
        source = _read_executable_source()
        for fragment in _FORBIDDEN_URL_FRAGMENTS:
            assert fragment not in source, (
                f"retirement_recommender.py contains the '{fragment}' fragment in "
                "EXECUTABLE code -- this module must never reference a trade/deploy "
                "endpoint."
            )

    def test_module_never_imports_alpha_bot_execution(self):
        """Architecture: 'never imports alpha_bot_execution/math_engine trade
        paths'. A source-scan for the module name catches both a direct
        `import alpha_bot_execution` and a `from alpha_bot_execution import ...`."""
        source = _read_source()
        assert "alpha_bot_execution" not in source, (
            "retirement_recommender.py must never import alpha_bot_execution "
            "(the live execution engine) -- this is a purely advisory, "
            "off-execution-path module."
        )

    def test_module_never_calls_invest_in_symphony_or_composer_draft_client(self):
        source = _read_executable_source()
        assert "invest_in_symphony" not in source
        assert "composer_draft_client" not in source, (
            "retirement_recommender.py must never reference composer_draft_client "
            "(the only module in the repo permitted to create a real Composer "
            "symphony) -- this is a pure math/advisory module with no write path."
        )

    def test_module_never_reads_or_sets_live_execution(self):
        source = _read_executable_source()
        assert "LIVE_EXECUTION" not in source


class TestNoWildcardImportEscapeHatch:
    def test_module_does_not_use_wildcard_imports(self):
        """Even an indirect `from alpha_bot_execution import *` (or from any
        other module) would make the invest/deploy-absence unverifiable by
        source-scan."""
        source = _read_source()
        assert "import *" not in source


class TestAdvisoryOnlyPersistence:
    def test_module_never_overrides_is_advisory_only(self):
        """database.insert_advisor_observation forces is_advisory_only=1 in
        its own SQL regardless of caller kwargs (AC-7's DB-layer half) -- this
        module must not attempt to pass a conflicting is_advisory_only kwarg
        (which insert_advisor_observation's **kwargs would silently swallow
        today, but a future refactor of that function must not be able to be
        overridden from this call site)."""
        source = _read_executable_source()
        assert "is_advisory_only" not in source, (
            "retirement_recommender.py must not reference is_advisory_only at "
            "all -- persistence is advisory-only by construction of "
            "database.insert_advisor_observation, never by a caller-supplied "
            "override."
        )


class TestRouteNotInSettingsWriteAllowlist:
    def test_retirement_recommendations_route_not_in_allowlist(self):
        import app as app_module  # noqa: PLC0415

        allowlist = getattr(app_module, "_SETTINGS_WRITE_ALLOWLIST", None)
        if allowlist is None:
            pytest.skip("_SETTINGS_WRITE_ALLOWLIST not found on app module")
        assert "/api/retirement-recommendations" not in allowlist


class TestRequiredModuleExistsForBoundaryToBeMeaningful:
    def test_module_exists(self):
        path = REPO_ROOT / _MODULE_REL_PATH
        assert path.exists(), (
            f"{_MODULE_REL_PATH} does not exist -- the no-trade boundary is unverifiable"
        )
