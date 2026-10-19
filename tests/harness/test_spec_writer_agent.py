"""Contract tests for the project spec-writer agent definition.

Each rule the file must keep is one pure checker returning a list of problems, plus one
mutation control that feeds the checker a deliberately broken variant and asserts it
reports a problem (a check that cannot fail proves nothing). One real child-process case
runs the compile freshness check; it is skipped where the harness scripts are not installed.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "spec-writer.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "spec-writer" / "AGENT.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"

EXPECTED_TOOLS = ["Read", "Grep", "Glob", "Bash", "WebFetch", "Write", "Edit", "SendMessage"]
EXPECTED_MODEL = "opus"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"

# One entry per binding the project must supply: (label, substrings that must all appear).
REQUIRED_BINDINGS = [
    ("Plans root and template", ["feature-plans/", "/scaffold", "architect's Gate-2"]),
    ("Project guidance and decision sources", [".claude/CLAUDE.md", "DECISIONS.md", "SUPERSEDED"]),
    ("Ground truth", ["git rev-parse HEAD", "git status -sb", "git show", "line drift"]),
    ("Earlier criteria on the same surface", [".obsolete.md", "docs/audit-inputs/", "AC ids"]),
    (
        "No-trade boundary",
        [
            "AI Advisor",
            "proposal-approve",
            "panic-sell",
            "Managed Sleeves",
            "LIVE_EXECUTION",
            "live_mode",
            "is_live",
            "protected surface",
        ],
    ),
    ("Execution engine", ["alpha_bot_execution.py", "math_engine.py", "blocking I/O"]),
    (
        "Data honesty observables",
        [
            "basis, unit, window, subject and source",
            "`None` stays distinct from `0`",
            "render consumer",
            "counterfactual",
            "go-live floor",
            "invested-since",
        ],
    ),
    ("Identifier and unit rules", ["Composer hash", "normalized display name", "percent-scale"]),
    ("Production-data facts", ["Known-Unconfirmed", "read-only query", "live read"]),
    ("Sensitive marking", ["CSRF", "settings allowlist", "credentials", "display-only"]),
    (
        "Definition of Done names",
        [
            "ruff format --check",
            "ruff check",
            "-n auto",
            "4 workers",
            "CI on the exact SHA",
            "live functional check",
            "captured producer output",
        ],
    ),
    (
        "Docs to Update names",
        [
            "docs/generated/",
            "docs/generated/INDEX.md",
            "README.md",
            "DECISIONS.md",
            "doc-writer",
            "Key Files row",
        ],
    ),
    ("Prose", ["em dashes", "en dashes", "spaced hyphens", "invented names"]),
]

HISTORY_PATTERNS = {
    "iso date": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    "decision id": re.compile(r"\bDE-[A-Z0-9][A-Z0-9-]*\b"),
    "pull request number": re.compile(r"\bPR\s?#\d+|#\d{2,}\b"),
    "attribution": re.compile(
        r"operator (ruling|directive|verbatim)|ruled by|per the operator", re.I
    ),
    "history wording": re.compile(r"\b(previously|used to|was removed|was fixed|incident)\b", re.I),
}
MACHINE_PATTERNS = {
    "windows path": re.compile(r"[A-Za-z]:[\\/]"),
    "user home path": re.compile(r"/Users/|/home/\w+", re.I),
    "user name": re.compile(r"paulm", re.I),
}
PROSE_PATTERNS = {
    "em dash": re.compile("\u2014"),
    "en dash": re.compile("\u2013"),
    "spaced hyphen": re.compile(r"[ \t]-[ \t]"),
}
PLACEHOLDER = re.compile(r"<[A-Za-z][^<>]*>")


def read_agent() -> str:
    return AGENT_FILE.read_text(encoding="utf-8")


def split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("file does not open with a frontmatter fence")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("frontmatter fence never closes")
    keys: dict[str, str] = {}
    for line in lines[1:end]:
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):[ \t]*(.*)$", line)
        if m:
            keys[m.group(1)] = m.group(2).strip().strip('"')
    return keys, "\n".join(lines[end + 1 :])


def bindings_section(text: str) -> str:
    idx = text.rfind(BINDINGS_HEADING)
    return text[idx:] if idx >= 0 else ""


# ---- checkers: each returns a list of problems, empty when the rule holds ----


def check_frontmatter(text: str) -> list[str]:
    try:
        keys, _ = split_frontmatter(text)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    if keys.get("name") != "spec-writer":
        problems.append(f"name is {keys.get('name')!r}, expected 'spec-writer'")
    desc = keys.get("description", "")
    if not desc:
        problems.append("description missing")
    elif "spec-auditor" not in desc or "feature-plans/" not in desc:
        problems.append("description does not name the paired auditor and the plans folder")
    return problems


def check_tools(text: str) -> list[str]:
    keys, _ = split_frontmatter(text)
    tools = [t.strip() for t in keys.get("tools", "").split(",") if t.strip()]
    if tools != EXPECTED_TOOLS:
        return [f"tools {tools} differ from the parent's {EXPECTED_TOOLS}"]
    return []


def check_model(text: str) -> list[str]:
    keys, _ = split_frontmatter(text)
    problems = []
    if keys.get("model") != EXPECTED_MODEL:
        problems.append(f"model is {keys.get('model')!r}, expected {EXPECTED_MODEL!r}")
    if keys.get("effort") != "high":
        problems.append(f"effort is {keys.get('effort')!r}, expected 'high'")
    return problems


def check_no_unrequested_keys(text: str) -> list[str]:
    keys, _ = split_frontmatter(text)
    return [f"unrequested key {k!r}" for k in ("memory", "mcpServers") if k in keys]


def check_flat_layout(agent_file: Path, nested: Path) -> list[str]:
    problems = []
    if not agent_file.is_file():
        problems.append("flat agent file missing")
    if nested.exists():
        problems.append("nested AGENT.md layout present; the registry cannot dispatch it")
    return problems


def check_compiled_shape(text: str) -> list[str]:
    problems = []
    if len(re.findall(r"^<!-- compiled-from: ", text, re.M)) != 1:
        problems.append("expected exactly one compiled-from provenance line")
    if len(re.findall(r"^## Project bindings \(", text, re.M)) != 1:
        problems.append("expected exactly one Project bindings heading")
    if BINDINGS_HEADING not in text:
        problems.append(f"missing heading {BINDINGS_HEADING!r}")
    if re.search(r"^#{1,6}\s*Extends\b", text, re.M):
        problems.append("file opens an Extends section; it must be a compiled specialization")
    return problems


def check_bindings_supplied(text: str) -> list[str]:
    section = bindings_section(text)
    problems = []
    for label, needles in REQUIRED_BINDINGS:
        if f"**{label}" not in section:
            problems.append(f"binding {label!r} missing")
            continue
        problems += [f"binding {label!r} lacks {n!r}" for n in needles if n not in section]
    return problems


def check_no_placeholder(text: str) -> list[str]:
    section = bindings_section(text)
    if not section:
        return ["bindings section missing"]
    return [f"placeholder {m!r}" for m in PLACEHOLDER.findall(section)]


def check_no_history_or_machine_text(text: str) -> list[str]:
    section = bindings_section(text)
    if not section:
        return ["bindings section missing"]
    problems = []
    for kind, pattern in {**HISTORY_PATTERNS, **MACHINE_PATTERNS}.items():
        problems += [f"{kind}: {m.group(0)!r}" for m in pattern.finditer(section)]
    return problems


def check_prose_rules(text: str) -> list[str]:
    section = bindings_section(text)
    if not section:
        return ["bindings section missing"]
    problems = []
    for kind, pattern in PROSE_PATTERNS.items():
        problems += [f"{kind} at offset {m.start()}" for m in pattern.finditer(section)]
    return problems


# ---- rule checks on the real file ----


def test_frontmatter_parses():
    assert check_frontmatter(read_agent()) == []


def test_tools_match_parent():
    assert check_tools(read_agent()) == []


def test_model_and_effort_follow_template():
    assert check_model(read_agent()) == []


def test_no_memory_or_mcp_keys():
    assert check_no_unrequested_keys(read_agent()) == []


def test_flat_layout():
    assert check_flat_layout(AGENT_FILE, NESTED_LAYOUT) == []


def test_compiled_shape():
    assert check_compiled_shape(read_agent()) == []


def test_every_binding_supplied():
    assert check_bindings_supplied(read_agent()) == []


def test_no_placeholder_in_bindings():
    assert check_no_placeholder(read_agent()) == []


def test_no_history_or_machine_text_in_bindings():
    assert check_no_history_or_machine_text(read_agent()) == []


def test_prose_rules_hold_in_bindings():
    assert check_prose_rules(read_agent()) == []


# ---- mutation controls: each check must fail on a deliberately broken variant ----


def test_mutation_frontmatter_missing_fence():
    broken = read_agent().replace("---", "", 1)
    assert check_frontmatter(broken) != []


def test_mutation_frontmatter_wrong_name():
    broken = read_agent().replace("name: spec-writer", "name: other-writer", 1)
    assert check_frontmatter(broken) != []


def test_mutation_tools_extra_tool():
    broken = read_agent().replace("SendMessage\n", "SendMessage, Skill\n", 1)
    assert check_tools(broken) != []


def test_mutation_tools_dropped_tool():
    broken = read_agent().replace("Edit, SendMessage", "SendMessage", 1)
    assert check_tools(broken) != []


def test_mutation_model_changed():
    broken = read_agent().replace("model: opus", "model: sonnet", 1)
    assert check_model(broken) != []


def test_mutation_memory_key_added():
    broken = read_agent().replace("model: opus", "model: opus\nmemory: project", 1)
    assert check_no_unrequested_keys(broken) != []


def test_mutation_nested_layout_present(tmp_path):
    nested = tmp_path / "spec-writer" / "AGENT.md"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert check_flat_layout(AGENT_FILE, nested) != []
    assert check_flat_layout(tmp_path / "absent.md", tmp_path / "none.md") != []


def test_mutation_compiled_shape_extends_section():
    broken = read_agent().replace(BINDINGS_HEADING, BINDINGS_HEADING + "\n\n# Extends `x`", 1)
    assert check_compiled_shape(broken) != []


def test_mutation_compiled_shape_no_provenance():
    broken = re.sub(r"^<!-- compiled-from: .*$", "", read_agent(), flags=re.M)
    assert check_compiled_shape(broken) != []


@pytest.mark.parametrize("label", [label for label, _ in REQUIRED_BINDINGS])
def test_mutation_each_binding_removal_detected(label):
    text = read_agent()
    start = text.index(f"- **{label}")
    end = text.find("\n- **", start + 1)
    broken = text[:start] + (text[end + 1 :] if end >= 0 else "")
    assert check_bindings_supplied(broken) != []


def test_mutation_binding_missing_key_phrase():
    broken = read_agent().replace("LIVE_EXECUTION", "LIVE_FLAG")
    assert check_bindings_supplied(broken) != []


def test_mutation_placeholder_added():
    broken = read_agent() + "\n- Plans go to <plan-name>.\n"
    assert check_no_placeholder(broken) != []


@pytest.mark.parametrize(
    "injected",
    [
        "Decided on 2026-01-02.",
        "See DE-EXAMPLE-001 for the ruling.",
        "Added in PR #123.",
        "Per the operator directive, never do this.",
        "This was fixed after an incident.",
        "Stored at C:\\Users\\someone\\file.",
        "Stored at /Users/someone/file.",
        "Owner is paulm.",
    ],
)
def test_mutation_history_or_machine_text_detected(injected):
    broken = read_agent() + f"\n- {injected}\n"
    assert check_no_history_or_machine_text(broken) != []


@pytest.mark.parametrize(
    "injected",
    ["a dash \u2014 here", "a range 1\u20132", "a spaced - hyphen"],
)
def test_mutation_prose_rule_detected(injected):
    broken = read_agent() + f"\n- {injected}\n"
    assert check_prose_rules(broken) != []


def test_mutation_bindings_section_missing():
    broken = read_agent().replace(BINDINGS_HEADING, "## Something else")
    assert check_no_placeholder(broken) != []
    assert check_no_history_or_machine_text(broken) != []
    assert check_prose_rules(broken) != []
    assert check_bindings_supplied(broken) != []


# ---- real child-process case ----


def require_compiler() -> None:
    if not COMPILER.is_file():
        pytest.skip(
            "harness script compile-agent-specialization.py is not installed on this runner"
        )


def run_compile_check(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(COMPILER), "--check", str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_compile_check_exits_zero_on_real_file():
    require_compiler()
    result = run_compile_check(AGENT_FILE)
    assert result.returncode == 0, result.stderr


def test_compile_check_mutation_stale_provenance_fails(tmp_path):
    require_compiler()
    stale = tmp_path / "spec-writer.md"
    text = read_agent()
    stale.write_text(
        re.sub(r"sha256:[0-9a-f]{12}", "sha256:000000000000", text, count=1), encoding="utf-8"
    )
    result = run_compile_check(stale)
    assert result.returncode == 1, (result.returncode, result.stderr)
