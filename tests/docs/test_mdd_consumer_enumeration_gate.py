"""
RED test — feature-plans/mdd-window-truth.md AC-0a (GATE, blocks AC-1).
DE-PERF-WINDOW-TRUTH-001.

AC-0a requires a COMMITTED enumeration artifact (not a verbal claim) listing
EVERY consumer of get_symphony_max_drawdown / get_portfolio_max_drawdown /
dry_run / mdd_bot / mdd_if_held / mdd_alpha across routes, templates, JS,
post-mortems, Discord embeds, and advisors -- with file:line citations -- PLUS a
decision on whether the AC-1 redefinition mutates the existing function in
place or introduces a new value alongside it.

This test does not (and cannot) verify the enumeration is EXHAUSTIVE against
the live codebase (that would require re-implementing the enumeration itself,
which is the implementer's job, not the test-writer's). It verifies the
artifact EXISTS, is genuinely committed (git-tracked, not a scratch file), and
covers -- by file:line-shaped citation -- every file the test-writer's own
independent grep (pasted below, reproducible) found calling these symbols at
RED-phase time. A future consumer this grep didn't find is not this test's
job to catch; the plan's own words are explicit that the artifact, not this
test, is the source of truth for exhaustiveness.

REPRODUCIBLE GREP (run from the repo root; each hit below is a required file
in the artifact):

    rg -n "get_symphony_max_drawdown\\(|get_portfolio_max_drawdown\\(|mdd_bot|mdd_if_held|mdd_alpha" \
        analytics.py app.py templates/index.html static/index.js

...found direct call/render sites in ALL FOUR of: analytics.py (compute_
windowed_portfolio_strip), app.py (multiple: _compute_portfolio_strip's warm/
cold-cache mdd branch, portfolio_meta build, 3 distinct per-symphony `_mdd`
enrichment loops -- live SSR dashboard, closed/frozen snapshot branch, and
/api/state's card-JSON loop -- plus GET /api/strip/<window>), templates/
index.html (the hero vs-row AND both per-symphony card footer blocks), and
static/index.js (updateComparisonRows' live-poll overwrite path). See
docs/audit/PERF-WINDOW-TRUTH-2026-09-03.md and feature-plans/mdd-window-truth.md's
own Architecture section for the corroborating file list.

AC-0b (units convention) is co-gated here: the SAME artifact must declare,
with file:line, whether the pre-fix dry_run is percentage-point (un-normalized)
while Composer's max_drawdown is a normalized fraction.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_PATH = _REPO_ROOT / "docs" / "audit" / "MDD-CONSUMER-ENUMERATION-2026-09-03.md"

# Every file the test-writer's own grep (docstring above) found directly
# calling/rendering the enumerated symbols at RED-phase time (HEAD bf5239ab).
_REQUIRED_FILES = [
    "analytics.py",
    "app.py",
    "templates/index.html",
    "static/index.js",
]

# The 6 symbols AC-0a names explicitly.
_REQUIRED_SYMBOLS = [
    "get_symphony_max_drawdown",
    "get_portfolio_max_drawdown",
    "dry_run",
    "mdd_bot",
    "mdd_if_held",
    "mdd_alpha",
]

_FILE_LINE_CITATION_RE = re.compile(r"[\w./\\-]+\.(?:py|html|js):\d+")


def _read_artifact() -> str:
    assert _ARTIFACT_PATH.exists(), (
        f"AC-0a GATE: the committed consumer-enumeration artifact does not exist "
        f"at {_ARTIFACT_PATH}. AC-0a is a GATE that blocks AC-1 -- the "
        f"redefinition of get_symphony_max_drawdown/get_portfolio_max_drawdown "
        f"must NOT proceed until this artifact is written and committed. It "
        f"must enumerate every consumer of get_symphony_max_drawdown / "
        f"get_portfolio_max_drawdown / dry_run / mdd_bot / mdd_if_held / "
        f"mdd_alpha with file:line citations, per the reproducible grep in this "
        f"test file's module docstring."
    )
    return _ARTIFACT_PATH.read_text(encoding="utf-8")


def _git_tracked(path: Path) -> bool:
    rel = path.relative_to(_REPO_ROOT)
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(rel)],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


class TestAC0aConsumerEnumerationArtifact:
    def test_artifact_exists_and_is_non_empty(self):
        content = _read_artifact()
        assert len(content.strip()) > 200, (
            "AC-0a artifact exists but looks like a stub (<200 chars) -- a real "
            "enumeration with file:line citations across 4+ files cannot be "
            "this short."
        )

    def test_artifact_is_git_tracked_not_a_scratch_file(self):
        """AC-0a: 'a committed artifact, not a verbal claim' -- an untracked
        file in the worktree is indistinguishable from a scratch note that
        never ships. Must be `git add`-ed (staged counts; ls-files --error-
        unmatch reports staged-or-committed paths)."""
        _read_artifact()  # existence precondition
        assert _git_tracked(_ARTIFACT_PATH), (
            f"{_ARTIFACT_PATH} exists on disk but is not tracked by git "
            f"(`git add` it) -- AC-0a requires a COMMITTED artifact, not an "
            f"untracked scratch file that could be silently left out of the PR."
        )

    @pytest.mark.parametrize("required_file", _REQUIRED_FILES)
    def test_artifact_cites_every_file_the_writers_own_grep_found(self, required_file):
        content = _read_artifact()
        assert required_file in content, (
            f"AC-0a artifact does not mention '{required_file}' -- the "
            f"test-writer's own grep (see this test file's module docstring, "
            f"reproducible) found a direct get_symphony_max_drawdown / "
            f"get_portfolio_max_drawdown / mdd_bot / mdd_if_held / mdd_alpha "
            f"consumer in this file. Either the enumeration missed a real "
            f"consumer, or (if it was genuinely reviewed and found irrelevant) "
            f"the artifact must say so explicitly and why."
        )

    @pytest.mark.parametrize("symbol", _REQUIRED_SYMBOLS)
    def test_artifact_names_every_required_symbol(self, symbol):
        content = _read_artifact()
        assert symbol in content, (
            f"AC-0a artifact does not mention the symbol '{symbol}' -- the plan "
            f"names 6 symbols explicitly (get_symphony_max_drawdown, "
            f"get_portfolio_max_drawdown, dry_run, mdd_bot, mdd_if_held, "
            f"mdd_alpha); all 6 must appear in the enumeration."
        )

    def test_artifact_contains_file_line_shaped_citations(self):
        """Not just filenames in prose -- genuine file:line citations, per
        AC-0a's explicit wording ('Produce the list with file:line')."""
        content = _read_artifact()
        citations = _FILE_LINE_CITATION_RE.findall(content)
        assert len(citations) >= len(_REQUIRED_FILES), (
            f"AC-0a artifact contains only {len(citations)} file:line-shaped "
            f"citation(s) (pattern: name.py:123) -- expected at least "
            f"{len(_REQUIRED_FILES)} (one per required file, most files have "
            f"multiple call sites so more is expected). Citations found: "
            f"{citations}"
        )

    def test_artifact_states_a_mutate_vs_alongside_decision(self):
        """AC-0a's explicit fork: 'If any consumer depends on the current
        divergence-residual semantics, the fix introduces a NEW correctly-shaped
        value alongside rather than mutating the existing one in place.' The
        artifact must state which path was chosen and why -- a decision this
        test cannot make on the implementer's behalf, but can verify was made
        explicitly rather than left implicit."""
        content = _read_artifact().lower()
        decision_stated = (
            ("mutate" in content) or ("alongside" in content) or ("in place" in content)
        )
        assert decision_stated, (
            "AC-0a artifact does not state an explicit mutate-in-place-vs-"
            "new-value-alongside decision (plan's exact fork). This is a "
            "GATE requirement, not optional prose -- a silent choice risks an "
            "engine-adjacent regression on an unexamined consumer."
        )


class TestAC0bUnitsConventionGate:
    """AC-0b is co-located in the SAME artifact per the plan's wording
    ('State explicitly, with file:line, whether...')."""

    def test_artifact_declares_the_units_mismatch_with_file_line_evidence(self):
        content = _read_artifact()
        lowered = content.lower()
        mentions_units_question = (
            "percentage-point" in lowered or "un-normalized" in lowered or "unnormalized" in lowered
        ) and "fraction" in lowered
        assert mentions_units_question, (
            "AC-0b GATE: the artifact must explicitly state whether the "
            "current dry_run is an un-normalized percentage-point peak-to-"
            "trough while Composer's max_drawdown is a normalized fraction "
            "(the exact evidence: dry_run is provably translation-invariant "
            "to if_held, which a normalized drawdown cannot be) -- and declare "
            "ONE convention for the fixed metric."
        )
        citations = _FILE_LINE_CITATION_RE.findall(content)
        assert len(citations) >= 1, (
            "AC-0b requires file:line evidence for the units declaration -- "
            "none found in the artifact."
        )
