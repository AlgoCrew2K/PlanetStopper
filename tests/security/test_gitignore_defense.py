"""
Defense-in-depth tests — .gitignore must cover operational artifact patterns.

The security audit confirmed that .gitignore already covers .env and the main
database files, but several operational artifact directories and file patterns
introduced in later sprints are not listed.  Committing these by accident would
expose: agent working-tree source code (worktrees), daemon heartbeat content,
screenshots containing UI state, and live database files.

Required patterns to add (defense-in-depth; not all are currently present):
  - .claude/heartbeats/       — agent heartbeat files written during cycles
  - .claude/worktrees/        — agent working-tree checkouts (code + state)
  - .claude/audit-worktrees/  — audit working trees
  - *.db                      — catch any additional SQLite files
  - screenshots/              — design-handoff screenshots containing UI state
  - *.db.preDryRun*           — backup database files created by dry-run
  - *.db.postDryRun*          — post-dry-run database snapshots
  - alphabot.db               — live state database (not already covered by *.db)

Note: .gitignore already covers alphabot_state.db and optuna_studies.db by name,
*.db-journal, *.db-wal, *.db-shm.  This test checks for the ADDITIONAL patterns
listed above.

These are defense-in-depth checks — the patterns need to be present, not
necessarily in any particular format.  The test checks that `git check-ignore`
or a text-scan of .gitignore confirms each artifact class is covered.

Expected state: RED for missing patterns until .gitignore is updated.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
GITIGNORE_PATH = REPO_ROOT / ".gitignore"


def _gitignore_lines() -> list[str]:
    """Return non-blank, non-comment lines from .gitignore."""
    if not GITIGNORE_PATH.exists():
        return []
    text = GITIGNORE_PATH.read_text(encoding="utf-8")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _pattern_is_covered(artifact_path: str, gitignore_lines: list[str]) -> bool:
    """Return True iff at least one .gitignore line plausibly covers artifact_path.

    Uses a simplified glob-style match:
      - Exact match: ".claude/heartbeats/" in gitignore == ".claude/heartbeats/"
      - Directory wildcard: ".claude/" covers ".claude/heartbeats/"
      - Glob: "*.db" covers "alphabot.db"

    We do NOT run `git check-ignore` (requires the git binary and a real index);
    instead we apply the common subset of .gitignore rules that cover the
    test cases here.  Patterns that include ** or complex negations are
    treated conservatively (not matched by this helper — use a more specific
    pattern).
    """
    for pattern in gitignore_lines:
        # Strip trailing slashes for comparison (both sides).
        pat = pattern.rstrip("/")
        art = artifact_path.rstrip("/")

        # Exact match.
        if pat == art:
            return True

        # Glob: pattern contains * — match against the artifact's basename
        # or full path using simple fnmatch-style rules.
        if "*" in pat:
            import fnmatch

            # Match against the full path and the basename.
            if fnmatch.fnmatch(art, pat):
                return True
            basename = art.split("/")[-1]
            if fnmatch.fnmatch(basename, pat):
                return True

        # Prefix/directory match: if pattern is a directory prefix of artifact.
        if art.startswith(pat + "/") or art.startswith(pat):
            return True

    return False


# ---------------------------------------------------------------------------
# Parametrised checks for each required pattern class
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "artifact,description",
    [
        (
            ".claude/heartbeats/some-agent-1.txt",
            ".claude/heartbeats/ — agent heartbeat files written during TDD cycles",
        ),
        (
            ".claude/worktrees/some-branch/app.py",
            ".claude/worktrees/ — agent working-tree code checkouts",
        ),
        (
            ".claude/audit-worktrees/audit-1/database.py",
            ".claude/audit-worktrees/ — audit working trees",
        ),
        (
            "alphabot.db",
            "alphabot.db — live state database (must not be committed)",
        ),
        (
            "some_new_study.db",
            "*.db — catch-all for any SQLite database file",
        ),
        (
            ".design-handoff/screenshots/screenshot_001.png",
            "screenshots/ or .design-handoff/ — design-handoff screenshots",
        ),
        (
            "alphabot.db.preDryRun-1779897990-backup",
            "*.db.preDryRun* — backup DB files created by dry-run harness",
        ),
        (
            "alphabot_state.db.postDryRun",
            "*.db.postDryRun — post-dry-run database snapshots",
        ),
    ],
)
def test_gitignore_covers_artifact_pattern(artifact, description):
    """Each operational artifact class must be covered by .gitignore.

    Accidentally committing these files would expose internal state,
    working-tree source code, or create noisy diff history.

    For each artifact:
    - The .gitignore must contain a pattern that covers it (exact, glob, or prefix).
    - The check is conservative: if this test passes, a `git add` of the
      artifact should result in the file being ignored by git.
    """
    assert GITIGNORE_PATH.exists(), (
        f".gitignore not found at {GITIGNORE_PATH}. Create it and add the required patterns."
    )

    lines = _gitignore_lines()
    assert _pattern_is_covered(artifact, lines), (
        f".gitignore does not cover: {artifact!r}\n"
        f"Category: {description}\n"
        f"Add a line to .gitignore that matches this artifact class "
        f"(e.g., '.claude/heartbeats/', '*.db', '.design-handoff/'). "
        f"Current .gitignore patterns ({len(lines)} non-blank/non-comment):\n  "
        + "\n  ".join(lines)
    )


# ---------------------------------------------------------------------------
# Structural: .gitignore must not be empty
# ---------------------------------------------------------------------------


def test_gitignore_is_not_empty():
    """.gitignore must exist and contain at least the critical .env pattern.

    An empty .gitignore means the entire working tree is unprotected —
    credentials, databases, and cache files can be committed by accident.
    """
    assert GITIGNORE_PATH.exists(), f".gitignore missing at {GITIGNORE_PATH}."
    lines = _gitignore_lines()
    assert lines, ".gitignore is empty (no non-blank, non-comment lines). Add patterns."

    # The .env pattern must always be present — this is the highest-risk omission.
    env_covered = _pattern_is_covered(".env", lines)
    assert env_covered, (
        ".gitignore must cover '.env' — missing this pattern means real credentials "
        "can be committed by accident. This was how the Discord webhook was leaked "
        "(S-1 finding). "
        f"Current patterns: {lines}"
    )


# ---------------------------------------------------------------------------
# Real backups, keys, logs and credentials files: asked of git itself
# ---------------------------------------------------------------------------
#
# The checks above scan the patterns by a simplified glob. These ask ``git check-ignore`` (without the
# index, so a tracked file does not hide a missing pattern), because the repository is public and one
# ``git add -A`` in a checkout that holds these publishes them. The backup names are the shapes of the
# real untracked files in the primary checkout: a database copy taken before a restart or a cleanup,
# with SQLite's -shm and -wal companions.

MUST_BE_IGNORED = [
    "alphabot_state.db.pre-data-cleanup-20260609-104917",
    "alphabot_state.db.pre-data-cleanup-20260609-104917-shm",
    "alphabot_state.db.pre-data-cleanup-20260609-104917-wal",
    "alphabot_state.db.pre-restart-oom-20260610-130505",
    "alphabot_state.db.pre-rogue-prism-cleanup",
    "optuna_studies.db.pre-restart-20260609-123159",
    "optuna_studies.db.pre-restart-20260609-123159-wal",
    "server.key",
    "certs/privkey.pem",
    "debug.log",
    "logs/app.log",
    "credentials.json",
    "composer-credentials.json",
    "config/service-account-credentials.json",
    ".env.bak-20260701",
]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("path", MUST_BE_IGNORED)
def test_git_ignores_the_real_backup_key_log_and_credentials_shapes(path):
    result = _git("check-ignore", "-q", "--no-index", path)
    assert result.returncode == 0, f"git would add {path!r}: .gitignore does not cover it"


def test_no_tracked_file_is_matched_by_an_ignore_pattern():
    """A pattern broad enough to match a tracked file hides its later changes from ``git status``.

    The two SQLite fixtures under tests/fixtures/optuna_studies are tracked and match ``*.sqlite``
    already; the widening of the ignore list must not add to them (a fixture named for an invalid
    credentials answer is tracked and must stay visible)."""
    result = _git("ls-files", "-ci", "--exclude-standard")
    assert result.returncode == 0, result.stderr
    newly_hidden = [
        line for line in result.stdout.splitlines() if line and not line.endswith(".sqlite")
    ]
    assert newly_hidden == [], f"tracked files now match an ignore pattern: {newly_hidden}"
