"""Contract test for the qa agent definition.

The agent file is a compiled specialization: the global template body plus a
``## Project bindings`` section. Each check below is one pure function that
returns a list of violations; the real file must produce none, and each check
has a mutation control proving a deliberately broken variant is caught.

The compiler's ``--check`` verifies provenance, not body presence, so the
template-body checks here are the only guard against a body that was cut out.
The review-artifact paragraph check matters more for qa than for the planning
agents: the PR gate recognizes a reviewer role by that paragraph (or by the
compiled-from marker), so a body that lost it writes a verdict file the gate
cannot attribute.

Several bindings state facts about this repository (which test files exist,
that the review folder is not gitignored, what the verdict validator demands).
Those facts have their own checks, so a binding cannot quietly go false.

The one real child-process case runs the harness compiler's ``--check`` on the
compiled file. It skips when the harness scripts are not installed (CI runners
do not have them); every other check runs everywhere.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.harness import _agent_contract as contract

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_PATH = REPO_ROOT / ".claude" / "agents" / "qa.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "qa" / "AGENT.md"
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
TEMPLATE_PATH = Path.home() / ".claude" / "agents" / "qa.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
RECORDS_PATH = Path.home() / ".claude" / "hooks" / "_lib" / "records.py"

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
EXPECTED_SKILL = "test-design-contract"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# qa"
DIRECTIVE_MARKER = "**Prime Directive:**"
REVIEW_ARTIFACT_MARKER = "**Review artifact.**"

# One distinctive phrase per sentence of the bindings section. Each occurs exactly once there,
# no phrase sits inside another, and removing one alone must make the rule check report exactly
# that rule. A sentence with no phrase would let a deleted rule pass, so the sentence-deletion
# test below fails when any sentence lacks one.
BINDING_ANCHORS: dict[str, str] = {
    "ground-truth-at-sha": "Ground truth is the code at the brief's SHA",
    "refind-cited-symbols": "Re-find every cited symbol there",
    "large-files-searched": "Large files are searched, not read whole",
    "large-file-sizes": "(about 170 KB) and `DECISIONS.md` (about 11k lines, append-only)",
    "whole-read-truncates": "A whole-file read truncates silently",
    "name-the-plan": "Name the plan with every criterion",
    "cite-plan-and-criterion": "Cite the plan file and the criterion id in each row",
    "test-runs-n0": "Test runs append `-n0` to the default addopts",
    "never-override-addopts": "Never pass `-o addopts=`",
    "named-files-only": "Run only the files the brief names and never the full suite",
    "stage-by-name": "Stage files by name and never with `git add -A`",
    "review-folder-not-ignored": "`.claude/review/` is not gitignored",
    "verdict-files-follow-contract": "Verdict files follow the review artifact contract exactly",
    "verdict-file-keys": "The file is `qa.json` with role `qa`",
    "row-keys": "Every row carries a unique id",
    "pass-with-blocking-denied": "A PASS file holding any row whose `blocking` is not boolean false",
    "quant-reviewer-not-gate-role": "The quant-code-reviewer overlay is not a gate reviewer role",
    "quant-reviewer-by-message": "Its findings reach the PM by message",
    "prove-wiring": "Prove wiring for every criterion that names a visible value",
    "producer-route-consumer": "Find the producer, the route and the template or JS consumer",
    "unrendered-field-is-gap": "A computed field with no render consumer",
    "judge-data-honesty": "Judge data honesty by what is shown",
    "number-states-basis": (
        "A displayed number is SATISFIED only when its period, subject, unit, source and "
        "coverage are stated"
    ),
    "counterfactual-labelled": (
        "Counterfactual figures (shadow history, if-held, backtest) must be labelled against "
        "realized ones"
    ),
    "production-data-not-from-code": "Production-data observables cannot be settled from code",
    "production-data-never-satisfied": (
        "Row counts, history depth, churn, latency and live Composer behaviour are never SATISFIED"
    ),
    "production-data-to-pm": "Send the criterion to the PM as a scope question",
    "security-flag-reenabled": "Security scenarios count only with the flag re-enabled",
    "tests-disable-auth-csrf": "Tests disable auth and CSRF through autouse fixtures",
    "refusal-order": "Refusals arrive in order",
    "advisory-post-200": "Advisory POST routes answer HTTP 200 on failure",
    "judge-by-body-keys": "Judge such a test by the body keys",
    "boundary-scan-reaches-path": "No-trade criteria need a scan that reaches the new path",
    "entry-point-list": "Confirm the new function sits in an existing boundary scan's entry-point list",
    "single-hop-not-proof": "The dashboard no-order test is single-hop",
    "engine-zero-diff-pin": "Engine zero-diff claims need a content pin",
    "engine-pin-named": (
        "A criterion that the engine files are unchanged is SATISFIED only by a named "
        "LF-normalized content pin"
    ),
    "fixture-only-parser-accepts": (
        "Treat a fixture that only its own parser accepts as test-strength evidence"
    ),
    "whole-module-magicmock": "The same goes for a whole-module `MagicMock`",
    "compare-to-producer-captures": "Compare the fixture to the producer captures under `tests/fixtures/`",
    "js-no-execution-harness": "JS rendering has no execution harness here",
    "js-harness-description": "The repo has no browser or DOM harness",
    "js-needs-live-render": (
        "A criterion whose observable is rendered by JavaScript therefore needs the PM's live "
        "render as evidence"
    ),
    "js-survivor-expected": "A surviving JS fault is expected where only runtime behaviour changes",
    "js-survivor-non-blocking": "Record it as a non-blocking test-strength row",
    "js-pinned-token-fault-blocks": "A fault that deletes a token the criterion pins",
    "archive-has-no-git": "A qa copy exported with `git archive` has no `.git`",
    "skipping-tests-named": "The git-diff scope guards",
    "skip-makes-inconclusive": "A skipped test makes a fault inconclusive",
    "pins-prove-nothing": "Engine pins and boundary scans prove nothing about a scenario",
    "pins-fail-on-any-edit": (
        "The content pin on the two engine files and the AST boundary scans fail on any edit"
    ),
    "engine-faults-only-for-engine-plans": (
        "Plant faults in the engine files only when the plan's purpose is an engine change"
    ),
    "single-line-search": "Write each fault's search as one line occurring once",
    "export-line-endings-follow-autocrlf": (
        "Line endings in a `git archive` export follow `core.autocrlf`"
    ),
    "one-line-search-matches-both-endings": "while a one-line search matches under both",
    "lf-pins-normalize-line-endings": (
        "The LF-normalized content pins hash after converting CRLF to LF"
    ),
    "sensitive-needs-hidden-pass": (
        "A plan whose Sensitive marking is not none needs a hidden-mode pass after approval"
    ),
    "hidden-file-location": "Write the hidden scenarios to `.claude/review/hidden/`",
    "sensitive-definitions": "Security means auth, CSRF, the settings allowlist and credentials",
    "hidden-test-basis": "Cover security with the flag re-enabled",
    "sweep-uses-grep": "Sweep mode searches with grep and excludes stale worktree copies",
    "sweep-excludes-worktrees": "Leave out `.claude/worktrees/` and `.claude/audit-worktrees/`",
    "prose-label": "**Prose in rows, artifacts and messages.**",
    "prose-no-dashes": (
        "No em dashes, en dashes or spaced hyphens in a verdict row, an artifact or any message"
    ),
    "prose-invented-names-only": "invented names only in examples",
    "prose-real-name-copied": "a real name in an example is copied into code and commits",
}

# Repository paths the bindings name in backticks. Each must exist, so a rename or deletion
# turns a binding false loudly instead of silently.
NAMED_PATH_RE = re.compile(r"`(tests/[\w./-]+)`")


def read_agent() -> str:
    return contract.read_lf(AGENT_PATH)


# --------------------------------------------------------------------------
# Pure checks. Each returns a list of violation strings; empty means pass.
# --------------------------------------------------------------------------


def check_frontmatter_parses(text: str) -> list[str]:
    try:
        fields, _ = contract.split_frontmatter(text)
    except ValueError:
        return ["frontmatter fences missing or unclosed"]
    problems = [
        f"missing key: {key}" for key in ("name", "description", "tools") if key not in fields
    ]
    if fields.get("name") != AGENT_PATH.stem:
        problems.append(f"name {fields.get('name')!r} does not match file stem")
    description = str(fields.get("description", ""))
    if "PlanetStopper" not in description or "acceptance verifier" not in description:
        problems.append("description must name the project and the acceptance-verifier role")
    for mode in ("`Mode: verify`", "`hidden`", "`sweep`"):
        if mode not in description:
            problems.append(f"description lacks the dispatch hint {mode}")
    return problems


def check_tools(text: str) -> list[str]:
    try:
        fields, _ = contract.split_frontmatter(text)
    except ValueError:
        return ["no frontmatter"]
    tools = [item.strip() for item in str(fields.get("tools", "")).split(",") if item.strip()]
    if tools != EXPECTED_TOOLS:
        return [f"tools {tools} != {EXPECTED_TOOLS}"]
    return []


def check_skills_follow_template(text: str) -> list[str]:
    try:
        fields, _ = contract.split_frontmatter(text)
    except ValueError:
        return ["no frontmatter"]
    if fields.get("skills") != [EXPECTED_SKILL]:
        return [f"skills {fields.get('skills')!r} != {[EXPECTED_SKILL]!r}"]
    return []


def check_model_and_effort_follow_template(text: str) -> list[str]:
    try:
        fields, _ = contract.split_frontmatter(text)
    except ValueError:
        return ["no frontmatter"]
    problems = []
    if fields.get("model") != EXPECTED_MODEL:
        problems.append(f"model {fields.get('model')!r} != {EXPECTED_MODEL!r}")
    if fields.get("effort") != EXPECTED_EFFORT:
        problems.append(f"effort {fields.get('effort')!r} != {EXPECTED_EFFORT!r}")
    return problems + contract.check_pins_record(text)


def check_no_memory_or_mcp(text: str) -> list[str]:
    try:
        fields, _ = contract.split_frontmatter(text)
    except ValueError:
        return ["no frontmatter"]
    return [f"unexpected key: {key}" for key in ("memory", "mcpServers") if key in fields]


def check_bindings_supplied(text: str) -> list[str]:
    return contract.check_rule_anchors(text, BINDING_ANCHORS)


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


def check_gate_recognizes_reviewer(text: str) -> list[str]:
    """The gate recognizes a reviewer role by the compiled-from marker whose base is a reviewer
    template, or by an own review-artifact paragraph. The compiled file must keep both."""
    problems = []
    if not re.search(r"^<!-- compiled-from: [^\n]*[/\\]qa\.md sha256:[0-9a-f]+ -->", text, re.M):
        problems.append("compiled-from marker does not name the qa template as its base")
    body = contract.compiled_template_body(text)
    if body is None:
        return problems + ["bindings heading missing, so the template body cannot be located"]
    if REVIEW_ARTIFACT_MARKER not in body:
        problems.append("template body lacks the review-artifact paragraph")
    if "qa.json" not in body:
        problems.append("template body does not name the qa.json artifact")
    return problems


def check_named_paths_exist(text: str, root: Path) -> list[str]:
    section = contract.bindings_section(text)
    if not section:
        return ["bindings section missing"]
    paths = sorted(set(NAMED_PATH_RE.findall(section)))
    if not paths:
        return ["bindings name no repository test paths, so the existence check is vacuous"]
    return [f"bindings name {p}, which does not exist" for p in paths if not (root / p).exists()]


def check_review_folder_not_ignored(gitignore_text: str) -> list[str]:
    """The bindings say .claude/review/ is not gitignored. A rule that ignores it, or any parent
    that contains it, makes that sentence false (and would hide verdict files from review)."""
    problems = []
    for raw in gitignore_text.splitlines():
        rule = raw.strip().lstrip("/").rstrip("/")
        if not rule or rule.startswith(("#", "!")):
            continue
        if rule in (".claude", ".claude/*", ".claude/**") or rule.startswith(".claude/review"):
            problems.append(f".gitignore rule {raw.strip()!r} ignores the review folder")
    return problems


def check_engine_pin_names_both_files(pin_text: str | None) -> list[str]:
    if pin_text is None:
        return ["engine content pin test missing"]
    return [
        f"engine pin does not name {name}"
        for name in ("alpha_bot_execution.py", "math_engine.py")
        if name not in pin_text
    ]


def load_records_module(path: Path):
    spec = importlib.util.spec_from_file_location("qa_agent_records_check", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_verdict_contract(records) -> list[str]:
    """The verdict-file binding states what the gate-side validator demands: three verdicts, a
    `path:line` location without spaces, and a DC id or `new-` kebab class."""
    problems = []
    if tuple(records.REVIEW_VERDICTS) != ("PASS", "FAIL", "BLOCKED"):
        problems.append(f"verdicts {tuple(records.REVIEW_VERDICTS)!r} differ from the binding")
    if not records.LOCATION_RE.match("app.py:12"):
        problems.append("a plain path:line location is refused")
    if records.LOCATION_RE.match("app.py: 12") or records.LOCATION_RE.match("my file.py:12"):
        problems.append("a location with spaces is accepted")
    if not records.CLASS_RE.match("DC-3") or not records.CLASS_RE.match("new-some-class"):
        problems.append("a DC id or new- kebab class is refused")
    if records.CLASS_RE.match("some class"):
        problems.append("a free-text class is accepted")
    return problems


# --------------------------------------------------------------------------
# Real-file checks and one mutation control per check.
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_text() -> str:
    return read_agent()


def test_frontmatter_parses(agent_text):
    assert check_frontmatter_parses(agent_text) == []


def test_frontmatter_parses_mutation(agent_text):
    broken = agent_text.replace("\n---\n", "\n", 1)
    assert check_frontmatter_parses(broken) != []
    assert check_frontmatter_parses(agent_text.replace("name: qa", "name: other", 1)) != []
    assert check_frontmatter_parses(agent_text.replace("PlanetStopper", "Other", 1)) != []
    assert check_frontmatter_parses(agent_text.replace("`hidden`", "`other`", 1)) != []


def test_tools_match_design(agent_text):
    assert check_tools(agent_text) == []


def test_tools_mutation(agent_text):
    added = agent_text.replace("TaskList\n", "TaskList, Edit\n", 1)
    removed = agent_text.replace("Bash, ", "", 1)
    assert check_tools(added) != []
    assert check_tools(removed) != []


def test_skills_follow_template(agent_text):
    assert check_skills_follow_template(agent_text) == []


def test_skills_mutation(agent_text):
    assert check_skills_follow_template(agent_text.replace("  - test-design-contract\n", "", 1))
    assert check_skills_follow_template(agent_text.replace(EXPECTED_SKILL, "other-skill", 1))


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


def test_pins_record_present(agent_text):
    assert contract.check_pins_record(agent_text) == []


def test_pins_record_mutation(agent_text):
    removed = re.sub(r"^<!-- template-owned pins:.*$", "", agent_text, flags=re.M)
    assert removed != agent_text
    assert contract.check_pins_record(removed) != []
    assert contract.check_pins_record(re.sub(r"model@[0-9a-f]+,?[ \t]*", "", agent_text)) != []
    assert contract.check_pins_record(re.sub(r",?[ \t]*effort@[0-9a-f]+", "", agent_text)) != []


def test_no_memory_or_mcp(agent_text):
    assert check_no_memory_or_mcp(agent_text) == []


def test_no_memory_or_mcp_mutation(agent_text):
    assert check_no_memory_or_mcp(agent_text.replace("tools:", "memory: project\ntools:", 1)) != []
    assert check_no_memory_or_mcp(agent_text.replace("tools:", "mcpServers:\ntools:", 1)) != []


def test_flat_layout():
    assert contract.check_flat_layout(AGENT_PATH, NESTED_LAYOUT) == []


def test_flat_layout_mutation(tmp_path):
    nested = tmp_path / "qa" / "AGENT.md"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(AGENT_PATH, nested) != []
    assert contract.check_flat_layout(tmp_path / "absent.md", tmp_path / "none.md") != []


def test_template_body_present(agent_text):
    assert contract.check_template_body_present(agent_text, TEMPLATE_TITLE, DIRECTIVE_MARKER) == []


def test_template_body_present_mutation(agent_text):
    broken = contract.without_template_body(agent_text)
    assert broken != agent_text
    assert contract.check_template_body_present(broken, TEMPLATE_TITLE, DIRECTIVE_MARKER) != []
    for marker in (TEMPLATE_TITLE, DIRECTIVE_MARKER):
        removed = agent_text.replace(marker, "REMOVED", 1)
        assert contract.check_template_body_present(removed, TEMPLATE_TITLE, DIRECTIVE_MARKER)


def test_template_body_matches_global_template(agent_text):
    if not TEMPLATE_PATH.is_file():
        pytest.skip("global template ~/.claude/agents/qa.md is not installed here")
    assert contract.check_template_body_matches(agent_text, contract.read_lf(TEMPLATE_PATH)) == []


def test_template_body_comparison_mutation(agent_text):
    template = "---\nname: qa\n---\n\n" + str(contract.compiled_template_body(agent_text))
    assert contract.check_template_body_matches(agent_text, template) == []
    assert contract.check_template_body_matches(
        contract.without_template_body(agent_text), template
    )
    edited = agent_text.replace(DIRECTIVE_MARKER, DIRECTIVE_MARKER + " edited", 1)
    assert contract.check_template_body_matches(edited, template)
    crlf = agent_text.replace("\n", "\r\n")
    assert contract.check_template_body_matches(crlf, template.replace("\n", "\r\n")) == []


def test_template_body_removal_fails_against_global_template(agent_text):
    if not TEMPLATE_PATH.is_file():
        pytest.skip("global template ~/.claude/agents/qa.md is not installed here")
    broken = contract.without_template_body(agent_text)
    assert contract.check_template_body_matches(broken, contract.read_lf(TEMPLATE_PATH)) != []


def test_gate_recognizes_reviewer(agent_text):
    assert check_gate_recognizes_reviewer(agent_text) == []


def test_gate_recognizes_reviewer_mutation(agent_text):
    no_marker = re.sub(r"^<!-- compiled-from: .*$", "", agent_text, flags=re.M)
    assert check_gate_recognizes_reviewer(no_marker) != []
    wrong_base = agent_text.replace("/agents/qa.md sha256", "/agents/other.md sha256", 1)
    assert check_gate_recognizes_reviewer(wrong_base) != []
    no_paragraph = agent_text.replace(REVIEW_ARTIFACT_MARKER, "**Artifact.**", 1)
    assert check_gate_recognizes_reviewer(no_paragraph) != []
    assert check_gate_recognizes_reviewer(contract.without_template_body(agent_text)) != []


def test_every_binding_supplied(agent_text):
    assert check_bindings_supplied(agent_text) == []


def test_binding_anchors_are_unique_and_disjoint(agent_text):
    assert contract.check_anchor_table(agent_text, BINDING_ANCHORS) == []


def test_binding_anchor_table_mutation(agent_text):
    duplicated = agent_text + "\nGround truth is the code at the brief's SHA\n"
    assert contract.check_anchor_table(duplicated, BINDING_ANCHORS) != []
    overlapping = {"a": "Stage files by name", "b": "Stage files by name and never"}
    assert contract.check_anchor_table(agent_text, overlapping) != []


@pytest.mark.parametrize("name", list(BINDING_ANCHORS))
def test_binding_removal_is_detected(agent_text, name):
    mutated = contract.without_bindings_phrase(agent_text, BINDING_ANCHORS[name])
    assert mutated != agent_text
    newly_missing = set(check_bindings_supplied(mutated)) - set(check_bindings_supplied(agent_text))
    assert name in check_bindings_supplied(mutated)
    assert newly_missing <= {name}


@pytest.mark.parametrize(
    "sentence",
    contract.binding_sentences(contract.read_lf(AGENT_PATH)),
    ids=lambda s: re.sub(r"\W+", "-", s)[:48],
)
def test_deleting_any_binding_sentence_is_detected(agent_text, sentence):
    """Every sentence of the bindings carries at least one anchor, so a deleted rule fails."""
    mutated = contract.without_bindings_sentence(agent_text, sentence)
    assert mutated != agent_text
    assert check_bindings_supplied(mutated) != []


def test_bindings_anchor_in_template_body_does_not_count(agent_text):
    # An anchor that appears only above the bindings heading is not a binding.
    section_start = agent_text.find(BINDINGS_HEADING)
    stripped = agent_text[:section_start] + BINDINGS_HEADING + "\n"
    assert set(check_bindings_supplied(stripped)) == set(BINDING_ANCHORS)


def test_no_placeholder(agent_text):
    assert contract.check_no_placeholder(agent_text) == []


def test_no_placeholder_mutation(agent_text):
    assert contract.check_no_placeholder(
        agent_text + "\n- Design system: <project design system>\n"
    )
    assert contract.check_no_placeholder(agent_text.replace("tools:", "extra: <value>\ntools:", 1))


def test_no_history_text(agent_text):
    assert contract.check_no_history_or_machine_text(agent_text) == []


def test_every_history_pattern_has_a_mutation_sample():
    assert set(contract.HISTORY_SAMPLES) == set(contract.HISTORY_PATTERNS)
    assert set(contract.PROSE_SAMPLES) == set(contract.PROSE_PATTERNS)


@pytest.mark.parametrize(
    ("name", "injected"),
    [(name, s) for name, samples in contract.HISTORY_SAMPLES.items() for s in samples],
)
def test_history_text_mutation(agent_text, name, injected):
    problems = contract.check_no_history_or_machine_text(agent_text + "\n- " + injected + "\n")
    assert any(p.startswith(f"{name}:") for p in problems), problems


def test_history_text_in_frontmatter_mutation(agent_text):
    broken = agent_text.replace("effort: high", "effort: high\nnote: decided 2026-01-02", 1)
    assert contract.check_no_history_or_machine_text(broken) != []


def test_prose_rules_hold_in_bindings(agent_text):
    assert contract.check_prose_rules(agent_text) == []


@pytest.mark.parametrize(("name", "injected"), list(contract.PROSE_SAMPLES.items()))
def test_prose_rule_mutation(agent_text, name, injected):
    problems = contract.check_prose_rules(agent_text + "\n- " + injected + "\n")
    assert any(p.startswith(f"{name}:") for p in problems), problems


def test_prose_rule_ignores_text_above_the_bindings(agent_text):
    # The template body is template-owned; only the bindings section is scanned for dashes.
    head = agent_text[: agent_text.find(BINDINGS_HEADING)]
    em_dash_above = head + chr(0x2014) + " above\n" + agent_text[len(head) :]
    assert contract.check_prose_rules(em_dash_above) == contract.check_prose_rules(agent_text)


def test_bindings_section_missing_is_reported(agent_text):
    broken = agent_text.replace(BINDINGS_HEADING, "## Something else")
    assert contract.check_prose_rules(broken) != []
    assert contract.check_no_history_or_machine_text(broken) != []
    assert contract.check_no_placeholder(broken) != []
    assert check_bindings_supplied(broken) != []
    assert check_named_paths_exist(broken, REPO_ROOT) != []


def test_compiled_shape(agent_text):
    assert check_compiled_shape(agent_text) == []


def test_compiled_shape_mutation(agent_text):
    no_provenance = re.sub(r"^<!-- compiled-from: .*$", "", agent_text, flags=re.M)
    assert check_compiled_shape(no_provenance) != []
    assert check_compiled_shape(agent_text + "\n## Project bindings (Other)\n") != []
    assert check_compiled_shape(agent_text + "\n# Extends the template\n") != []


# --------------------------------------------------------------------------
# Facts the bindings state about this repository.
# --------------------------------------------------------------------------


def test_named_test_paths_exist(agent_text):
    assert check_named_paths_exist(agent_text, REPO_ROOT) == []


def test_named_test_paths_mutation(agent_text, tmp_path):
    assert check_named_paths_exist(agent_text, tmp_path) != []
    renamed = agent_text.replace("tests/test_scope_guard.py", "tests/test_gone_guard.py", 1)
    assert check_named_paths_exist(renamed, REPO_ROOT) != []
    no_paths = contract.without_bindings_phrase(agent_text, "`tests/test_scope_guard.py`")
    stripped = re.sub(r"`tests/[\w./-]+`", "REMOVED", no_paths)
    assert check_named_paths_exist(stripped, REPO_ROOT) != []


def test_review_folder_is_not_gitignored():
    assert GITIGNORE_PATH.is_file(), ".gitignore missing"
    assert check_review_folder_not_ignored(contract.read_lf(GITIGNORE_PATH)) == []


@pytest.mark.parametrize(
    "rule",
    [".claude/review/", "/.claude/review", ".claude/", ".claude/**", ".claude/review/hidden"],
)
def test_review_folder_ignore_mutation(rule):
    original = contract.read_lf(GITIGNORE_PATH)
    assert check_review_folder_not_ignored(original + "\n" + rule + "\n") != []


def test_review_folder_check_ignores_comments_and_unrelated_rules():
    text = "# .claude/review/ is not ignored\n.claude/worktrees/\n!.claude/review/\n"
    assert check_review_folder_not_ignored(text) == []


def test_engine_content_pin_names_both_engine_files():
    pin = contract.read_lf_if_present(
        REPO_ROOT / "tests" / "test_mdd_window_truth_zero_diff_guard.py"
    )
    assert check_engine_pin_names_both_files(pin) == []


def test_engine_content_pin_mutation():
    assert check_engine_pin_names_both_files(None) != []
    assert check_engine_pin_names_both_files("only math_engine.py here") != []
    assert check_engine_pin_names_both_files("only alpha_bot_execution.py here") != []


def test_verdict_contract_matches_gate_validator():
    if not RECORDS_PATH.is_file():
        pytest.skip("harness records validator not installed on this machine")
    assert check_verdict_contract(load_records_module(RECORDS_PATH)) == []


class _FakeRecords:
    REVIEW_VERDICTS = ("PASS", "FAIL", "BLOCKED")
    LOCATION_RE = re.compile(r"^\S+:\d+(-\d+)?$")
    CLASS_RE = re.compile(r"^(DC-\d+|new-[a-z0-9]+(-[a-z0-9]+)*)$")


def test_verdict_contract_mutation():
    assert check_verdict_contract(_FakeRecords) == []

    class WrongVerdicts(_FakeRecords):
        REVIEW_VERDICTS = ("PASS", "FAIL")

    class LooseLocation(_FakeRecords):
        LOCATION_RE = re.compile(r"^.+:\s*\d+$")

    class LooseClass(_FakeRecords):
        CLASS_RE = re.compile(r".*")

    class StrictClass(_FakeRecords):
        CLASS_RE = re.compile(r"^DC-\d+$")

    for broken in (WrongVerdicts, LooseLocation, LooseClass, StrictClass):
        assert check_verdict_contract(broken) != [], broken.__name__


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
    tampered = tmp_path / "qa.md"
    tampered.write_text(
        re.sub(r"^<!-- compiled-from: .*$", "", agent_text, flags=re.M), encoding="utf-8"
    )
    result = _run_compiler_check(tampered)
    assert result.returncode != 0, "check must fail on a file without provenance"


# --------------------------------------------------------------------------
# Required common rules. The needles live in tests/harness/_agent_contract.py and not in this
# file, so a rule this agent file never carries fails here even when no anchor above names it.
# --------------------------------------------------------------------------

REQUIRED_RULES_EXEMPT: frozenset[str] = frozenset()


def test_required_common_rules_exemption_is_only_the_engine_rule():
    assert REQUIRED_RULES_EXEMPT <= contract.ENGINE_RULE_NAMES


def test_required_common_rules_are_carried():
    assert contract.check_required_common_rules(read_agent(), REQUIRED_RULES_EXEMPT) == []


@pytest.mark.parametrize(
    "rule", [r for r in contract.COMMON_RULE_NEEDLES if r not in REQUIRED_RULES_EXEMPT]
)
def test_required_common_rule_removal_is_detected(rule):
    text = read_agent()
    mutated = contract.without_bindings_needle(text, rule)
    assert mutated != text
    assert contract.check_required_common_rules(mutated, REQUIRED_RULES_EXEMPT) == [rule]


def test_required_common_rules_do_not_count_the_template_body():
    text = read_agent()
    head = text[: len(text) - len(contract.bindings_section(text))]
    stripped = head + "## Project bindings (PlanetStopper)\n"
    assert contract.check_required_common_rules(stripped, REQUIRED_RULES_EXEMPT) == [
        r for r in contract.COMMON_RULE_NEEDLES if r not in REQUIRED_RULES_EXEMPT
    ]


def test_required_common_rules_report_a_missing_bindings_section():
    broken = read_agent().replace("## Project bindings (PlanetStopper)", "## Something else")
    assert contract.check_required_common_rules(broken, REQUIRED_RULES_EXEMPT) != []
