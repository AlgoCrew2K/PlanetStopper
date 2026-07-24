"""
RED -- AC-10 (exit-friction-realized-savings): zero live-engine references.

SIM_EXIT_FRICTION_PCT is a replay/reporting/analytics-only concern. This
test asserts it is referenced ONLY from the files the plan's Architecture
section authorizes -- specifically NEVER from alpha_bot_execution.py (the
live per-minute exit-decision path) or math_engine.py's decision functions
(the live engine's risk math). A reference from either file would mean the
friction constant leaked into a live trade-decision path, which the plan's
Scope Boundaries explicitly forbid ("Zero changes to live exit-decision
codepaths").
"""

from __future__ import annotations

import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent

_ALLOWED_FILES = {
    _WORKTREE_ROOT / "autotuner.py",
    _WORKTREE_ROOT / "reporting.py",
    _WORKTREE_ROOT / "database.py",
    _WORKTREE_ROOT / "app.py",
}

_FORBIDDEN_FILES = {
    _WORKTREE_ROOT / "alpha_bot_execution.py",
    _WORKTREE_ROOT / "math_engine.py",
}


def _grep_constant_references(path: pathlib.Path) -> list[int]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [i + 1 for i, line in enumerate(lines) if "SIM_EXIT_FRICTION_PCT" in line]


@pytest.mark.parametrize(
    "forbidden_file",
    sorted(_FORBIDDEN_FILES, key=lambda p: p.name),
    ids=lambda p: p.name,
)
def test_sim_exit_friction_pct_never_referenced_in_live_engine_files(forbidden_file):
    hits = _grep_constant_references(forbidden_file)
    assert not hits, (
        f"SIM_EXIT_FRICTION_PCT must NEVER be referenced in {forbidden_file.name} "
        f"(the live engine's decision path) -- found reference(s) at line(s) "
        f"{hits}. AC-10 / plan Scope Boundaries: zero live-engine decision-path "
        f"changes."
    )


def test_sim_exit_friction_pct_referenced_only_in_allowed_surfaces():
    """Positive control: the constant IS expected to appear somewhere in the
    allowed replay/reporting/analytics/route surfaces once implemented --
    this guards against a future "fix" that satisfies the negative test above
    by simply deleting the feature.
    """
    total_hits = sum(len(_grep_constant_references(f)) for f in _ALLOWED_FILES)
    assert total_hits > 0, (
        "SIM_EXIT_FRICTION_PCT must be referenced from at least one of the "
        f"allowed surfaces ({sorted(f.name for f in _ALLOWED_FILES)}) once "
        "implemented -- zero references anywhere means the feature isn't "
        "wired up at all, which is not the same as 'zero live-engine "
        "references' (AC-10 is a scoping guard, not a deletion license)."
    )


def test_no_python_file_outside_allowed_and_forbidden_sets_references_it():
    """Sweep every tracked .py file in the repo (excluding tests/ and
    worktree/audit scratch dirs per the project's blast-radius scanner
    convention) for stray references outside the explicitly-authorized
    surfaces -- catches an accidental reference from an unrelated module
    (e.g. a helper script) that neither the allow-list nor the forbid-list
    anticipated.
    """
    skip_dir_names = {".claude", "node_modules", ".git", "__pycache__"}
    stray: dict[str, list[int]] = {}

    for py_file in _WORKTREE_ROOT.rglob("*.py"):
        if any(part in skip_dir_names for part in py_file.parts):
            continue
        if "tests" in py_file.relative_to(_WORKTREE_ROOT).parts:
            continue
        if py_file in _ALLOWED_FILES or py_file in _FORBIDDEN_FILES:
            continue
        hits = _grep_constant_references(py_file)
        if hits:
            stray[str(py_file.relative_to(_WORKTREE_ROOT))] = hits

    assert not stray, (
        f"SIM_EXIT_FRICTION_PCT referenced from unauthorized file(s): {stray}. "
        f"Only {sorted(f.name for f in _ALLOWED_FILES)} are authorized "
        f"(plan Architecture section)."
    )
