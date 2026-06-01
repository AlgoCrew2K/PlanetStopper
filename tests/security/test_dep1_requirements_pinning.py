"""
RED tests — DEP-1 (MEDIUM): requirements.txt is fully unpinned + anthropic undeclared.

Security audit finding DEP-1 confirmed that requirements.txt lists 11 packages
with NO version constraints and does not declare `anthropic`, which is imported
by ai_advisor.py but not listed.

Non-reproducible builds introduce two risks:
  1. Future malicious/broken releases are silently installed on a fresh deploy.
  2. A fresh install of the codebase will fail at runtime (ImportError from
     ai_advisor.py) because `anthropic` is not in requirements.txt.

Required fixes:
  - Pin every package to a compatible-release constraint (e.g., requests~=2.31)
    or provide a lockfile (pip-compile output).  At minimum, each package must
    have a version specifier (==, ~=, >=, >=x,<y).
  - Add `anthropic` (pinned) to requirements.txt.

Expected state: RED (tests fail) until requirements are pinned and anthropic declared.
"""

from __future__ import annotations

import pathlib
import re

import pytest


# ---------------------------------------------------------------------------
# Fixture: parse requirements.txt
# ---------------------------------------------------------------------------

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"


def _parse_requirements(path: pathlib.Path) -> list[tuple[str, str]]:
    """Return list of (package_name, raw_line) tuples from requirements.txt.

    Skips blank lines and comment lines.  Does not skip editable installs
    (those are treated as unpinned for purposes of this test).
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    result = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Normalise: package name is everything up to the first version specifier
        # character or whitespace.
        name_match = re.match(r"^([A-Za-z0-9_\-\.]+)", stripped)
        if name_match:
            result.append((name_match.group(1).lower(), stripped))
    return result


_VERSION_SPECIFIER_RE = re.compile(r"[><=!~]")


def _has_version_specifier(raw_line: str) -> bool:
    """Return True iff the requirements line contains at least one version specifier."""
    return bool(_VERSION_SPECIFIER_RE.search(raw_line))


# ---------------------------------------------------------------------------
# DEP-1 RED-1: requirements.txt must exist and be non-empty
# ---------------------------------------------------------------------------


def test_requirements_txt_exists():
    """requirements.txt must exist in the repo root.

    A missing requirements.txt is worse than an unpinned one — it makes
    the install process completely undefined.
    """
    assert REQUIREMENTS_PATH.exists(), (
        f"requirements.txt not found at {REQUIREMENTS_PATH}. "
        "Create requirements.txt in the repo root and pin all dependencies."
    )
    content = REQUIREMENTS_PATH.read_text(encoding="utf-8").strip()
    assert content, (
        "requirements.txt exists but is empty. "
        "Populate it with all runtime dependencies, pinned."
    )


# ---------------------------------------------------------------------------
# DEP-1 RED-2: anthropic must be declared in requirements.txt
# ---------------------------------------------------------------------------


def test_anthropic_is_declared_in_requirements():
    """'anthropic' must be listed in requirements.txt.

    ai_advisor.py imports `anthropic` at line 433.  Without it in
    requirements.txt, a fresh `pip install -r requirements.txt` produces
    an environment where `import ai_advisor` raises ImportError at runtime.
    This is a deployment-blocking defect, not merely a style issue.
    """
    assert REQUIREMENTS_PATH.exists(), "requirements.txt missing — cannot check for anthropic."
    declared = {name for (name, _) in _parse_requirements(REQUIREMENTS_PATH)}
    assert "anthropic" in declared, (
        "Package 'anthropic' is imported by ai_advisor.py but NOT declared "
        "in requirements.txt. A fresh pip install will fail at runtime. "
        f"Currently declared packages: {sorted(declared)}"
    )


# ---------------------------------------------------------------------------
# DEP-1 RED-3: every declared package must have a version specifier
# ---------------------------------------------------------------------------


def test_all_requirements_are_pinned():
    """Every line in requirements.txt must include a version specifier.

    Acceptable specifiers: ==, ~=, >=, <=, !=, > (combined or standalone).
    Examples of PASSING lines:
      requests~=2.31
      numpy>=1.26,<2.0
      Flask==3.0.3

    Examples of FAILING lines (bare package names):
      requests
      numpy
      Flask

    Unpinned packages mean `pip install -r requirements.txt` installs
    whatever the latest version is on that day — a supply-chain risk and
    a reproducibility failure.
    """
    assert REQUIREMENTS_PATH.exists(), "requirements.txt missing — cannot check pinning."
    unpinned = [
        raw for (_, raw) in _parse_requirements(REQUIREMENTS_PATH)
        if not _has_version_specifier(raw)
    ]
    assert not unpinned, (
        "The following packages in requirements.txt have no version specifier. "
        "Add at minimum a compatible-release constraint (~=) for each:\n  "
        + "\n  ".join(unpinned)
        + "\n\nRationale: unpinned deps enable supply-chain attacks — a new "
        "major release could introduce breaking changes or malicious code "
        "that is silently installed on the next fresh deploy."
    )


# ---------------------------------------------------------------------------
# DEP-1 RED-4: anthropic must also be pinned (not just declared)
# ---------------------------------------------------------------------------


def test_anthropic_is_pinned():
    """The 'anthropic' entry in requirements.txt must include a version specifier.

    Declaring `anthropic` without a pin is better than nothing but still
    leaves the deployment exposed to breaking API changes in the Anthropic
    SDK (the SDK has shipped breaking changes between minor versions).
    """
    assert REQUIREMENTS_PATH.exists(), "requirements.txt missing."
    entries = _parse_requirements(REQUIREMENTS_PATH)
    anthropic_lines = [(name, raw) for (name, raw) in entries if name == "anthropic"]

    assert anthropic_lines, (
        "anthropic is not declared in requirements.txt — see RED-2."
    )
    _, raw = anthropic_lines[0]
    assert _has_version_specifier(raw), (
        f"anthropic is declared but not pinned: {raw!r}. "
        "Add a version specifier (e.g., anthropic~=0.28) to ensure "
        "reproducible installs and guard against breaking SDK changes."
    )


# ---------------------------------------------------------------------------
# DEP-1 RED-5: ai_advisor.py actually imports anthropic (smoke-verify the audit claim)
# ---------------------------------------------------------------------------


def test_anthropic_is_actually_imported_by_ai_advisor():
    """Verify that ai_advisor.py contains an import of the anthropic package.

    This test guards against the scenario where 'anthropic' is added to
    requirements.txt but the actual import is later removed from ai_advisor.py
    — an orphaned dependency.  It also confirms the audit finding that
    ai_advisor.py depends on anthropic.

    Uses source-text search (not module import) to avoid daemon startup effects.
    """
    ai_advisor_path = REPO_ROOT / "ai_advisor.py"
    assert ai_advisor_path.exists(), (
        f"ai_advisor.py not found at {ai_advisor_path}. "
        "If it was renamed or removed, update this test."
    )
    source = ai_advisor_path.read_text(encoding="utf-8")
    # Accept 'import anthropic' or 'from anthropic import ...' patterns.
    has_import = bool(
        re.search(r"^\s*(import\s+anthropic|from\s+anthropic\s+import)", source, re.MULTILINE)
    )
    assert has_import, (
        "ai_advisor.py does not appear to import 'anthropic'. "
        "Either the import was removed (remove anthropic from requirements.txt too) "
        "or it is imported conditionally under a different pattern — "
        "update this test to match the actual import style."
    )
