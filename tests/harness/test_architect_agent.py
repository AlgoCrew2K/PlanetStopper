"""Contract tests for ``.claude/agents/architect.md``.

The agent file is a compiled specialization: the harness template's body plus a
``## Project bindings (PlanetStopper)`` section that this repository owns. These
tests pin the bindings and the frontmatter so an edit that drops a rule, adds a
placeholder or writes history text fails here.

Every check is a pure function over the file text and returns a list of
problems. Each check has one mutation control: a deliberately broken variant of
the text that the same check must flag, so a check that cannot fail is caught.

One real child-process case runs the harness compile ``--check`` command on the
compiled file. It skips when the harness scripts are not installed (CI runners
do not have them); every other test runs everywhere.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "architect.md"
COMPILE_SCRIPT = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "architect.md"

PROJECT_NAME = "PlanetStopper"
BINDINGS_HEADING = f"## Project bindings ({PROJECT_NAME})"
EXPECTED_TOOLS = ["Read", "Glob", "Grep", "Bash", "Write", "Edit", "TaskUpdate", "TaskList"]
# Template-owned pins; the stub omits them so a recompile re-derives them.
EXPECTED_MODEL = "opus"
EXPECTED_EFFORT = "high"
EXPECTED_SKILLS = ["test-design-contract"]

# One distinctive anchor per project binding. A binding is "supplied" when its
# anchor text is present in the bindings section.
BINDING_ANCHORS: dict[str, str] = {
    "1-read-code-at-ref": "Never copy a `path:line` from `.claude/CLAUDE.md`",
    "1-key-files-not-exhaustive": "Key Files table is not exhaustive",
    "1-exclude-worktrees": "Exclude `.claude/worktrees/` copies",
    "2-advisory-output-seam": "`database.insert_advisor_observation`",
    "2-upsert-form": "never `INSERT OR REPLACE`",
    "2-composer-clients": "header and base-URL seam in the execution module",
    "2-scheduler-wrapper": "two-function wrapper",
    "2-single-worker-executor": "single-worker executor that returns 202",
    "2-shared-helpers": "tree identity hash helper",
    "3-migration-numbering": "one above the highest existing file",
    "3-migration-list": "`database._MIGRATION_FILES`",
    "3-one-alter": "Keep one ALTER per migration file",
    "3-database-imports": "`database.py` never imports `advisors.*`",
    "4-bot-state-writers": "`bot_state` has writers in two processes",
    "4-bot-state-shape": "symphony-entry shape check",
    "5-auth-csrf": "global `before_request` hooks",
    "5-settings-allowlist": "settings write allowlist",
    "5-body-cap": "64 KiB",
    "5-route-degrade": "honest empty state with HTTP 200",
    "5-exception-type": "never `str(exc)`",
    "5-none-handling": "`is None` checks",
    "5-name-keyed-writes": "normalized display name",
    "6-lazy-imports": "imported inside functions in `app.py`",
    "6-blast-radius": "blast-radius allow-list test",
    "6-engine-no-io": "1-minute engine path allows no blocking I/O",
    "7-never-raises": "Advisory modules never raise",
    "7-no-trade-scan": "AST scans are extended to every new helper",
    "8-memory-cap": "`MemoryMax` at 3G",
    "8-memory-dropin": "its own memory drop-in in `deploy/`",
    "8-host-timezone": "proves the host timezone",
    "9-composer-413": "HTTP 413",
    "9-rate-limits": "existing 429 handling",
    "9-backtest-dates": "`start_date` and `end_date`",
    "9-put-symphony": "`PUT /symphonies/{id}`",
    "9-counterfactual": "counterfactual, never realized history",
    "10-wal-single-writer": "WAL allows one writer at a time",
    "10-select-then-insert": "SELECT-then-INSERT",
    "10-three-databases": "three SQLite files",
    "11-db-path-sentinel": "per-xdist-worker `DB_PATH`",
    "11-auth-csrf-fixtures": "re-enables the flag",
    "11-markers": "deselect the live, slow and perf markers",
    "11-worker-cap": "Never plan `-n auto` or more than 4 workers",
    "11-js-syntax": "single parametrized test module",
    "11-math-golden": "golden fixture",
    "11-ruff-length": "Ruff line length is 100",
    "12-live-functional-check": "PM's live functional check",
    "13-prose": "No em dashes, en dashes or spaced hyphens",
}

DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
ID_RE = re.compile(r"\b(?:DE|AC|ARCH|MA|BL|F)-[A-Z0-9]+(?:-[A-Z0-9]+)*\b|\bPR\s*#\d+|\bAC-\d+\b")
HISTORY_WORDS_RE = re.compile(
    r"\b(?:previously|formerly|used to|no longer|was removed|was fixed|incident|operator ruling)\b",
    re.IGNORECASE,
)
MACHINE_LOCAL_RE = re.compile(r"[A-Za-z]:\\|/Users/|/home/|paulm", re.IGNORECASE)
PLACEHOLDER_RE = re.compile(r"<[A-Za-z][^<>\n]*>|\b(?:TODO|TBD|FIXME|XXX)\b")
SPACED_HYPHEN_RE = re.compile(r"(?<=\S) - (?=\S)")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Return (frontmatter dict, body). Supports ``key: value`` and ``- item`` lists."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("file does not open with a frontmatter fence")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise ValueError("frontmatter fence never closes") from None
    fm: dict[str, object] = {}
    current: str | None = None
    for raw in lines[1:end]:
        if not raw.strip():
            continue
        if raw.startswith("  - ") or raw.startswith("- "):
            if current is None or not isinstance(fm.get(current), list):
                raise ValueError(f"list item without a list key: {raw!r}")
            fm[current].append(raw.split("- ", 1)[1].strip())  # type: ignore[union-attr]
            continue
        key, sep, value = raw.partition(":")
        if not sep:
            raise ValueError(f"frontmatter line without a colon: {raw!r}")
        key, value = key.strip(), value.strip()
        current = key
        fm[key] = value.strip('"') if value else []
    return fm, "\n".join(lines[end + 1 :])


def bindings_section(text: str) -> str:
    """Text after the last ``## Project bindings (...)`` heading, or empty."""
    matches = list(re.finditer(r"^## Project bindings \([^)]*\)[ \t\r]*$", text, re.MULTILINE))
    return text[matches[-1].end() :] if matches else ""


# ---------------------------------------------------------------------------
# Checks: each returns a list of problems (empty means the rule holds)
# ---------------------------------------------------------------------------


def check_frontmatter(text: str, stem: str = "architect") -> list[str]:
    try:
        fm, _ = split_frontmatter(text)
    except ValueError as exc:
        return [f"frontmatter does not parse: {exc}"]
    problems = []
    if fm.get("name") != stem:
        problems.append(f"name is {fm.get('name')!r}, expected {stem!r}")
    description = fm.get("description")
    if not isinstance(description, str) or not description.strip():
        problems.append("description missing or empty")
    elif len(re.findall(r"[.!?](?:\s|$)", description)) > 2:
        problems.append("description is longer than two sentences")
    if "mcp__" in str(fm.get("tools", "")) and "mcpServers" not in fm:
        problems.append("tools names an mcp__ entry but mcpServers is absent")
    for forbidden in ("memory", "mcpServers"):
        if forbidden in fm:
            problems.append(f"frontmatter carries {forbidden}, which this role does not use")
    return problems


def check_tools(text: str) -> list[str]:
    fm, _ = split_frontmatter(text)
    tools = [t.strip() for t in str(fm.get("tools", "")).split(",") if t.strip()]
    if tools != EXPECTED_TOOLS:
        return [f"tools is {tools}, expected {EXPECTED_TOOLS}"]
    return []


def check_pins_and_skills(text: str) -> list[str]:
    fm, _ = split_frontmatter(text)
    problems = []
    if fm.get("model") != EXPECTED_MODEL:
        problems.append(f"model is {fm.get('model')!r}, expected {EXPECTED_MODEL!r}")
    if fm.get("effort") != EXPECTED_EFFORT:
        problems.append(f"effort is {fm.get('effort')!r}, expected {EXPECTED_EFFORT!r}")
    if fm.get("skills") != EXPECTED_SKILLS:
        problems.append(f"skills is {fm.get('skills')!r}, expected {EXPECTED_SKILLS!r}")
    return problems


def check_compiled_shape(text: str) -> list[str]:
    problems = []
    provenance = re.findall(
        r"^<!-- compiled-from: ~/\.claude/agents/architect\.md sha256:[0-9a-f]{12} -->$",
        text,
        re.MULTILINE,
    )
    if len(provenance) != 1:
        problems.append(f"expected exactly one provenance line, found {len(provenance)}")
    headings = re.findall(r"^## Project bindings \([^)]*\)[ \t\r]*$", text, re.MULTILINE)
    if headings != [BINDINGS_HEADING]:
        problems.append(f"bindings headings are {headings}, expected [{BINDINGS_HEADING!r}]")
    if re.search(r"^#+\s*Extends\b|^Extends\s+`?~/", text, re.MULTILINE):
        problems.append("file opens with an Extends pointer instead of a compiled body")
    return problems


def missing_bindings(text: str) -> list[str]:
    section = bindings_section(text)
    return [key for key, anchor in BINDING_ANCHORS.items() if anchor not in section]


def check_no_placeholder(text: str) -> list[str]:
    section = bindings_section(text)
    return [f"placeholder: {m.group(0)!r}" for m in PLACEHOLDER_RE.finditer(section)]


def check_no_history_text(text: str) -> list[str]:
    problems = []
    for label, regex in (
        ("date", DATE_RE),
        ("decision or plan id", ID_RE),
        ("history wording", HISTORY_WORDS_RE),
        ("machine-local term", MACHINE_LOCAL_RE),
    ):
        problems += [f"{label}: {m.group(0)!r}" for m in regex.finditer(text)]
    return problems


def check_prose_dashes(text: str) -> list[str]:
    section = bindings_section(text)
    problems = [f"dash character {ch!r}" for ch in ("\u2014", "\u2013") if ch in section]
    problems += [f"spaced hyphen: {m.group(0)!r}" for m in SPACED_HYPHEN_RE.finditer(section)]
    return problems


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_text() -> str:
    assert AGENT_FILE.is_file(), f"agent file missing: {AGENT_FILE}"
    return AGENT_FILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Checks against the real file, each with its mutation control
# ---------------------------------------------------------------------------


def test_agent_file_is_flat_and_registered_by_stem(agent_text):
    assert AGENT_FILE.parent.name == "agents"
    assert not (AGENT_FILE.parent / "architect" / "AGENT.md").exists()


def test_frontmatter_parses(agent_text):
    assert check_frontmatter(agent_text) == []


def test_frontmatter_mutation_control(agent_text):
    broken = agent_text.replace("name: architect", "name: architect-v2", 1)
    assert check_frontmatter(broken), "wrong name must be flagged"
    assert check_frontmatter(agent_text.replace("---", "", 1)), "missing fence must be flagged"
    assert check_frontmatter(agent_text.replace("\ntools:", "\nmemory: project\ntools:", 1))


def test_tools_match_the_design(agent_text):
    assert check_tools(agent_text) == []


def test_tools_mutation_control(agent_text):
    removed = agent_text.replace(
        "Read, Glob, Grep, Bash, Write, Edit,", "Read, Glob, Grep, Bash,", 1
    )
    assert check_tools(removed), "a removed tool must be flagged"
    added = agent_text.replace("TaskList\n", "TaskList, WebFetch\n", 1)
    assert check_tools(added), "an added tool must be flagged"


def test_model_effort_and_skills_follow_the_template(agent_text):
    assert check_pins_and_skills(agent_text) == []


def test_pins_mutation_control(agent_text):
    assert check_pins_and_skills(agent_text.replace("model: opus", "model: sonnet", 1))
    assert check_pins_and_skills(agent_text.replace("effort: high", "effort: low", 1))


def test_compiled_shape(agent_text):
    assert check_compiled_shape(agent_text) == []


def test_compiled_shape_mutation_control(agent_text):
    no_heading = agent_text.replace(BINDINGS_HEADING, "## Bindings", 1)
    assert check_compiled_shape(no_heading)
    doubled = agent_text.replace(
        "-->\n\n# Architect",
        "-->\n<!-- compiled-from: ~/.claude/agents/architect.md sha256:000000000000 -->\n\n# Architect",
        1,
    )
    assert check_compiled_shape(doubled)


def test_every_project_binding_is_supplied(agent_text):
    assert missing_bindings(agent_text) == []


@pytest.mark.parametrize("key", sorted(BINDING_ANCHORS))
def test_binding_mutation_control(agent_text, key):
    """Removing one binding's anchor text must make exactly that binding missing."""
    broken = agent_text.replace(BINDING_ANCHORS[key], "REMOVED")
    assert key in missing_bindings(broken)


def test_binding_anchors_cover_all_thirteen_binding_groups():
    groups = {int(k.split("-", 1)[0]) for k in BINDING_ANCHORS}
    assert groups == set(range(1, 14))


def test_no_placeholder_in_bindings(agent_text):
    assert check_no_placeholder(agent_text) == []


def test_placeholder_mutation_control(agent_text):
    assert check_no_placeholder(agent_text + "\n- Framework: <framework>\n")
    assert check_no_placeholder(agent_text + "\n- TODO fill in\n")


def test_no_history_text(agent_text):
    assert check_no_history_text(agent_text) == []


@pytest.mark.parametrize(
    "injection",
    [
        "Fixed on 2026-01-02.",
        "See DE-EXAMPLE-001 for the decision.",
        "Previously the route raised.",
        "Path C:\\Users\\someone\\repo.",
        "Closes PR #12.",
    ],
)
def test_history_mutation_control(agent_text, injection):
    assert check_no_history_text(agent_text + "\n" + injection + "\n")


def test_prose_has_no_em_dash_en_dash_or_spaced_hyphen(agent_text):
    assert check_prose_dashes(agent_text) == []


@pytest.mark.parametrize("injection", ["a \u2014 b", "a \u2013 b", "a - b"])
def test_prose_mutation_control(agent_text, injection):
    assert check_prose_dashes(agent_text + "\n" + injection + "\n")


# ---------------------------------------------------------------------------
# Real child-process case: the harness compile --check on the compiled file
# ---------------------------------------------------------------------------


def _run_check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(COMPILE_SCRIPT), "--check", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _require_harness() -> None:
    if not COMPILE_SCRIPT.is_file():
        pytest.skip(f"harness script not installed: {COMPILE_SCRIPT}")
    if not TEMPLATE_FILE.is_file():
        pytest.skip(f"harness template not installed: {TEMPLATE_FILE}")


def test_compile_check_exits_zero_on_the_compiled_file():
    _require_harness()
    result = _run_check(AGENT_FILE)
    assert result.returncode == 0, (
        f"compile --check exit={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )


def test_compile_check_flags_a_stale_copy(agent_text, tmp_path):
    """Mutation control: a tampered provenance hash must make the same command fail."""
    _require_harness()
    tampered = tmp_path / "architect.md"
    tampered.write_text(
        re.sub(r"sha256:[0-9a-f]{12}", "sha256:000000000000", agent_text, count=1),
        encoding="utf-8",
    )
    result = _run_check(tampered)
    assert result.returncode == 1, (
        f"stale copy should exit 1, got {result.returncode} stderr={result.stderr!r}"
    )
