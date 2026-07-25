"""
RED -- AC-9 (strategy-incubation-gate): zero live-engine references.

Mirrors the shipped tests/autotuner/test_exit_friction_blast_radius.py pattern
exactly (parametrized allow/forbid file sets + a full-repo sweep for stray
references). Strategy incubation is a Strategy-Builder/dashboard-only concern;
this test asserts every incubation symbol is referenced ONLY from the files
the handoff's Architecture section authorizes -- specifically NEVER from
alpha_bot_execution.py (the live per-minute exit-decision path) or
math_engine.py's decision functions (the live engine's risk math). A reference
from either file would mean incubation logic leaked into a live trade-decision
path, which the plan's Scope Boundaries explicitly forbid ("Zero changes to
live exit-decision codepaths").
"""

from __future__ import annotations

import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent

_ALLOWED_FILES = {
    _WORKTREE_ROOT / "database.py",
    _WORKTREE_ROOT / "app.py",
    _WORKTREE_ROOT / "advisors" / "incubation.py",
    _WORKTREE_ROOT / "advisors" / "strategy_builder_engine.py",
    _WORKTREE_ROOT / "static" / "ai_advisor.js",
    _WORKTREE_ROOT / "templates" / "ai_advisor.html",
}

_FORBIDDEN_FILES = {
    _WORKTREE_ROOT / "alpha_bot_execution.py",
    _WORKTREE_ROOT / "math_engine.py",
}

_INCUBATION_SYMBOLS = (
    "strategy_incubation",
    "incubation_daily",
    "register_incubation_candidate",
    "run_incubation_tick",
    "MAX_INCUBATING",
    "INCUBATION_WINDOW_TRADING_DAYS",
    "INCUBATION_MDD_BREACH_MULT",
)


def _grep_symbol_references(path: pathlib.Path, symbol: str) -> list[int]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return [i + 1 for i, line in enumerate(lines) if symbol in line]


@pytest.mark.parametrize(
    "forbidden_file",
    sorted(_FORBIDDEN_FILES, key=lambda p: p.name),
    ids=lambda p: p.name,
)
@pytest.mark.parametrize("symbol", _INCUBATION_SYMBOLS)
def test_incubation_symbols_never_referenced_in_live_engine_files(forbidden_file, symbol):
    hits = _grep_symbol_references(forbidden_file, symbol)
    assert not hits, (
        f"{symbol!r} must NEVER be referenced in {forbidden_file.name} (the live "
        f"engine's decision path) -- found reference(s) at line(s) {hits}. "
        "AC-9: zero live-engine decision-path changes."
    )


def test_incubation_referenced_only_in_allowed_surfaces():
    """Positive control: at least one incubation symbol IS expected to appear in
    the allowed surfaces once implemented -- guards against a future "fix" that
    satisfies the negative test above by simply never building the feature."""
    total_hits = sum(
        len(_grep_symbol_references(f, symbol))
        for f in _ALLOWED_FILES
        for symbol in _INCUBATION_SYMBOLS
    )
    assert total_hits > 0, (
        "No incubation symbol is referenced anywhere in the allowed surfaces "
        f"({sorted(f.name for f in _ALLOWED_FILES)}) -- the feature isn't wired up "
        "at all, which is not the same as 'zero live-engine references' (AC-9 is a "
        "scoping guard, not a deletion license)."
    )


def test_no_python_file_outside_allowed_and_forbidden_sets_references_strategy_incubation():
    """Sweep every tracked .py file in the repo (excluding tests/ and worktree/audit
    scratch dirs per the project's blast-radius scanner convention) for stray
    references to the strategy_incubation table name outside the explicitly
    authorized surfaces."""
    skip_dir_names = {".claude", "node_modules", ".git", "__pycache__"}
    stray: dict[str, list[int]] = {}

    for py_file in _WORKTREE_ROOT.rglob("*.py"):
        if any(part in skip_dir_names for part in py_file.parts):
            continue
        if "tests" in py_file.relative_to(_WORKTREE_ROOT).parts:
            continue
        if py_file in _ALLOWED_FILES or py_file in _FORBIDDEN_FILES:
            continue
        hits = _grep_symbol_references(py_file, "strategy_incubation")
        if hits:
            stray[str(py_file.relative_to(_WORKTREE_ROOT))] = hits

    assert not stray, (
        f"'strategy_incubation' referenced from unauthorized file(s): {stray}. "
        f"Only {sorted(f.name for f in _ALLOWED_FILES)} are authorized "
        "(.claude/tdd-handoff.md Architecture)."
    )
