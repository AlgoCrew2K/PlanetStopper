"""`.claude/archive/` is ignored by git, and no ignore rule that was there is gone.

The archive folder holds retired working files that must never be staged by a broad `git add`.
The first check asks git itself (`git check-ignore -v`), so a rule that the file's own text carries
but a later negation undoes is not counted; the second keeps every rule the file held before the
archive rule was added.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Every rule .gitignore held before the archive rule was added (comments and blank lines left out).
_REFERENCE_RULES = (
    "Your keys:",
    ".env",
    ".env.*",
    "!.env.example",
    "alphabot_state.db",
    "optuna_studies.db",
    "__pycache__/",
    "cache/",
    "history_cache.json",
    "symphony_logs.json",
    "post_mortem_*.json",
    "!tests/fixtures/**/post_mortem_*.json",
    ".coverage",
    ".pytest_cache/",
    ".ruff_cache/",
    "htmlcov/",
    "*.db-journal",
    "*.db-wal",
    "*.db-shm",
    "coverage.json",
    "alphabot_daemon.log",
    "alphabot_daemon.out.log",
    "alphabot_daemon.err.log",
    "alphabot.pid",
    "*.db-wal",
    "*.db-shm",
    "*.db",
    "*.db.*backup",
    "*.db.postDryRun",
    "*.db.preDryRun*",
    "*.sqlite",
    "*.sqlite-journal",
    "*.sqlite-wal",
    "*.sqlite-shm",
    ".scratch_*",
    ".dry-run-smoke.*",
    ".claude/worktrees/",
    ".claude/audit-worktrees/",
    ".claude/heartbeats/",
    "ux-*.png",
    ".design-handoff/screenshots/",
    ".playwright-mcp/",
    ".claude/settings.local.json",
    ".claude/tdd-handoff.md",
    "*.db.*",
    "*.db-*",
    "*.key",
    "*.pem",
    "*.log",
    "credentials.json",
    "*credentials*.json",
    "!tests/fixtures/**/*credentials*.json",
)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )


def test_a_file_under_the_archive_folder_is_ignored():
    found = _git("check-ignore", "-v", ".claude/archive/any-file")

    assert found.returncode == 0, "git does not ignore .claude/archive/"
    assert ".claude/archive" in found.stdout


def test_a_nested_archive_path_is_ignored_too():
    assert _git("check-ignore", "-q", ".claude/archive/2026/retired/notes.md").returncode == 0


def test_the_archive_rule_does_not_ignore_its_neighbours():
    for neighbour in (".claude/agents/some-agent.md", ".claude/CLAUDE.md"):
        assert _git("check-ignore", "-q", neighbour).returncode == 1, neighbour


def test_every_rule_that_was_there_is_still_there():
    lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    present = {line.strip() for line in lines if line.strip() and not line.strip().startswith("#")}

    assert set(_REFERENCE_RULES) - present == set()
