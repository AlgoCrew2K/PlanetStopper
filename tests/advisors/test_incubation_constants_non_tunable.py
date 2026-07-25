"""
RED -- review Finding 2 (ga3-rev, 2026-07-25): non-tunability regression trio
for the 5 Strategy Incubation Gate constants.

Mirrors tests/autotuner/test_exit_friction_constant.py's AC-3 pattern exactly:
absent from OPTUNA_SEARCH_SPACE_KEYS, absent from the AI Advisor's suggestible
allowlist, and never read from a per-trial params dict or the environment.
Same rationale as that precedent: a tunable incubation threshold would let the
optimizer (or an AI-Advisor-suggested config change) game its own gate --
these are hand-set, code-only knobs by design (.claude/tdd-handoff.md "Named
constants" table: "all operator knobs -- NEVER Optuna/advisor-tunable").

Non-vacuity addendum (this file's own requirement, since the exit-friction
precedent has no planted-positive control): the dict/env-access AST+string
scan below is demonstrated capable of catching a REAL violation by running it
against a temporary source copy with a violation deliberately planted into
it, proving the scan mechanism itself is not vacuous -- not just "found
nothing" on real (clean) source, which alone would leave open the question of
whether the scan can detect anything at all.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent

# The 5 incubation constants and the file each is defined in, per
# .claude/tdd-handoff.md "Module placement" (database.py owns the
# cap/refractory checks it enforces directly; advisors/incubation.py owns the
# tick/promotion-only constants).
_CONSTANTS_AND_HOME_FILES = [
    ("MAX_INCUBATING", _WORKTREE_ROOT / "database.py"),
    ("INCUBATION_REFRACTORY_DAYS", _WORKTREE_ROOT / "database.py"),
    ("INCUBATION_WINDOW_TRADING_DAYS", _WORKTREE_ROOT / "advisors" / "incubation.py"),
    ("INCUBATION_MDD_BREACH_MULT", _WORKTREE_ROOT / "advisors" / "incubation.py"),
    ("INCUBATION_MAX_FETCH_FAILURES", _WORKTREE_ROOT / "advisors" / "incubation.py"),
]

_CONSTANT_NAMES = [name for name, _ in _CONSTANTS_AND_HOME_FILES]

# Every file this cycle touches or could plausibly read a tunable value from
# -- the same allowed-surface set as tests/advisors/test_incubation_blast_radius.py,
# minus the dashboard-only files (JS/HTML can't have Optuna/env dict-access
# patterns) plus the two constants' own home files.
_SCANNED_SOURCE_FILES = [
    _WORKTREE_ROOT / "database.py",
    _WORKTREE_ROOT / "advisors" / "incubation.py",
    _WORKTREE_ROOT / "advisors" / "strategy_builder_engine.py",
    _WORKTREE_ROOT / "app.py",
    _WORKTREE_ROOT / "autotuner.py",
]


def _find_dict_style_or_env_access(source_text: str, const_name: str) -> list[int]:
    """Return line numbers of any p.get(const_name, ...) / p[const_name] /
    os.environ.get(const_name, ...) access pattern for const_name in
    source_text. Shared scan helper -- used by both the real-source tests
    below and the non-vacuity planted-violation test, so both exercise the
    IDENTICAL detection logic (no drift between "what we test" and "what we
    prove can be caught")."""
    offending: list[int] = []

    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        # A temp-copy-with-violation test may intentionally construct
        # syntactically valid Python only -- a real SyntaxError here means a
        # caller bug, not a detection result. Surface it loudly rather than
        # silently reporting "no violations found".
        raise

    for node in ast.walk(tree):
        # p.get("CONST_NAME", ...) / params.get(...) / os.environ.get(...)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == const_name
        ):
            offending.append(node.lineno)
        # p["CONST_NAME"]
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value == const_name
        ):
            offending.append(node.lineno)

    # os.environ.get("CONST_NAME" -- string-level check (environ.get's first
    # arg is already covered by the AST Call branch above via node.func.attr
    # == "get", but this redundant string check guards against any
    # environ[...] subscript form the AST walk might miss due to aliasing).
    if f'environ.get("{const_name}"' in source_text or f"environ.get('{const_name}'" in source_text:
        # Avoid double-counting if the AST walk already found it -- only add
        # a sentinel line if nothing was found yet, so callers can still
        # detect "some violation exists" even if line-number precision from
        # this branch is coarser.
        if not offending:
            offending.append(-1)

    return offending


# ---------------------------------------------------------------------------
# Absent from OPTUNA_SEARCH_SPACE_KEYS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("const_name", _CONSTANT_NAMES)
def test_incubation_constant_absent_from_optuna_search_space(const_name):
    import autotuner

    assert const_name not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        f"{const_name} must NOT be a member of autotuner.OPTUNA_SEARCH_SPACE_KEYS "
        "-- a tunable incubation threshold would let the optimizer game its own "
        "gate (.claude/tdd-handoff.md: 'all operator knobs -- NEVER Optuna/"
        "advisor-tunable')."
    )


# ---------------------------------------------------------------------------
# Absent from the AI Advisor's suggestible allowlist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("const_name", _CONSTANT_NAMES)
def test_incubation_constant_absent_from_advisor_suggestible_allowlist(const_name):
    import ai_advisor

    assert const_name not in ai_advisor._SUGGESTIBLE_ALLOWLIST, (
        f"{const_name} must NOT be a member of ai_advisor._SUGGESTIBLE_ALLOWLIST "
        "-- the AI Advisor must never be able to suggest changing an incubation "
        "threshold; these are hand-set, code-only knobs."
    )


# ---------------------------------------------------------------------------
# Never read from a per-trial params dict or the environment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("const_name", _CONSTANT_NAMES)
@pytest.mark.parametrize("scanned_file", _SCANNED_SOURCE_FILES, ids=lambda p: p.name)
def test_incubation_constant_never_dict_or_env_accessed(const_name, scanned_file):
    if not scanned_file.exists():
        pytest.skip(f"{scanned_file} does not exist in this tree")
    source_text = scanned_file.read_text(encoding="utf-8")
    offending = _find_dict_style_or_env_access(source_text, const_name)
    assert not offending, (
        f"{const_name} must never be read via dict-style access (p.get(...)/"
        f"p[...]) or os.environ -- found at line(s) {offending} in "
        f"{scanned_file.name}. It must be referenced directly as the module "
        "constant, never sourced from a per-trial params dict or env var."
    )


# ---------------------------------------------------------------------------
# Non-vacuity: prove the scan can actually catch a real violation
# ---------------------------------------------------------------------------


def test_dict_style_scan_detects_a_planted_violation():
    """Non-vacuity control (this file's own requirement -- the exit-friction
    precedent this trio mirrors has no equivalent planted-positive check).
    Runs the SAME _find_dict_style_or_env_access helper the real tests above
    use against a synthetic source string containing a deliberately planted
    p.get("MAX_INCUBATING", 20) violation, and confirms it is detected. This
    proves a clean scan result on the real source files means "genuinely no
    violation", not "the scan can't detect anything"."""
    planted_source = (
        "def _read_config(p):\n"
        "    # A hypothetical future bug: reading the cap from a per-trial\n"
        "    # params dict instead of the module constant.\n"
        "    cap = p.get('MAX_INCUBATING', 20)\n"
        "    return cap\n"
    )
    hits = _find_dict_style_or_env_access(planted_source, "MAX_INCUBATING")
    assert hits == [4], (
        f"Expected the scan to detect the planted p.get('MAX_INCUBATING', 20) "
        f"violation at line 4, got {hits}. If this fails, the scan helper "
        "used by the real tests above cannot actually detect a real "
        "violation -- a clean result on real source would be meaningless."
    )


def test_subscript_style_scan_detects_a_planted_violation():
    """Companion non-vacuity control for the p["CONST_NAME"] subscript form
    (distinct AST node type from the .get(...) Call form above)."""
    planted_source = (
        "def _read_config(p):\n"
        "    window = p['INCUBATION_WINDOW_TRADING_DAYS']\n"
        "    return window\n"
    )
    hits = _find_dict_style_or_env_access(planted_source, "INCUBATION_WINDOW_TRADING_DAYS")
    assert hits == [2], (
        f"Expected the scan to detect the planted subscript violation at "
        f"line 2, got {hits}."
    )


def test_env_style_scan_detects_a_planted_violation():
    """Companion non-vacuity control for the os.environ.get("CONST_NAME", ...)
    form."""
    planted_source = (
        "import os\n"
        "\n"
        "def _read_config():\n"
        "    n = int(os.environ.get(\"INCUBATION_MAX_FETCH_FAILURES\", 5))\n"
        "    return n\n"
    )
    hits = _find_dict_style_or_env_access(planted_source, "INCUBATION_MAX_FETCH_FAILURES")
    assert hits, (
        "Expected the scan to detect the planted os.environ.get(...) "
        f"violation, got no hits. Source:\n{planted_source}"
    )


def test_scan_reports_zero_hits_on_source_with_no_violation():
    """Negative control: confirms the scan does NOT false-positive on
    unrelated dict/env access that happens to share no name collision --
    completes the non-vacuity triangle (can detect a real hit; does not
    fabricate one)."""
    clean_source = (
        "def _read_config(p):\n"
        "    unrelated = p.get('SOME_OTHER_KEY', 1)\n"
        "    return unrelated\n"
    )
    hits = _find_dict_style_or_env_access(clean_source, "MAX_INCUBATING")
    assert hits == [], (
        f"Expected zero hits for a const_name with no matching access in the "
        f"source, got {hits} -- the scan must not false-positive on an "
        "unrelated dict key."
    )
