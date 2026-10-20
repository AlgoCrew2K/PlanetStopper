"""Contract tests for the project doc-gen agent definition.

Each rule the file must keep is one pure checker returning a list of problems, plus one
mutation control that feeds the checker a deliberately broken variant and asserts it
reports a problem (a check that cannot fail proves nothing). One real child-process case
runs the compile freshness check; it is skipped where the harness scripts are not installed.

The agent file is a compiled specialization: the global template body plus a
``## Project bindings (PlanetStopper)`` section this repository owns. The compiler's own
``--check`` verifies provenance, not body presence, so the template-body checks here are the
only guard against a body that was cut out.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.harness import _agent_contract as contract

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "doc-gen.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "doc-gen" / "AGENT.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "doc-gen.md"

# The parent's tool list with one addition: Edit, so the large existing docs are changed in place.
EXPECTED_TOOLS = [
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "SendMessage",
    "TaskUpdate",
    "TaskList",
    "mcp__codebase-memory-mcp__search_graph",
    "mcp__codebase-memory-mcp__trace_path",
    "mcp__codebase-memory-mcp__get_code_snippet",
    "mcp__codebase-memory-mcp__query_graph",
    "mcp__codebase-memory-mcp__get_architecture",
    "mcp__codebase-memory-mcp__search_code",
    "mcp__codebase-memory-mcp__check_index_coverage",
    "mcp__codebase-memory-mcp__index_status",
    "mcp__codebase-memory-mcp__list_projects",
]
EXPECTED_MODEL = "sonnet"
EXPECTED_EFFORT = "medium"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# Doc Gen"
DIRECTIVE_MARKER = "**Prime Directive:**"

# One entry per binding the project must supply: (label, substrings that must all appear in
# that binding's own bullet).
REQUIRED_BINDINGS = [
    ("Trigger and scope", ["once per slice", "every reviewer", "not-ready", ".claude/CLAUDE.md"]),
    (
        "Ground truth",
        ["reviewed HEAD", "path:line", "docs/generated", "DECISIONS.md", "codebase-memory"],
    ),
    (
        "Large files and in-place edits",
        ["INDEX.md", "app.md", "never read whole", "Edit", "old_string", "decision id"],
    ),
    (
        "Current state, never a changelog",
        ["changelog", "Last updated", "one date", "DECISIONS.md"],
    ),
    ("Gate semantics", ["production source", "docs/generated", "scripts count", "duty"]),
    (
        "Tail record",
        [
            ".claude/review/",
            "twelve-character",
            "doc-gen.json",
            "`reviewed`",
            "`READY`",
            "first-parent",
            "Write it last",
            "orphans",
        ],
    ),
    ("Commit scope", ["pathspec", "git add -A", "not gitignored", "hidden qa", "docs-done"]),
    (
        "File naming and shape",
        ["underscores", "_js", "static_index_js.md", "Internal Dependencies"],
    ),
    (
        "Coverage is partial",
        ["no doc file", "acceptance_gate.py", "mc-band-edge-stability", "hand-narrated"],
    ),
    ("Hand-narrated docs", ["manual-section marker", "surgical", "superseded banner"]),
    ("Index", ["Module Index", "not alphabetical", "Pruned", "Architecture Notes"]),
    ("Decision log", ["append-only", "never mint", "ISO date", "test run count"]),
    ("README and Key Files", ["README.md", "Key Files row", "route inventory", "docs-done"]),
    (
        "Reconcile by change class",
        ["app.md", "database.md", "migration counts", "advisors_", "Scheduler", "DEPLOYMENT.md"],
    ),
    (
        "Trade boundary and engine claims",
        [
            "AI Advisor",
            "proposal-approve",
            "undeployed",
            "panic-sell",
            "Managed Sleeves",
            "LIVE_EXECUTION",
            "live_mode",
            "is_live",
            "alpha_bot_execution.py",
            "math_engine.py",
            "git diff",
        ],
    ),
    (
        "Data honesty in prose",
        [
            "basis, unit, window, subject and source",
            "`None`",
            "counterfactual",
            "go-live floor",
            "percent-scale",
            "Composer hash",
            "normalized display name",
            "read-only query",
            "live read",
        ],
    ),
    ("Tests you run", ["-n0", "-o addopts=", "-n auto", "4 workers"]),
    ("Prose", ["em dashes", "en dashes", "spaced hyphens", "invented names"]),
]

# One anchor per sentence of the bindings section (a bullet label counts as a sentence). Each
# anchor occurs exactly once in the bindings, none sits inside another, and removing a sentence
# must make the rule check report exactly that rule.
BINDING_RULES: dict[str, str] = {
    "trigger-label": "**Trigger and scope.**",
    "once-per-slice-at-tail": "Run once per slice, at the tail, after every reviewer has passed",
    "per-cycle-docs-fold-in": "fold every per-cycle documentation need into that single run",
    "template-trigger-governs": "The template trigger governs over the wording in `.claude/CLAUDE.md`",
    "dispatch-without-pass-not-ready": "A dispatch made while any head lacks a reviewer PASS",
    "ground-truth-label": "**Ground truth.**",
    "facts-from-code-at-head": "Every fact comes from the code at the reviewed HEAD",
    "refind-symbols-never-copy-locator": "Re-find every symbol with Grep at that HEAD",
    "cite-symbols-not-lines": "Cite symbols and file names in new text",
    "verify-docstring-claims": "Check docstring-derived statements about schedulers",
    "codebase-memory-leads": "Treat codebase-memory results as leads",
    "large-files-label": "**Large files and in-place edits.**",
    "large-files-never-read-whole": "are never read whole, because their header lines are changelog paragraphs",
    "grep-then-ranged-read": "Find the region with Grep and line numbers",
    "change-in-place-with-edit": "Change these files in place with Edit",
    "edit-is-the-added-tool": "Edit is the one tool this role adds to the parent's list",
    "current-state-label": "**Current state, never a changelog.**",
    "write-current-state": "Write each doc as the current state of the module",
    "no-changelog-in-headers": "Never append a changelog sentence to the header line",
    "history-goes-to-decisions": "A change's history, the reason it was made and what it replaced",
    "gate-semantics-label": "**Gate semantics.**",
    "create-gate-production-source": "The create gate counts every changed path as production source",
    "passing-gate-proves-nothing": "A passing gate therefore proves nothing about truth",
    "tail-record-label": "**Tail record.**",
    "tail-record-fields": "After the docs commit, write `.claude/review/` plus the new head's",
    "tail-record-write-last": "Write it last, because the gate requires the file",
    "tail-record-every-repo": "Write it for every repo of the slice",
    "commit-scope-label": "**Commit scope.**",
    "stage-by-pathspec": "Stage by explicit pathspec after `--`",
    "commit-docs-paths-only": "Commit only paths under `docs/` or ending in `.md`",
    "naming-label": "**File naming and shape.**",
    "module-doc-naming": "A module doc is named from the source path",
    "header-and-sections": "Keep the header lines (title, blurb, Source, Last updated)",
    "coverage-label": "**Coverage is partial.**",
    "document-changed-symbols-new-doc": "Document the symbols the diff changed",
    "never-claim-complete": "Never claim the whole tree is complete",
    "probe-outputs-not-hand-edited": "Never hand-edit the probe outputs",
    "hand-narrated-label": "**Hand-narrated docs.**",
    "no-manual-marker": "No module doc carries a manual-section marker",
    "surgical-edits": "Make surgical edits to the affected sections",
    "superseded-banner": "Dated historical snapshots keep their body",
    "index-label": "**Index.**",
    "index-table-row-edit": "The `Module Index` table in `docs/generated/INDEX.md`",
    "index-other-sections-alone": "Leave the Pruned, Deleted Templates and Architecture Notes sections alone",
    "decision-log-label": "**Decision log.**",
    "decision-append-only": "Append a new `##` section at the end of `DECISIONS.md`",
    "decision-id-never-minted": "Use the id the brief or the plan supplies",
    "decision-entry-anatomy": "Follow the entry anatomy",
    "decision-test-count-only-if-supplied": "Put a test run count in the entry only when",
    "decision-text-not-a-substitute": "Never copy log text into a `docs/generated` file",
    "readme-key-files-label": "**README and Key Files.**",
    "readme-only-when-needed": "Touch `README.md` only when setup, usage, architecture or safety",
    "key-files-row-in-place": "Edit the `.claude/CLAUDE.md` Key Files row of a changed file in place",
    "name-key-files-edits-in-docs-done": "Name each Key Files row edit in docs-done",
    "reconcile-label": "**Reconcile by change class.**",
    "reconcile-route": "A new route reaches the route section",
    "reconcile-table-accessor": "A new table or accessor reaches `docs/generated/database.md`",
    "reconcile-advisor-module": "A new advisor module gets its own",
    "reconcile-scheduler-slot": "A new scheduler slot reaches the Scheduler section",
    "trade-boundary-label": "**Trade boundary and engine claims.**",
    "no-trade-boundary-per-surface": "Describe the no-trade boundary per surface and never repo-wide",
    "ai-advisor-advisory-only": "AI Advisor surfaces are advisory only",
    "only-route-to-symphony-creation": (
        "The only route that reaches Composer symphony creation is the proposal-approve route"
    ),
    "panic-sell-only-trade-button": "The panic-sell route is the only dashboard trade button",
    "managed-sleeves-order-path": "The Managed Sleeves module is the one approved order path",
    "live-trading-two-flags": (
        "Effective live trading is the global `LIVE_EXECUTION` flag AND the symphony's `live_mode`"
    ),
    "engine-unchanged-needs-git-diff": (
        "State that `alpha_bot_execution.py` and `math_engine.py` are unchanged only after"
    ),
    "data-honesty-label": "**Data honesty in prose.**",
    "number-states-five-attributes": "Every number a doc states carries its basis, unit, window",
    "none-distinct-from-zero": "`None` is documented as distinct from `0`",
    "counterfactual-labelled": "A counterfactual result (shadow history, if-held, backtest)",
    "shadow-history-go-live-floor": "Shadow history begins at the production go-live floor",
    "scale-and-key-space": "Returns are percent-scale while Composer drawdown is a fraction",
    "production-data-unconfirmed": "Row counts, history depth and live Composer behaviour",
    "tests-label": "**Tests you run.**",
    "tests-n0-and-worker-cap": "When you run tests to check a documented claim, pass `-n0`",
    "test-count-only-from-a-run": "Record a test count only from a run you made",
    "prose-label": "**Prose.**",
    "prose-no-dashes": "Use no em dashes, en dashes or spaced hyphens in anything you write",
    "examples-use-invented-names": "Examples use invented names only",
    "zero-diff-cites-content-pin-result": (
        "cite the result of the content pin `tests/test_mdd_window_truth_zero_diff_guard.py` "
        "at that HEAD"
    ),
}


def read_agent() -> str:
    return contract.read_lf(AGENT_FILE)


def split_frontmatter(text: str) -> dict[str, str | list[str]]:
    return contract.split_frontmatter(text)[0]


# ---- checkers: each returns a list of problems, empty when the rule holds ----


def check_frontmatter(text: str) -> list[str]:
    try:
        keys = split_frontmatter(text)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    if keys.get("name") != "doc-gen":
        problems.append(f"name is {keys.get('name')!r}, expected 'doc-gen'")
    desc = keys.get("description", "")
    if not desc:
        problems.append("description missing")
    elif "docs/generated" not in desc or "tail" not in desc:
        problems.append("description does not name the docs folder and the slice tail")
    return problems


def check_tools(text: str) -> list[str]:
    keys = split_frontmatter(text)
    tools = [t.strip() for t in str(keys.get("tools", "")).split(",") if t.strip()]
    if tools != EXPECTED_TOOLS:
        return [f"tools {tools} differ from the parent's list plus Edit {EXPECTED_TOOLS}"]
    return []


def check_model(text: str) -> list[str]:
    keys = split_frontmatter(text)
    problems = []
    if keys.get("model") != EXPECTED_MODEL:
        problems.append(f"model is {keys.get('model')!r}, expected {EXPECTED_MODEL!r}")
    if keys.get("effort") != EXPECTED_EFFORT:
        problems.append(f"effort is {keys.get('effort')!r}, expected {EXPECTED_EFFORT!r}")
    return problems


def check_no_unrequested_keys(text: str) -> list[str]:
    keys = split_frontmatter(text)
    return [f"unrequested key {k!r}" for k in ("memory", "mcpServers", "skills") if k in keys]


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
    section = contract.bindings_section(text)
    problems = []
    for label, needles in REQUIRED_BINDINGS:
        bullet = contract.bullet_of(section, label)
        if not bullet:
            problems.append(f"binding {label!r} missing")
            continue
        problems += [f"binding {label!r} lacks {n!r}" for n in needles if n not in bullet]
    return problems


def check_binding_rules(text: str) -> list[str]:
    return contract.check_rule_anchors(text, BINDING_RULES)


# ---- rule checks on the real file ----


def test_frontmatter_parses():
    assert check_frontmatter(read_agent()) == []


def test_tools_are_the_parents_plus_edit():
    assert check_tools(read_agent()) == []


def test_model_and_effort_follow_template():
    assert check_model(read_agent()) == []


def test_pins_record_present():
    assert contract.check_pins_record(read_agent()) == []


def test_no_memory_or_mcp_or_skills_keys():
    assert check_no_unrequested_keys(read_agent()) == []


def test_flat_layout():
    assert contract.check_flat_layout(AGENT_FILE, NESTED_LAYOUT) == []


def test_compiled_shape():
    assert check_compiled_shape(read_agent()) == []


def test_template_body_present():
    assert (
        contract.check_template_body_present(read_agent(), TEMPLATE_TITLE, DIRECTIVE_MARKER) == []
    )


def test_template_body_matches_global_template():
    if not TEMPLATE_FILE.is_file():
        pytest.skip("global template ~/.claude/agents/doc-gen.md is not installed on this runner")
    assert contract.check_template_body_matches(read_agent(), contract.read_lf(TEMPLATE_FILE)) == []


def test_every_binding_supplied():
    assert check_bindings_supplied(read_agent()) == []


def test_every_binding_rule_supplied():
    assert check_binding_rules(read_agent()) == []


def test_binding_rule_anchors_are_unique_and_disjoint():
    assert contract.check_anchor_table(read_agent(), BINDING_RULES) == []


def test_no_placeholder_in_bindings():
    assert contract.check_no_placeholder(read_agent()) == []


def test_no_history_or_machine_text_in_bindings():
    assert contract.check_no_history_or_machine_text(read_agent()) == []


def test_prose_rules_hold_in_bindings():
    assert contract.check_prose_rules(read_agent()) == []


# ---- mutation controls: each check must fail on a deliberately broken variant ----


def test_mutation_frontmatter_missing_fence():
    broken = read_agent().replace("---", "", 1)
    assert check_frontmatter(broken) != []


def test_mutation_frontmatter_wrong_name():
    broken = read_agent().replace("name: doc-gen", "name: other-gen", 1)
    assert check_frontmatter(broken) != []


def test_mutation_frontmatter_description_loses_docs_folder():
    broken = read_agent().replace("docs/generated", "docs", 1)
    assert check_frontmatter(broken) != []


def test_mutation_tools_extra_tool():
    broken = read_agent().replace("SendMessage, TaskUpdate", "SendMessage, Skill, TaskUpdate", 1)
    assert broken != read_agent()
    assert check_tools(broken) != []


def test_mutation_tools_edit_dropped():
    broken = read_agent().replace("Read, Write, Edit, Glob", "Read, Write, Glob", 1)
    assert broken != read_agent()
    assert check_tools(broken) != []


def test_mutation_tools_mcp_tool_dropped():
    broken = read_agent().replace(", mcp__codebase-memory-mcp__list_projects", "", 1)
    assert broken != read_agent()
    assert check_tools(broken) != []


def test_mutation_model_changed():
    broken = read_agent().replace("model: sonnet", "model: opus", 1)
    assert broken != read_agent()
    assert check_model(broken) != []


def test_mutation_effort_changed():
    broken = read_agent().replace("effort: medium", "effort: high", 1)
    assert broken != read_agent()
    assert check_model(broken) != []


def test_mutation_memory_key_added():
    broken = read_agent().replace("model: sonnet", "model: sonnet\nmemory: project", 1)
    assert broken != read_agent()
    assert check_no_unrequested_keys(broken) != []


def test_mutation_pins_record_removed():
    broken = re.sub(r"^<!-- template-owned pins:.*$", "", read_agent(), flags=re.M)
    assert broken != read_agent()
    assert contract.check_pins_record(broken) != []


@pytest.mark.parametrize(
    "pin_pattern",
    [r"model@[0-9a-f]+,?[ \t]*", r",?[ \t]*effort@[0-9a-f]+"],
    ids=["model-pin", "effort-pin"],
)
def test_mutation_pins_record_missing_one_pin(pin_pattern):
    broken = re.sub(pin_pattern, "", read_agent(), count=1)
    assert broken != read_agent()
    assert contract.check_pins_record(broken) != []


def test_mutation_nested_layout_present(tmp_path):
    nested = tmp_path / "doc-gen" / "AGENT.md"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(AGENT_FILE, nested) != []
    assert contract.check_flat_layout(tmp_path / "absent.md", tmp_path / "none.md") != []


def test_mutation_compiled_shape_extends_section():
    broken = read_agent().replace(BINDINGS_HEADING, BINDINGS_HEADING + "\n\n# Extends `x`", 1)
    assert check_compiled_shape(broken) != []


def test_mutation_compiled_shape_no_provenance():
    broken = re.sub(r"^<!-- compiled-from: .*$", "", read_agent(), flags=re.M)
    assert check_compiled_shape(broken) != []


def test_mutation_template_body_removed():
    broken = contract.without_template_body(read_agent())
    assert broken != read_agent()
    assert contract.check_template_body_present(broken, TEMPLATE_TITLE, DIRECTIVE_MARKER) != []


@pytest.mark.parametrize("marker", [TEMPLATE_TITLE, DIRECTIVE_MARKER])
def test_mutation_template_body_marker_removed(marker):
    broken = read_agent().replace(marker, "REMOVED", 1)
    assert contract.check_template_body_present(broken, TEMPLATE_TITLE, DIRECTIVE_MARKER) != []


def synthetic_template(text: str) -> str:
    """A template whose body is the compiled file's body: the unbroken side of a comparison."""
    return "---\nname: doc-gen\n---\n\n" + str(contract.compiled_template_body(text)) + "\n"


def test_mutation_template_body_comparison_detects_removal_and_edit():
    text = read_agent()
    template = synthetic_template(text)
    assert contract.check_template_body_matches(text, template) == []
    assert contract.check_template_body_matches(contract.without_template_body(text), template)
    edited = text.replace(DIRECTIVE_MARKER, DIRECTIVE_MARKER + " edited", 1)
    assert contract.check_template_body_matches(edited, template)
    crlf = text.replace("\n", "\r\n")
    assert contract.check_template_body_matches(crlf, template.replace("\n", "\r\n")) == []


def test_mutation_template_body_removal_fails_against_global_template():
    if not TEMPLATE_FILE.is_file():
        pytest.skip("global template ~/.claude/agents/doc-gen.md is not installed on this runner")
    template = contract.read_lf(TEMPLATE_FILE)
    broken = contract.without_template_body(read_agent())
    assert contract.check_template_body_matches(broken, template) != []


@pytest.mark.parametrize("label", [label for label, _ in REQUIRED_BINDINGS])
def test_mutation_each_binding_removal_detected(label):
    text = read_agent()
    start = text.index(f"- **{label}")
    end = text.find("\n- **", start + 1)
    broken = text[:start] + (text[end + 1 :] if end >= 0 else "")
    assert broken != text
    assert check_bindings_supplied(broken) != []


def test_mutation_binding_missing_key_phrase():
    broken = read_agent().replace("LIVE_EXECUTION", "LIVE_FLAG")
    assert check_bindings_supplied(broken) != []


def test_mutation_binding_phrase_only_in_another_binding():
    text = read_agent()
    section = contract.bullet_of(contract.bindings_section(text), "Commit scope")
    moved = text.replace(section, section.replace("git add -A", "REMOVED"), 1) + "\ngit add -A\n"
    assert check_bindings_supplied(moved) != []


@pytest.mark.parametrize("rule", list(BINDING_RULES))
def test_mutation_rule_removal_detected(rule):
    broken = contract.without_bindings_phrase(read_agent(), BINDING_RULES[rule])
    assert broken != read_agent()
    newly_missing = set(check_binding_rules(broken)) - set(check_binding_rules(read_agent()))
    assert rule in check_binding_rules(broken)
    assert newly_missing <= {rule}


@pytest.mark.parametrize(
    "sentence",
    contract.binding_sentences(AGENT_FILE.read_text(encoding="utf-8")),
    ids=lambda s: re.sub(r"\W+", "-", s)[:48],
)
def test_mutation_deleting_any_binding_sentence_is_detected(sentence):
    """Every sentence of the bindings carries at least one anchor, so a deleted rule fails."""
    broken = contract.without_bindings_sentence(read_agent(), sentence)
    assert broken != read_agent()
    assert check_binding_rules(broken) != []


def test_mutation_binding_anchor_table_flags_duplicate_and_overlap():
    text = read_agent()
    duplicated = text + "\n" + BINDING_RULES["tail-record-write-last"] + "\n"
    assert contract.check_anchor_table(duplicated, BINDING_RULES)
    overlapping = {"a": "Write it last", "b": "Write it last, because"}
    assert contract.check_anchor_table(text, overlapping)


def test_mutation_placeholder_added():
    broken = read_agent() + "\n- Docs go to <module-name>.\n"
    assert contract.check_no_placeholder(broken) != []
    assert contract.check_no_placeholder(read_agent() + "\n- TBD fill in\n") != []


def test_every_history_pattern_has_a_mutation_sample():
    assert set(contract.HISTORY_SAMPLES) == set(contract.HISTORY_PATTERNS)
    assert set(contract.PROSE_SAMPLES) == set(contract.PROSE_PATTERNS)


@pytest.mark.parametrize(
    ("pattern", "injected"),
    [(name, s) for name, samples in contract.HISTORY_SAMPLES.items() for s in samples],
)
def test_mutation_history_or_machine_text_detected(pattern, injected):
    broken = read_agent() + f"\n- {injected}\n"
    problems = contract.check_no_history_or_machine_text(broken)
    assert any(p.startswith(f"{pattern}:") for p in problems), problems


@pytest.mark.parametrize(("pattern", "injected"), list(contract.PROSE_SAMPLES.items()))
def test_mutation_prose_rule_detected(pattern, injected):
    broken = read_agent() + f"\n- {injected}\n"
    problems = contract.check_prose_rules(broken)
    assert any(p.startswith(f"{pattern}:") for p in problems), problems


def test_mutation_history_text_in_frontmatter_detected():
    broken = read_agent().replace("effort: medium", "effort: medium\nnote: decided 2026-01-02", 1)
    assert broken != read_agent()
    assert contract.check_no_history_or_machine_text(broken) != []


def test_mutation_bindings_section_missing():
    broken = read_agent().replace(BINDINGS_HEADING, "## Something else")
    assert contract.check_no_placeholder(broken) != []
    assert contract.check_no_history_or_machine_text(broken) != []
    assert contract.check_prose_rules(broken) != []
    assert check_bindings_supplied(broken) != []
    assert check_binding_rules(broken) != []


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
    stale = tmp_path / "doc-gen.md"
    text = read_agent()
    stale.write_text(
        re.sub(r"sha256:[0-9a-f]{12}", "sha256:000000000000", text, count=1), encoding="utf-8"
    )
    result = run_compile_check(stale)
    assert result.returncode == 1, (result.returncode, result.stderr)


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
