"""The dead state-poll table template is gone, and nothing renders, loads or names it.

`GET /api/state` once rendered `templates/table_partial.html` into an `html` key that no client
reads. The template is deleted, so these scans hold the tree to four things:

* the template file does not exist, and `app.py`, `static/` and `templates/` never name it;
* no test names it, except a docstring that records a past change or describes history and the
  pins that `static/index.js` never puts `data.html` into the DOM; no test renders it, reads its
  file, or reads a table context out of a patched `render_template`;
* the two frozen-render fixtures are kept only while a test reads them;
* the header comment of `static/settings-modal.js` names only what opens the modal now.

Scans read the files of this checkout and skip the worktrees and caches inside it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"
NAME = "table_partial"

# Files under tests/ that may still name the template: two docstrings that record a past cycle's
# diff, the four pins that the dashboard script never puts `data.html` into the DOM, and this scan.
ALLOWED_TEST_FILES = frozenset(
    {
        "tests/test_scope_guard_f7.py",
        "tests/execution/test_cycleat_fix.py",
        "tests/dashboard/test_cards_live_updates.py",
        "tests/dashboard/test_card_consistency_liveness.py",
        "tests/dashboard/test_cards_live_refresh.py",
        "tests/dashboard/test_symph_autoupdate.py",
        "tests/dashboard/test_table_partial_retired.py",
    }
)
# What a line that names the template must not also do (render it, load it, read its file).
RENDERING_TOKENS = ("get_template", "render_template", "read_text", "open(", "Path(")

FIXTURE_DIR = TESTS / "fixtures" / "dashboard" / "frozen_html"
REMOVED_FIXTURE = "frozen_html_missing_shadow_hwm.json"
KEPT_FIXTURE = "frozen_html_with_symphonies.json"

SKIPPED_PARTS = {".claude", ".git", "__pycache__", "node_modules", ".venv"}


def _files(root: Path, pattern: str) -> list[Path]:
    return sorted(
        path
        for path in root.rglob(pattern)
        if path.is_file() and not SKIPPED_PARTS & set(path.relative_to(REPO).parts)
    )


def _relative(path: Path) -> str:
    return path.relative_to(REPO).as_posix()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# The served code
# ---------------------------------------------------------------------------


def test_the_template_file_does_not_exist():
    assert not (REPO / "templates" / "table_partial.html").exists()


def test_nothing_the_app_serves_names_the_template():
    served = [REPO / "app.py", *_files(REPO / "static", "*"), *_files(REPO / "templates", "*")]
    hits = [_relative(path) for path in served if path.is_file() and NAME in _read(path)]
    assert not hits, f"these served files still name the template: {hits}"


def test_no_other_python_module_of_the_app_names_the_template():
    modules = [path for path in REPO.glob("*.py")] + _files(REPO / "scripts", "*.py")
    hits = [_relative(path) for path in modules if NAME in _read(path)]
    assert not hits, f"these modules still name the template: {hits}"


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------


def test_only_allowed_test_files_name_the_template():
    hits = [
        _relative(path)
        for path in _files(TESTS, "*.py")
        if NAME in _read(path) and _relative(path) not in ALLOWED_TEST_FILES
    ]
    assert not hits, f"these test files still name the template: {hits}"


def test_no_test_renders_loads_or_reads_the_template_file():
    offending = []
    for path in _files(TESTS, "*.py"):
        if _relative(path) == "tests/dashboard/test_table_partial_retired.py":
            continue
        for number, line in enumerate(_read(path).splitlines(), start=1):
            if NAME in line and any(token in line for token in RENDERING_TOKENS):
                offending.append(f"{_relative(path)}:{number}")
    assert not offending, f"these lines render or read the template: {offending}"


def test_no_test_reads_a_table_context_from_a_patched_render_template():
    """A test that patches `render_template` to record its keyword arguments and then reads the
    account map or the sort fields out of them reads the table's context, which no longer exists."""
    context_reads = re.compile(r"(accounts_map|sort_col|sort_dir)")
    capture_names = ("capturing_render", "captured_render_args", "captured_ctx")
    offending = []
    for path in _files(TESTS, "*.py"):
        if _relative(path) == "tests/dashboard/test_table_partial_retired.py":
            continue
        text = _read(path)
        if "render_template" in text and any(name in text for name in capture_names):
            for number, line in enumerate(text.splitlines(), start=1):
                if context_reads.search(line) and (
                    "captured" in line or "ctx" in line or "kwargs" in line
                ):
                    offending.append(f"{_relative(path)}:{number}")
    assert not offending, f"these lines read a table context: {offending}"


# ---------------------------------------------------------------------------
# The fixtures
# ---------------------------------------------------------------------------


def test_the_fixture_only_the_removed_tests_read_is_gone():
    assert not (FIXTURE_DIR / REMOVED_FIXTURE).exists()


def test_the_fixture_a_kept_test_reads_stays_and_a_test_names_it():
    assert (FIXTURE_DIR / KEPT_FIXTURE).is_file()
    readers = [
        _relative(path)
        for path in _files(TESTS, "*.py")
        if KEPT_FIXTURE.removesuffix(".json") in _read(path)
        and _relative(path) != "tests/dashboard/test_table_partial_retired.py"
    ]
    assert readers, "no test reads the kept fixture, so it would be dead"


def test_every_json_file_in_the_fixture_folder_is_named_by_a_test():
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        stem = path.stem
        readers = [
            _relative(test)
            for test in _files(TESTS, "*.py")
            if stem in _read(test)
            and _relative(test) != "tests/dashboard/test_table_partial_retired.py"
        ]
        assert readers, f"{path.name} has no reader"


# ---------------------------------------------------------------------------
# The settings modal header
# ---------------------------------------------------------------------------


def test_the_settings_modal_header_names_only_the_card_gear():
    source = _read(REPO / "static" / "settings-modal.js")
    header = source[: source.index("*/") + 2]
    assert header.startswith("/**"), "fixture sanity: the file opens with its header comment"
    assert NAME not in header
    assert "table row" not in header.lower(), "the header still names a table row as an opener"
    assert "gear" in header.lower(), "the header no longer says what opens the modal"


def test_the_settings_modal_header_has_no_line_over_100_characters_and_no_doubled_space():
    source = _read(REPO / "static" / "settings-modal.js")
    header = source[: source.index("*/") + 2]
    assert header.startswith("/**"), "fixture sanity: the file opens with its header comment"
    too_long = [n for n, line in enumerate(header.splitlines(), 1) if len(line) > 100]
    doubled = [
        n
        for n, line in enumerate(header.splitlines(), 1)
        if "  " in re.sub(r"^\s*\*?\s*", "", line)
    ]
    assert too_long == [], f"header lines over 100 characters: {too_long}"
    assert doubled == [], f"header lines with a doubled space inside the text: {doubled}"


# ---------------------------------------------------------------------------
# The specialist agent file
# ---------------------------------------------------------------------------

AGENT_FILE = REPO / ".claude" / "agents" / "flask-dashboard-specialist.md"


def test_the_flask_specialist_agent_file_names_no_deleted_template():
    text = _read(AGENT_FILE)
    offending = [n for n, line in enumerate(text.splitlines(), 1) if NAME in line]
    assert offending == [], f"lines naming the deleted template: {offending}"


def test_the_flask_specialist_description_still_names_its_surfaces():
    description = next(
        line for line in _read(AGENT_FILE).splitlines() if line.startswith("description:")
    )
    for surface in ("app.py", "templates/index.html", "static/"):
        assert surface in description, f"the description no longer names {surface}"
