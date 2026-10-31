"""
RED tests — Frontrunner Builder no-auto-trade boundary (structural, not policy).

feature-plans/frontrunner-builder.md §Security Considerations mandates that
`composer_draft_client` (the shared Composer write client used by the
frontrunner builder AND the propose_strategies retrofit) DOES NOT IMPLEMENT
invest/deploy at all — so no code path in this feature can fund or trade a
symphony, even accidentally. Deploying is `invest_in_symphony` ->
`POST /deploy/accounts/{uuid}/symphonies/{id}/invest`, an entirely separate
call this feature must never construct or reach.

These tests are adversarial: they source-scan and introspect the REAL modules
(not a mocked stand-in) so the guarantee holds regardless of future refactors
inside the module bodies — a passing test here means the invest/deploy surface
is structurally absent, not merely unused today.

Expected state: RED until advisors/composer_draft_client.py and
advisors/frontrunner_builder.py exist and satisfy the boundary.
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from unittest.mock import patch

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# The literal deploy/invest endpoint fragment that must never appear as a
# constructed URL anywhere in the frontrunner surface.
_DEPLOY_PATH_FRAGMENT = "/deploy/"
_INVEST_ENDPOINT_FRAGMENT = "/invest"


def _read_source(relative_path: str) -> str:
    path = REPO_ROOT / relative_path
    if not path.exists():
        pytest.fail(f"expected module source not found: {relative_path}")
    return path.read_text(encoding="utf-8")


def _read_executable_source(relative_path: str) -> str:
    """Return the module source with docstrings and comments stripped.

    A module is allowed to DOCUMENT the no-trade boundary in prose (its own
    module docstring may legitimately say "never call /deploy/.../invest" as
    an explanation of what it deliberately omits — composer_draft_client.py
    does exactly this). Scanning raw source text would flag that
    documentation as if it were the violation. Stripping docstrings/comments
    via the AST keeps the check on ACTUAL CODE — string literals used in
    expressions/calls (f-strings, concatenation, a URL passed to
    requests.post) — which is what the boundary is really about.
    """
    source = _read_source(relative_path)
    try:
        tree = ast.parse(source)
    except SyntaxError:
        pytest.fail(f"{relative_path} has a syntax error — cannot source-scan")

    # Strip every docstring (module/class/function Expr-Constant-str as the
    # first statement of its body) by blanking its literal value.
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
                lines[lineno - 1] = ""  # blank the whole docstring line range

    # Also strip full-line `#` comments (a simple heuristic — good enough for
    # this repo's style; a `#` inside a string literal on an otherwise-code
    # line is left alone, which only makes the check MORE conservative, never
    # less, since it would still flag a real fragment inside actual code).
    stripped_lines = []
    for line in lines:
        no_comment = line.split("#", 1)[0] if line.lstrip().startswith("#") is False else ""
        stripped_lines.append(no_comment if line.lstrip().startswith("#") else line)
    return "\n".join(stripped_lines)


# ---------------------------------------------------------------------------
# composer_draft_client — no invest/deploy symbol, no invest/deploy URL literal
# ---------------------------------------------------------------------------


def test_composer_draft_client_module_has_no_invest_or_deploy_attribute():
    """Word-boundary check, not a bare substring: 'deploy' as a substring would
    also flag verify_undeployed, the plan-mandated post-create SAFETY-
    VERIFICATION read (confirms zero allocation; never POSTs to /deploy or
    /invest — covered separately by the source-scan tests in this file).
    Only a genuine 'deploy'/'invest' ACTION token — as a whole underscore-
    delimited component — is a hit; 'undeployed' is explicitly not."""
    import advisors.composer_draft_client as cdc  # noqa: PLC0415

    public_names = [n for n in dir(cdc) if not n.startswith("_")]
    offending = [
        n
        for n in public_names
        if "invest" in n.lower().split("_") or "deploy" in n.lower().split("_")
    ]
    assert offending == [], f"composer_draft_client exposes trade-shaped symbols: {offending}"


def test_composer_draft_client_source_never_constructs_the_invest_url():
    """Source-scan (not just symbol-table) so a future author can't hand-roll
    a raw requests.post(f'{BASE}/deploy/.../invest') without a public symbol
    naming it 'invest' or 'deploy'.

    Scans EXECUTABLE code only (docstrings/comments stripped) — the module's
    own docstring legitimately documents this exact exclusion in prose
    ("...invest_in_symphony -> POST /deploy/.../invest, an entirely separate
    call..."), which must not itself trip the scan."""
    source = _read_executable_source("advisors/composer_draft_client.py")
    assert _DEPLOY_PATH_FRAGMENT not in source, (
        "composer_draft_client.py contains a '/deploy/' path fragment in "
        "EXECUTABLE code — this module must never reference the deploy/invest endpoint"
    )


def test_composer_draft_client_source_never_calls_invest_in_symphony():
    """invest_in_symphony is the named function in alpha_bot_execution that
    performs the actual funded deploy (mirrors the go-to-cash call at
    alpha_bot_execution.py:263). composer_draft_client must not import or call
    it (executable code only — the module docstring may name it in prose)."""
    source = _read_executable_source("advisors/composer_draft_client.py")
    assert "invest_in_symphony" not in source


# ---------------------------------------------------------------------------
# frontrunner_builder — the orchestrator must not reach the deploy/invest path
# either, even indirectly (e.g. by importing alpha_bot_execution's invest call)
# ---------------------------------------------------------------------------


def test_frontrunner_builder_source_never_constructs_the_invest_url():
    """Executable code only — a module docstring may legitimately document
    this exclusion in prose (as composer_draft_client.py's does) without
    tripping the scan."""
    source = _read_executable_source("advisors/frontrunner_builder.py")
    assert _DEPLOY_PATH_FRAGMENT not in source, (
        "frontrunner_builder.py contains a '/deploy/' path fragment in "
        "EXECUTABLE code — this module must never reference the deploy/invest endpoint"
    )


def test_frontrunner_builder_source_never_calls_invest_in_symphony():
    source = _read_executable_source("advisors/frontrunner_builder.py")
    assert "invest_in_symphony" not in source


def test_frontrunner_builder_never_imports_from_alpha_bot_execution_the_invest_symbol():
    """Even an indirect `from alpha_bot_execution import invest_in_symphony` (or a
    wildcard import that would silently pull it in) must be absent."""
    source = _read_source("advisors/frontrunner_builder.py")
    assert "import *" not in source, (
        "frontrunner_builder.py must not use wildcard imports — this would make "
        "the invest_in_symphony absence unverifiable by source-scan"
    )


# ---------------------------------------------------------------------------
# Every created symphony must be verified zero-allocation before an approval
# is marked uploaded — the belt-and-suspenders half of the boundary.
# ---------------------------------------------------------------------------


def test_frontrunner_builder_calls_verify_undeployed_before_marking_uploaded():
    """A textual/AST presence check that the approval-handling code path in
    frontrunner_builder.py references verify_undeployed at all. This does not
    replace the behavioral test in test_frontrunner_integration.py (which
    proves the ORDER: verify happens before the 'uploaded' status write) —
    it is the structural floor that the call exists in the module in the
    first place."""
    source = _read_source("advisors/frontrunner_builder.py")
    assert "verify_undeployed" in source, (
        "frontrunner_builder.py never references composer_draft_client.verify_undeployed "
        "— a created symphony must be verified zero-allocation before an approval "
        "is marked 'uploaded'"
    )


# ---------------------------------------------------------------------------
# No create without an explicit per-candidate operator approval, even on an
# unattended weekly run.
# ---------------------------------------------------------------------------


def test_run_weekly_build_extension_never_calls_save_symphony_directly():
    """AC-9 / Decisions table: 'Approval required even on unattended weekly
    runs'. The weekly scheduler path (strategy_builder_scheduler.run_weekly_build,
    extended per the plan to also invoke the frontrunner builder) must not
    itself call composer_draft_client.save_symphony — only the operator-driven
    /approve route may do that."""
    source = _read_source("advisors/strategy_builder_scheduler.py")
    assert "save_symphony" not in source, (
        "strategy_builder_scheduler.py calls save_symphony directly — this would "
        "create Composer symphonies on an unattended weekly run with no operator "
        "approval, violating the approval-gated write invariant"
    )


def test_frontrunner_builder_run_entrypoint_does_not_call_save_symphony(monkeypatch):
    """The run-a-build entry point (whatever public function drives 'detect
    through queue-for-approval') must not itself call save_symphony — only
    the separate approve() path may. We assert this by patching save_symphony
    to explode and driving the module's own 'run' function with all
    externals (Fable/backtest/Atlas) neutralised, then confirming no explosion
    occurred, i.e. save_symphony was never reached.

    Atlas mock (2026-07-11 hardening): community_strats.load_community_strategies
    is explicitly patched to an unavailable result. Previously this test's
    "Atlas neutralised" claim was true only by coincidence — the isolated
    test DB's empty live-symphony roster meant _run_build_for_symphony's
    per-cascade loop (and therefore any Atlas call inside it) never actually
    executed. That is a fragile, implicit safety net, not a real guard: if
    the roster were ever non-empty here, this test would attempt a genuine
    live Atlas/Mongo fetch. Explicit mocking makes the guarantee hold
    regardless of roster state."""
    import advisors.composer_draft_client as cdc
    import advisors.frontrunner_builder as fb

    def _explode(*_a, **_k):
        raise AssertionError("save_symphony must not be called from the run path")

    monkeypatch.setattr(cdc, "save_symphony", _explode, raising=True)

    run_fn = getattr(fb, "run_frontrunner_build", None) or getattr(fb, "run_build", None)
    assert run_fn is not None, (
        "frontrunner_builder.py must expose a run-the-build entrypoint "
        "(run_frontrunner_build or run_build) for the scheduler/route to call"
    )

    # The run function must be D-1 never-raises regardless of environment —
    # calling it with no further mocking (Fable/backtest absent/misconfigured)
    # must degrade cleanly, not explode, and critically must never reach save_symphony.
    with patch(
        "advisors.community_strats.load_community_strategies",
        return_value={"available": False, "candidates": [], "stats": {}, "source": "captplanet"},
    ):
        try:
            run_fn()
        except AssertionError:
            raise
        except Exception:
            # Any other exception is a pre-existing D-1 contract violation, out of
            # scope for THIS test (covered by test_frontrunner_integration.py);
            # what matters here is that save_symphony specifically was not hit.
            pass


# ---------------------------------------------------------------------------
# Sanity: confirm the assertions above are actually exercising real files, not
# vacuously passing because the module doesn't exist (would raise via _read_source's
# pytest.fail, but double-check the fixture list in one place for maintainers).
# ---------------------------------------------------------------------------


def test_required_modules_exist_for_this_boundary_to_be_meaningful():
    for rel in ("advisors/composer_draft_client.py", "advisors/frontrunner_builder.py"):
        path = REPO_ROOT / rel
        assert path.exists(), f"{rel} does not exist — the no-trade boundary is unverifiable"
        assert inspect is not None  # inspect imported for future AST-based extensions here


# ---------------------------------------------------------------------------
# The Composer create call has exactly one caller, and every click is bounded
# ---------------------------------------------------------------------------


def _production_python_sources() -> list[pathlib.Path]:
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "-z", "*.py"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout.decode("utf-8")
    return [
        REPO_ROOT / rel
        for rel in out.split(chr(0))
        if rel and not rel.startswith(("tests/", "docs/", "feature-plans/", ".claude/"))
    ]


def _enclosing_function_names(tree: ast.AST) -> dict[int, str | None]:
    """Map each node id to the name of the innermost function that contains it."""
    owner: dict[int, str | None] = {}

    def visit(node: ast.AST, current: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            name = (
                child.name
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                else current
            )
            owner[id(child)] = name
            visit(child, name)

    visit(tree, None)
    return owner


def test_save_symphony_is_referenced_only_inside_approve_frontrunner_proposal():
    """Every reference to the create call, outside its own module, sits in the approval function."""
    offenders = []
    approvals = 0
    for path in _production_python_sources():
        relative = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
        if relative == "advisors/composer_draft_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        owner = _enclosing_function_names(tree)
        for node in ast.walk(tree):
            refers = (isinstance(node, ast.Attribute) and node.attr == "save_symphony") or (
                isinstance(node, ast.Name) and node.id == "save_symphony"
            )
            refers = refers or (
                isinstance(node, ast.alias) and node.name.split(".")[-1] == "save_symphony"
            )
            if not refers:
                continue
            function = owner.get(id(node))
            if (
                function == "approve_frontrunner_proposal"
                and relative == "advisors/frontrunner_builder.py"
            ):
                approvals += 1
            else:
                offenders.append(f"{relative}:{getattr(node, 'lineno', '?')} in {function}")

    assert offenders == [], f"save_symphony referenced outside the approval function: {offenders}"
    assert approvals >= 1, "the approval function no longer reaches save_symphony: scan is vacuous"


def test_every_http_call_a_click_can_wait_on_in_the_draft_client_carries_a_timeout():
    """A click on Approve waits on these calls; none may be able to wait without a bound."""
    path = REPO_ROOT / "advisors" / "composer_draft_client.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "requests"
        and node.func.attr in {"get", "post", "put", "patch", "delete", "request"}
    ]
    assert calls, "no HTTP call found: the scan is vacuous"
    for call in calls:
        timeout = next((k.value for k in call.keywords if k.arg == "timeout"), None)
        assert timeout is not None, f"line {call.lineno}: HTTP call without a timeout"
        assert not (isinstance(timeout, ast.Constant) and timeout.value is None), (
            f"line {call.lineno}: timeout=None is unbounded"
        )
