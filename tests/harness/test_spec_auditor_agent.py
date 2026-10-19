"""Contract test for the spec-auditor agent definition.

The agent file is a compiled specialization: the global template body plus a
``## Project bindings`` section. Each check below is one pure function that
returns a list of violations; the real file must produce none, and each check
has a mutation control proving a deliberately broken variant is caught.

The compiler's ``--check`` verifies provenance, not body presence, so the
template-body checks here are the only guard against a body that was cut out.

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

from tests.harness import _agent_contract as contract

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_PATH = REPO_ROOT / ".claude" / "agents" / "spec-auditor.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "spec-auditor" / "AGENT.md"
CHECKLIST_PATH = REPO_ROOT / ".claude" / "agents" / "_refs" / "spec-review-checklist.md"
GLOBAL_CHECKLIST_PATH = Path.home() / ".claude" / "agents" / "_refs" / "spec-review-checklist.md"
TEMPLATE_PATH = Path.home() / ".claude" / "agents" / "spec-auditor.md"
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
TEMPLATE_TITLE = "# spec-auditor"
DIRECTIVE_MARKER = "**Prime Directive:**"

# One distinctive phrase per distinct rule in the bindings section. Each occurs exactly once
# there, and removing it alone must make the rule check report exactly that rule.
BINDING_ANCHORS: dict[str, str] = {
    "ground-truth-at-ref": "Ground truth is the code at the brief's ref",
    "preamble-commands": "Run `git rev-parse HEAD` and `git status -sb`",
    "git-show-at-ref": "when HEAD differs from the ref, read with `git show SHA:path`",
    "doc-locators-are-historical": (
        "Every path:line in `.claude/CLAUDE.md`, `DECISIONS.md`, `docs/audit`, older plans and "
        "docstrings is a historical locator only"
    ),
    "refind-symbol-at-ref": "re-find the symbol at the ref",
    "line-drift-large": "Line drift in this repo is large and some doc pointers name moved files",
    "evidence-ranks-by-execution": "Evidence ranks by what executes",
    "docs-rank-below-executing-code": (
        "Docstrings, code comments, plan files and `.claude/CLAUDE.md` summaries rank below "
        "executing code"
    ),
    "only-executing-path-settles": "when documents disagree only the executing path settles it",
    "append-only-decision-log": (
        "`DECISIONS.md` is append-only and the first half of an entry is often superseded by its end"
    ),
    "grep-decision-ids-and-symbols": (
        "grep each decision id the spec cites, plus the surface's symbols"
    ),
    "decision-search-sources": (
        "across `DECISIONS.md`, `docs/audit/`, `docs/audit-inputs/` and `docs/generated/`"
    ),
    "read-each-entry-to-its-end": "and read each entry to its end",
    "large-files-searched": "Large files are searched, not read whole",
    "large-file-sizes": (
        "`.claude/CLAUDE.md` (about 72k tokens) and `DECISIONS.md` (about 11k lines) exceed a read"
    ),
    "targeted-grep-then-ranged-reads": "targeted grep, then ranged reads",
    "whole-read-truncates-silently": (
        "A whole-file read truncates silently and the missed part is where the supersession sits"
    ),
    "production-data-is-question": "Production data shape is a QUESTION",
    "production-data-not-confirmable": (
        "Counts, history depth, churn, latency and live Composer behaviour cannot be confirmed "
        "from code"
    ),
    "classify-question-with-query": (
        "Classify the claim QUESTION and name the read-only query that would settle it"
    ),
    "plan-format-decides-review": "Plan format decides the review",
    "scaffold-governs-new-plans": "The global `/scaffold` template governs new plans",
    "plan-format-legacy-shape": (
        "use a legacy shape (Summary, AC checkboxes, Architecture, Edge Cases, Testing Strategy, "
        "Scope Boundaries) and are reviewed as written"
    ),
    "new-format-named-files-outside-contract": (
        "In a new-format plan, named files, functions or modules outside the observable contract "
        "(routes, JSON fields, log text, rendered labels)"
    ),
    "gate-2-findings-section-d": "are Gate-2 findings, section D",
    "earlier-criteria-label": "Earlier criteria on the same surface",
    "earlier-criteria-suffixes": (
        "Search every `feature-plans/*.md` including the `.completed.md` and `.obsolete.md` "
        "suffixes and the suite folders"
    ),
    "status-line-not-evidence": (
        "Plan files are not updated when a later decision supersedes them, so a Status line is "
        "not evidence of correctness"
    ),
    "migration-numbers": (
        "Migration numbers come from the `migrations/` listing and `database._MIGRATION_FILES` "
        "at the ref, never from `DECISIONS.md`"
    ),
    "two-decisions-claimed-same-number": "because two decisions have claimed the same number",
    "no-trade-boundary": "No-trade boundary",
    "new-route-off-settings-allowlist": (
        "Any new route, helper or advisory module must stay off the settings write allowlist"
    ),
    "engine-and-draft-client-off-call-path": (
        "keep `LIVE_EXECUTION`, the engine module, the Composer draft client and symphony "
        "creation off its transitive call path"
    ),
    "extend-ast-boundary-scans": (
        "extend the existing AST boundary scans (module scan, transitive call-graph walk, "
        "scoped source window)"
    ),
    "spec-names-scan-and-non-vacuity": (
        "The spec must name which scan covers each new path and what makes it non-vacuous"
    ),
    "single-hop-scan-is-not-proof": (
        "The dashboard no-order-path test is single-hop and inspects only route view-function "
        "bodies, so it is never proof of a negative on its own"
    ),
    "order-capable-paths-are-the-only-ones": (
        "The order-capable paths that exist are the only ones: panic sell, Managed Sleeves "
        "orders, and undeployed symphony creation on proposal approve"
    ),
    "another-order-path-contradicts-boundary": (
        "A spec adding another contradicts the boundary unless it cites explicit new "
        "authorization for that path"
    ),
    "live-trading-two-flags": "Effective live trading is `LIVE_EXECUTION` AND the symphony's `live_mode`",
    "arming-sentence-names-flag": "a spec sentence about arming must say which flag",
    "post-route-inventory": (
        "Negative claims about the dashboard are checked against the full POST route inventory"
    ),
    "many-post-routes-not-all-allowlisted": (
        "Many POST routes exist and not every write route uses the settings allowlist"
    ),
    "verify-against-every-route": "is verified against every route, never against a summary line",
    "refusal-chain-in-order": "Refusal chain for a new POST route, in order",
    "auth-hook-first": "the global auth hook",
    "auth-hook-302-html": "302 HTML",
    "auth-hook-401-json": "401 JSON for API or XHR",
    "auth-hook-503-misconfigured": "503 when misconfigured",
    "csrf-403": "CSRF 403",
    "body-cap-413": "the app-wide 64 KiB body cap (413)",
    "route-level-400": "then route-level 400",
    "sanitized-500": "and sanitized 500",
    "no-json-error-handler": "There is no JSON error handler",
    "403-413-bodies-are-framework-html": "so 403 and 413 bodies are framework HTML",
    "tests-disable-auth-and-csrf": "Tests disable auth and CSRF by default",
    "auth-csrf-re-enabled": "any security criterion needs a scenario that re-enables the flag",
    "criterion-without-scenario-unverifiable": "a criterion without one is unverifiable as written",
    "data-honesty-checks": "Data honesty checks",
    "number-states-period-subject-units-source-coverage": (
        "Every displayed number states period, subject, units, source and coverage"
    ),
    "ui-says-when-coverage-short": "plus what the UI says when coverage is short of the request",
    "honesty-field-render-consumer": "A computed honesty field with no render consumer is a defect",
    "none-distinct-from-zero": (
        "`None` stays distinct from 0 on every surface (server template, JS poll, frozen "
        "snapshot, API)"
    ),
    "counterfactual-labelled-against-realized": (
        "Counterfactual figures (shadow history, if-held, backtest) are labelled against "
        "realized ones"
    ),
    "counterfactual-vs-realized": "a backtest is never presented as realized history",
    "percent-vs-fraction-stated": "Percent-scale returns versus fraction-scale drawdown must be stated",
    "hash-vs-name-key-space-stated": "Composer hash versus display-name key space must be stated",
    "recurring-defect-classes": "Defect classes this repo repeats, to seed the audit",
    "defect-honesty-signal-never-rendered": "an honesty signal computed but never rendered",
    "defect-rule-at-one-entry-point-only": (
        "a rule enforced at one entry point and missing at another"
    ),
    "defect-built-never-wired-or-codesigned-fixture": (
        "a function built and never wired, or a fixture co-designed with its parser"
    ),
    "defect-documents-as-evidence": "documents treated as evidence of what code does",
    "defect-mixed-basis-units-windows": "mixed basis, units or windows in one comparison",
    "defect-fabricated-zero": "a fabricated zero",
    "engine-zero-diff-guard": "needs a named mechanical guard",
    "content-pin-line-endings-normalized": (
        "a content pin on the engine files, line-ending normalized"
    ),
    "green-counts-not-correctness-evidence": (
        "Green test counts and prior review passes are not correctness evidence"
    ),
    "sensitive-marking": "Sensitive marking",
    "security-covers": "Security covers auth, CSRF, the settings allowlist and credentials",
    "money-definition": (
        "Money covers anything that can move money or drives a real-money keep, retire or "
        "trade decision"
    ),
    "display-only-dollar-figures-not-money": "Display-only dollar figures are not money",
    "bounded-verification-runs": "Verification runs are bounded",
    "never-full-suite-or-more-than-4-workers": (
        "Never run the full test suite or more than 4 xdist workers to verify a claim"
    ),
    "memory-exhaustion-reason": "because it can exhaust memory",
}


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
    if "PlanetStopper" not in description or "spec-writer" not in description:
        problems.append("description must name the project and the paired spec-writer")
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
    nested = tmp_path / "spec-auditor" / "AGENT.md"
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
        pytest.skip("global template ~/.claude/agents/spec-auditor.md is not installed here")
    assert contract.check_template_body_matches(agent_text, contract.read_lf(TEMPLATE_PATH)) == []


def test_template_body_comparison_mutation(agent_text):
    template = "---\nname: spec-auditor\n---\n\n" + str(contract.compiled_template_body(agent_text))
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
        pytest.skip("global template ~/.claude/agents/spec-auditor.md is not installed here")
    broken = contract.without_template_body(agent_text)
    assert contract.check_template_body_matches(broken, contract.read_lf(TEMPLATE_PATH)) != []


def test_every_binding_supplied(agent_text):
    assert check_bindings_supplied(agent_text) == []


def test_binding_anchors_are_unique_and_disjoint(agent_text):
    assert contract.check_anchor_table(agent_text, BINDING_ANCHORS) == []


def test_binding_anchor_table_mutation(agent_text):
    duplicated = agent_text + "\nGround truth is the code at the brief's ref\n"
    assert contract.check_anchor_table(duplicated, BINDING_ANCHORS) != []
    overlapping = {"a": "Money covers", "b": "Money covers anything"}
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


def test_compiled_shape(agent_text):
    assert check_compiled_shape(agent_text) == []


def test_compiled_shape_mutation(agent_text):
    no_provenance = re.sub(r"^<!-- compiled-from: .*$", "", agent_text, flags=re.M)
    assert check_compiled_shape(no_provenance) != []
    assert check_compiled_shape(agent_text + "\n## Project bindings (Other)\n") != []
    assert check_compiled_shape(agent_text + "\n# Extends the template\n") != []


# --------------------------------------------------------------------------
# The project checklist copy: content, not just existence.
# --------------------------------------------------------------------------


def test_project_checklist_copy_matches_pinned_content():
    assert contract.check_checklist_pin(contract.read_lf_if_present(CHECKLIST_PATH)) == []


def test_project_checklist_copy_matches_global_checklist():
    if not GLOBAL_CHECKLIST_PATH.is_file():
        pytest.skip(
            "global checklist ~/.claude/agents/_refs/spec-review-checklist.md not installed"
        )
    project = contract.read_lf_if_present(CHECKLIST_PATH)
    assert (
        contract.check_checklist_matches_global(project, contract.read_lf(GLOBAL_CHECKLIST_PATH))
        == []
    )


def test_project_checklist_copy_mutation(tmp_path):
    original = contract.read_lf(CHECKLIST_PATH)
    first_line, _, rest = original.partition("\n")
    edited = first_line + " edited\n" + rest
    foreign = original + "\n- A foreign check the global checklist does not carry.\n"
    for broken in (edited, foreign):
        assert contract.check_checklist_pin(broken) != []
        assert contract.check_checklist_matches_global(broken, original) != []
    # Absent and empty copies fail both checks.
    assert contract.check_checklist_pin(None) != []
    assert contract.check_checklist_pin("  \n") != []
    assert contract.check_checklist_matches_global(None, original) != []
    # Line endings are normalized, so a CRLF working copy still matches.
    crlf = original.replace("\n", "\r\n")
    assert contract.check_checklist_pin(crlf) == contract.check_checklist_pin(original)
    assert contract.check_checklist_matches_global(crlf, original) == []
    # The reader maps a missing file to None.
    assert contract.read_lf_if_present(tmp_path / "missing.md") is None


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
