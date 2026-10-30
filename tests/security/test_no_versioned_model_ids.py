"""Source scan: no versioned Claude model ID in production or deployed source, and the
retired model accessors, literals and display maps are gone.

The scanner lives in this file and is self-contained (it imports nothing from a sibling
scanner). A scan that cannot fail proves nothing, so every rule of the scan is also run
against planted files in ``tmp_path`` laid out like the repository: the real-tree test
passes only when production source is clean, and the planted tests pass only when the
scanner reports what it should.

Scanned: tracked Python files, any file under ``templates/``, ``static/`` or ``deploy/``,
shell and PowerShell scripts, workflows under ``.github/workflows/``, agent files under
``.claude/agents/`` and ``.env.example``. Exempt: ``tests/``, ``docs/`` and
``feature-plans/``. Read-only: it never writes a file.
"""

from __future__ import annotations

import ast
import pathlib
import re
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# claude- + a family name + a hyphen + a version digit: every ID shape the API has used
# (claude-sonnet-5, claude-opus-4-8, claude-haiku-4-5-20251001).
_VERSIONED_ID = re.compile(r"claude-(?:opus|sonnet|haiku|fable|mythos)-\d", re.IGNORECASE)
_FAMILIES = ("opus", "sonnet", "haiku", "fable", "mythos")

_EXEMPT_TREES = ("tests/", "docs/", "feature-plans/")
_ANY_FILE_TREES = ("templates/", "static/", "deploy/", ".github/workflows/", ".claude/agents/")
_SCRIPT_SUFFIXES = (".py", ".ps1", ".sh")
_ENV_EXAMPLE = ".env.example"


def _in_scope(relative: str) -> bool:
    relative = relative.replace("\\", "/")
    if relative.startswith(_EXEMPT_TREES):
        return False
    return (
        relative.endswith(_SCRIPT_SUFFIXES)
        or relative.startswith(_ANY_FILE_TREES)
        or relative == _ENV_EXAMPLE
    )


def scan(root: pathlib.Path, relative_paths) -> list[tuple[str, int, str]]:
    """(file, line, text) of every versioned model ID in the in-scope files of ``relative_paths``."""
    found = []
    for relative in relative_paths:
        if not _in_scope(relative):
            continue
        path = root / relative
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _VERSIONED_ID.search(line):
                found.append((relative.replace("\\", "/"), number, line.strip()))
    return found


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, check=True
    ).stdout
    return [p for p in out.decode("utf-8").split("\0") if p]


def _plant(tmp_path: pathlib.Path, relative: str, text: str) -> list[str]:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return [relative]


def _production_python() -> list[pathlib.Path]:
    return [REPO_ROOT / p for p in _tracked_files() if p.endswith(".py") and _in_scope(p)]


# ---------------------------------------------------------------------------
# The real tree
# ---------------------------------------------------------------------------


def test_the_repository_holds_no_versioned_model_id_in_production_or_deployed_source():
    hits = scan(REPO_ROOT, _tracked_files())

    assert hits == [], "versioned model IDs found (file, line, text): " + "; ".join(
        f"{f}:{n}: {t}" for f, n, t in hits
    )


def test_the_scan_reads_the_files_it_is_meant_to_cover():
    in_scope = [p for p in _tracked_files() if _in_scope(p)]

    assert "model_config.py" in in_scope
    assert "ai_advisor.py" in in_scope
    assert _ENV_EXAMPLE in in_scope
    assert any(p.startswith("templates/") for p in in_scope)
    assert any(p.startswith("static/") for p in in_scope)


# ---------------------------------------------------------------------------
# The scanner against planted files (each rule fails when it should)
# ---------------------------------------------------------------------------


class TestScannerFlagsWhatItShould:
    def test_an_id_in_a_string_used_as_code(self, tmp_path):
        files = _plant(tmp_path, "advisors/example_site.py", 'MODEL = "claude-opus-4-8"\n')

        assert scan(tmp_path, files) == [
            ("advisors/example_site.py", 1, 'MODEL = "claude-opus-4-8"')
        ]

    def test_an_id_only_in_a_docstring_or_a_comment(self, tmp_path):
        text = '"""Default: claude-fable-5."""\n\n# was claude-sonnet-5 once\nx = 1\n'
        files = _plant(tmp_path, "example_module.py", text)

        assert [(n) for _, n, _ in scan(tmp_path, files)] == [1, 3]

    @pytest.mark.parametrize(
        "relative",
        [
            "templates/example_page.html",
            "static/example_script.js",
            "deploy/example-dropin.conf",
            "schedule_example.ps1",
            "scripts/example.sh",
            ".github/workflows/example.yml",
            ".claude/agents/example-agent.md",
            ".env.example",
        ],
    )
    def test_an_id_in_every_deployed_file_kind(self, tmp_path, relative):
        files = _plant(tmp_path, relative, "line one\nmodel: claude-haiku-4-5-20251001\n")

        hits = scan(tmp_path, files)

        assert [(f, n) for f, n, _ in hits] == [(relative, 2)]

    @pytest.mark.parametrize(
        "model_id",
        [
            "claude-sonnet-5",
            "claude-opus-4-8",
            "claude-haiku-4-5-20251001",
            *[f"claude-{family}-9" for family in _FAMILIES],
        ],
    )
    def test_every_id_shape_and_every_family(self, tmp_path, model_id):
        files = _plant(tmp_path, "example_module.py", f'X = "{model_id}"\n')

        assert len(scan(tmp_path, files)) == 1

    def test_an_id_in_an_agent_model_line(self, tmp_path):
        files = _plant(
            tmp_path,
            ".claude/agents/prism-example.md",
            "---\nname: x\nmodel: claude-opus-4-8\n---\n",
        )

        assert [n for _, n, _ in scan(tmp_path, files)] == [3]


class TestScannerPassesWhatItShould:
    @pytest.mark.parametrize(
        "relative",
        [
            "tests/example_test.py",
            "tests/fixtures/anthropic/example.json",
            "docs/example.md",
            "docs/research/example_probe.py",
            "feature-plans/example.md",
        ],
    )
    def test_exempt_trees_may_name_ids(self, tmp_path, relative):
        files = _plant(tmp_path, relative, "claude-opus-4-8 claude-fable-5\n")

        assert scan(tmp_path, files) == []

    @pytest.mark.parametrize(
        "relative, text",
        [
            ("advisors/example.py", 'MODEL = "opus"\n'),
            ("README_example.py", "# The Fable family answers this.\n"),
            (".claude/agents/prism-example.md", "---\nmodel: opus\n---\n"),
            (".claude/agents/other-example.md", "---\nmodel: sonnet\n---\n"),
            ("advisors/example.py", "# claude-fable- is a prefix, not an ID\n"),
        ],
    )
    def test_generic_tags_and_prose_are_not_versioned_ids(self, tmp_path, relative, text):
        files = _plant(tmp_path, relative, text)

        assert scan(tmp_path, files) == []

    def test_the_scan_only_reads(self, tmp_path):
        files = _plant(tmp_path, "example_module.py", 'X = "claude-opus-4-8"\n')
        files += _plant(tmp_path, "templates/page.html", "<p>clean</p>\n")
        before = {f: (tmp_path / f).stat().st_mtime_ns for f in files}
        content_before = {f: (tmp_path / f).read_bytes() for f in files}

        scan(tmp_path, files)

        assert {f: (tmp_path / f).stat().st_mtime_ns for f in files} == before
        assert {f: (tmp_path / f).read_bytes() for f in files} == content_before

    def test_the_real_tree_scan_changes_no_tracked_file(self):
        status = ["git", "status", "--porcelain", "--untracked-files=no"]
        before = subprocess.run(status, cwd=REPO_ROOT, capture_output=True, check=True).stdout

        scan(REPO_ROOT, _tracked_files())

        after = subprocess.run(status, cwd=REPO_ROOT, capture_output=True, check=True).stdout
        assert after == before


# ---------------------------------------------------------------------------
# Retired identifiers, literals and display maps
# ---------------------------------------------------------------------------

_RETIRED_NAMES = (
    "get_advisor_suggestion_model",
    "resolve_advisor_model",
    "_ADVISOR_SUGGESTION_MODEL_DEFAULT",
    "FABLE_MODEL",
    "_humanize_model_name",
    "_MODEL_DISPLAY_NAMES",
)


def _identifiers(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.alias):
            names.add(node.name.split(".")[-1])
            if node.asname:
                names.add(node.asname)
    return names


class TestRetiredIdentifiersAreGone:
    def test_no_production_module_defines_or_references_a_retired_name(self):
        offenders = {
            str(path.relative_to(REPO_ROOT)): sorted(set(_RETIRED_NAMES) & _identifiers(path))
            for path in _production_python()
        }
        offenders = {k: v for k, v in offenders.items() if v}

        assert offenders == {}, f"retired model accessors still referenced: {offenders}"

    def test_the_imported_modules_do_not_expose_the_old_accessors(self):
        import ai_advisor
        import model_config

        assert not hasattr(model_config, "get_advisor_suggestion_model")
        assert not hasattr(model_config, "_ADVISOR_SUGGESTION_MODEL_DEFAULT")
        assert not hasattr(ai_advisor, "resolve_advisor_model")

    def test_the_strategy_builder_engine_keeps_no_private_copy_of_the_reason_code(self):
        import advisors.strategy_builder_engine as sbe
        import llm_request

        assert not hasattr(sbe, "_MODEL_UNAVAILABLE_REASON")
        assert llm_request.REASON_MODEL_UNAVAILABLE == "model_unavailable"

    def test_no_production_module_other_than_llm_request_spells_the_unavailable_reason(self):
        offenders = []
        for path in _production_python():
            relative = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            if relative == "llm_request.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = _docstring_nodes(tree)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and node.value == "model_unavailable"
                    and id(node) not in docstrings
                ):
                    offenders.append(f"{relative}:{node.lineno}")

        assert offenders == [], f"the reason code is spelled out instead of imported: {offenders}"

    def test_no_production_module_other_than_model_config_spells_the_unavailable_text(self):
        offenders = []
        for path in _production_python():
            relative = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
            if relative == "model_config.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = _docstring_nodes(tree)
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value == "model unavailable"
                    and id(node) not in docstrings
                ):
                    offenders.append(f"{relative}:{node.lineno}")

        assert offenders == [], (
            "the badge text literal is repeated instead of using "
            f"model_config.MODEL_UNAVAILABLE_TEXT: {offenders}"
        )


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                ids.add(id(body[0].value))
    return ids
