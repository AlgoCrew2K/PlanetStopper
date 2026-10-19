"""Contract test for the spec-auditor agent definition.

The agent file is a compiled specialization: the global template body plus a
``## Project bindings`` section. Each check below is one pure function that
returns a list of violations; the real file must produce none, and each check
has a mutation control proving a deliberately broken variant is caught.

The one real child-process case runs the harness compiler's ``--check`` on the
compiled file. It skips when the harness scripts are not installed (CI runners
do not have them); every other check runs everywhere.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_PATH = REPO_ROOT / ".claude" / "agents" / "spec-auditor.md"
CHECKLIST_PATH = REPO_ROOT / ".claude" / "agents" / "_refs" / "spec-review-checklist.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"

EXPECTED_TOOLS = [
    "Read",
    "Glob",
    "Grep",
    "Bash",
    "Write",
    "SendMessage",
    "TaskUpdate",
    "TaskList",
]
EXPECTED_MODEL = "opus"
EXPECTED_EFFORT = "high"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"

# One distinctive phrase per project binding the parent template asks the
# project to supply. Each must appear in the bindings section.
BINDING_ANCHORS = {
    "ground-truth-at-ref": "Ground truth is the code at the brief's ref",
    "evidence-ranks-by-execution": "Evidence ranks by what executes",
    "append-only-decision-log": "`DECISIONS.md` is append-only",
    "large-files-searched": "about 72k tokens",
    "production-data-is-question": "Production data shape is a QUESTION",
    "plan-format-legacy-shape": "legacy shape",
    "earlier-criteria-suffixes": ".obsolete.md",
    "migration-numbers": "database._MIGRATION_FILES",
    "no-trade-boundary": "No-trade boundary",
    "live-trading-two-flags": "`LIVE_EXECUTION` AND the symphony's `live_mode`",
    "post-route-inventory": "full POST route inventory",
    "refusal-chain": "64 KiB",
    "auth-csrf-re-enabled": "re-enables the flag",
    "none-distinct-from-zero": "stays distinct from 0",
    "counterfactual-vs-realized": "never presented as realized history",
    "recurring-defect-classes": "Defect classes this repo repeats",
    "engine-zero-diff-guard": "needs a named mechanical guard",
    "sensitive-marking": "Sensitive marking",
    "bounded-verification-runs": "more than 4 xdist workers",
}

# Text that carries history or identity instead of a rule and its reason.
HISTORY_PATTERNS = {
    "iso-date": r"\b\d{4}-\d{2}-\d{2}\b",
    "decision-or-criterion-id": r"\b(?:DE|AC|ADR)-[A-Z0-9][A-Z0-9-]*\b",
    "pr-number": r"\bPR\s?#\d+|#\d{2,}\b",
    "attribution-phrase": (
        r"(?i)operator ruling|operator directive|per the operator|"
        r"was approved|approved:|incident|postmortem|post-mortem"
    ),
    "machine-local-path": r"[A-Za-z]:\\|/Users/|/home/|~/\.claude",
    "person-name": r"(?i)\bpaulm\b|\bpaul\b",
}
PLACEHOLDER = re.compile(r"<[A-Za-z][^<>]*>")


# --------------------------------------------------------------------------
# Pure checks. Each returns a list of violation strings; empty means pass.
# --------------------------------------------------------------------------


def split_frontmatter(text: str) -> tuple[dict[str, str], str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            fields: dict[str, str] = {}
            for line in lines[1:index]:
                if line and not line[0].isspace() and ":" in line:
                    key, _, value = line.partition(":")
                    fields[key.strip()] = value.strip()
            return fields, "\n".join(lines[index + 1 :])
    return None


def bindings_section(text: str) -> str:
    start = text.find(BINDINGS_HEADING)
    return "" if start < 0 else text[start:]


def check_frontmatter_parses(text: str) -> list[str]:
    parsed = split_frontmatter(text)
    if parsed is None:
        return ["frontmatter fences missing or unclosed"]
    fields, _ = parsed
    problems = [
        f"missing key: {key}" for key in ("name", "description", "tools") if key not in fields
    ]
    if fields.get("name") != AGENT_PATH.stem:
        problems.append(f"name {fields.get('name')!r} does not match file stem")
    description = fields.get("description", "").strip('"')
    if "PlanetStopper" not in description or "spec-writer" not in description:
        problems.append("description must name the project and the paired spec-writer")
    return problems


def check_tools(text: str) -> list[str]:
    parsed = split_frontmatter(text)
    if parsed is None:
        return ["no frontmatter"]
    tools = [item.strip() for item in parsed[0].get("tools", "").split(",") if item.strip()]
    if tools != EXPECTED_TOOLS:
        return [f"tools {tools} != {EXPECTED_TOOLS}"]
    return []


def check_model_and_effort_follow_template(text: str) -> list[str]:
    parsed = split_frontmatter(text)
    if parsed is None:
        return ["no frontmatter"]
    fields, _ = parsed
    problems = []
    if fields.get("model") != EXPECTED_MODEL:
        problems.append(f"model {fields.get('model')!r} != {EXPECTED_MODEL!r}")
    if fields.get("effort") != EXPECTED_EFFORT:
        problems.append(f"effort {fields.get('effort')!r} != {EXPECTED_EFFORT!r}")
    record = re.search(r"<!-- template-owned pins: ([^>]*) -->", text)
    owned = record.group(1) if record else ""
    for pin in ("model@", "effort@"):
        if pin not in owned:
            problems.append(f"{pin.rstrip('@')} is not recorded as template-owned")
    return problems


def check_no_memory_or_mcp(text: str) -> list[str]:
    parsed = split_frontmatter(text)
    if parsed is None:
        return ["no frontmatter"]
    return [f"unexpected key: {key}" for key in ("memory", "mcpServers") if key in parsed[0]]


def check_bindings_supplied(text: str) -> list[str]:
    section = bindings_section(text)
    if not section:
        return ["bindings heading missing"]
    return [name for name, anchor in BINDING_ANCHORS.items() if anchor not in section]


def check_no_placeholder(text: str) -> list[str]:
    parsed = split_frontmatter(text)
    head = "" if parsed is None else "\n".join(f"{k}: {v}" for k, v in parsed[0].items())
    return PLACEHOLDER.findall(head) + PLACEHOLDER.findall(bindings_section(text))


def check_no_history_text(text: str) -> list[str]:
    parsed = split_frontmatter(text)
    head = "" if parsed is None else "\n".join(f"{k}: {v}" for k, v in parsed[0].items())
    scanned = head + "\n" + bindings_section(text)
    return [name for name, pattern in HISTORY_PATTERNS.items() if re.search(pattern, scanned)]


def check_compiled_shape(text: str) -> list[str]:
    problems = []
    if len(re.findall(r"^<!-- compiled-from: ", text, flags=re.M)) != 1:
        problems.append("expected exactly one compiled-from provenance line")
    if text.count("## Project bindings") != 1:
        problems.append("expected exactly one bindings heading")
    if BINDINGS_HEADING not in text:
        problems.append("bindings heading does not name the project")
    if re.search(r"^#+\s*Extends\b", text, flags=re.M):
        problems.append("stub-style Extends section present")
    return problems


def check_checklist_copy(path: Path) -> list[str]:
    if not path.is_file():
        return [f"project checklist copy missing: {path.name}"]
    if not path.read_text(encoding="utf-8").strip():
        return ["project checklist copy is empty"]
    return []


# --------------------------------------------------------------------------
# Real-file checks and one mutation control per check.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_text() -> str:
    return AGENT_PATH.read_text(encoding="utf-8")


def test_frontmatter_parses(agent_text):
    assert check_frontmatter_parses(agent_text) == []


def test_frontmatter_parses_mutation(agent_text):
    broken = agent_text.replace("\n---\n", "\n", 1)
    assert check_frontmatter_parses(broken) != []
    assert (
        check_frontmatter_parses(agent_text.replace("name: spec-auditor", "name: other", 1)) != []
    )


def test_tools_match_design(agent_text):
    assert check_tools(agent_text) == []


def test_tools_mutation(agent_text):
    added = agent_text.replace("TaskList\n", "TaskList, Edit\n", 1)
    removed = agent_text.replace("Bash, ", "", 1)
    assert check_tools(added) != []
    assert check_tools(removed) != []


def test_model_and_effort_follow_template(agent_text):
    assert check_model_and_effort_follow_template(agent_text) == []


def test_model_and_effort_mutation(agent_text):
    assert (
        check_model_and_effort_follow_template(
            agent_text.replace("model: opus", "model: sonnet", 1)
        )
        != []
    )
    assert (
        check_model_and_effort_follow_template(agent_text.replace("effort: high", "effort: low", 1))
        != []
    )
    unowned = re.sub(
        r"<!-- template-owned pins: [^>]*-->", "<!-- template-owned pins: none -->", agent_text
    )
    assert check_model_and_effort_follow_template(unowned) != []


def test_no_memory_or_mcp(agent_text):
    assert check_no_memory_or_mcp(agent_text) == []


def test_no_memory_or_mcp_mutation(agent_text):
    assert check_no_memory_or_mcp(agent_text.replace("tools:", "memory: project\ntools:", 1)) != []
    assert check_no_memory_or_mcp(agent_text.replace("tools:", "mcpServers:\ntools:", 1)) != []


def test_every_binding_supplied(agent_text):
    assert check_bindings_supplied(agent_text) == []


@pytest.mark.parametrize("name", sorted(BINDING_ANCHORS))
def test_binding_removal_is_detected(agent_text, name):
    section_start = agent_text.find(BINDINGS_HEADING)
    head, section = agent_text[:section_start], agent_text[section_start:]
    mutated = head + section.replace(BINDING_ANCHORS[name], "REMOVED")
    assert check_bindings_supplied(mutated) == [name]


def test_bindings_anchor_in_template_body_does_not_count(agent_text):
    # An anchor that appears only above the bindings heading is not a binding.
    section_start = agent_text.find(BINDINGS_HEADING)
    stripped = agent_text[:section_start] + BINDINGS_HEADING + "\n"
    assert set(check_bindings_supplied(stripped)) == set(BINDING_ANCHORS)


def test_no_placeholder(agent_text):
    assert check_no_placeholder(agent_text) == []


def test_no_placeholder_mutation(agent_text):
    assert check_no_placeholder(agent_text + "\n- Design system: <project design system>\n") != []
    assert check_no_placeholder(agent_text.replace("tools:", "extra: <value>\ntools:", 1)) != []


def test_no_history_text(agent_text):
    assert check_no_history_text(agent_text) == []


@pytest.mark.parametrize(
    ("name", "injected"),
    [
        ("iso-date", "- Rule recorded 2026-01-02.\n"),
        ("decision-or-criterion-id", "- Rule from DE-EXAMPLE-001.\n"),
        ("pr-number", "- Rule from PR #123.\n"),
        ("attribution-phrase", "- Rule per the operator.\n"),
        ("machine-local-path", "- Rule at C:\\work\\repo.\n"),
        ("person-name", "- Rule asked for by Paul.\n"),
    ],
)
def test_history_text_mutation(agent_text, name, injected):
    assert name in check_no_history_text(agent_text + "\n" + injected)


def test_compiled_shape(agent_text):
    assert check_compiled_shape(agent_text) == []


def test_compiled_shape_mutation(agent_text):
    no_provenance = re.sub(r"^<!-- compiled-from: .*$", "", agent_text, flags=re.M)
    assert check_compiled_shape(no_provenance) != []
    assert check_compiled_shape(agent_text + "\n## Project bindings (Other)\n") != []
    assert check_compiled_shape(agent_text + "\n# Extends the template\n") != []


def test_project_checklist_copy_present():
    assert check_checklist_copy(CHECKLIST_PATH) == []


def test_project_checklist_copy_mutation(tmp_path):
    assert check_checklist_copy(tmp_path / "missing.md") != []
    empty = tmp_path / "empty.md"
    empty.write_text("  \n", encoding="utf-8")
    assert check_checklist_copy(empty) != []


# --------------------------------------------------------------------------
# Real child process: the harness compiler's freshness check.
# --------------------------------------------------------------------------


def _run_compiler_check(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(COMPILER), "--check", str(target)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_compile_check_exits_zero():
    if not COMPILER.is_file():
        pytest.skip("harness compiler not installed on this machine")
    result = _run_compiler_check(AGENT_PATH)
    assert result.returncode == 0, result.stdout + result.stderr


def test_compile_check_fails_without_provenance(tmp_path, agent_text):
    if not COMPILER.is_file():
        pytest.skip("harness compiler not installed on this machine")
    tampered = tmp_path / "spec-auditor.md"
    tampered.write_text(
        re.sub(r"^<!-- compiled-from: .*$", "", agent_text, flags=re.M), encoding="utf-8"
    )
    result = _run_compiler_check(tampered)
    assert result.returncode != 0, "check must fail on a file without provenance"
