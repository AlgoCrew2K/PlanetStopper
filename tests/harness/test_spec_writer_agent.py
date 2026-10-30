"""Contract tests for the project spec-writer agent definition.

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
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "spec-writer.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "spec-writer" / "AGENT.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "spec-writer.md"

EXPECTED_TOOLS = ["Read", "Grep", "Glob", "Bash", "WebFetch", "Write", "Edit", "SendMessage"]
EXPECTED_MODEL = "opus"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# Spec Writer"
DIRECTIVE_MARKER = "**Prime Directive:**"

# One entry per binding the project must supply: (label, substrings that must all appear in
# that binding's own bullet).
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

# One anchor per distinct rule in the bindings section. Each anchor occurs exactly once in the
# bindings, and removing it alone must make the rule check report exactly that rule.
BINDING_RULES: dict[str, str] = {
    "plans-root-is-repo-root": "the plans root is this repository's root",
    "spec-location": "a spec goes to the `feature-plans/` folder as the plan name plus `.md`",
    "no-project-scaffold-template": (
        "No project scaffold template exists, so the global `/scaffold` tier template is the shape"
    ),
    "template-wins-over-legacy-shape": "It wins over the older plan shape",
    "legacy-shape-sections": (
        "Summary, AC checkboxes, Architecture, Edge Cases, Testing Strategy, Scope Boundaries"
    ),
    "naming-belongs-to-gate-2": (
        "file, function and module naming belongs to the architect's Gate-2 document "
        "and never to the spec"
    ),
    "guidance-file-location": "the guidance file is `.claude/CLAUDE.md`, not a root CLAUDE.md",
    "large-file-sizes": (
        "That file (about 72k tokens) and `DECISIONS.md` (about 11k lines) are too large to read whole"
    ),
    "grep-by-symbols-and-decision-id": (
        "grep each by the touched surface's symbols and by decision id"
    ),
    "read-entries-to-their-end": "then read every hit entry to its end",
    "decisions-append-only": (
        "`DECISIONS.md` is append-only and a correction arrives as a later section or entry"
    ),
    "correction-markers": "(markers such as SUPERSEDED, Revise, Corrected)",
    "first-hit-not-last-word": "so the first hit is never the last word",
    "ground-truth-is-code-at-ref": "**Ground truth:** the code at the brief's ref",
    "preamble-commands": "Run `git rev-parse HEAD` and `git status -sb` first",
    "git-show-at-ref": "when HEAD differs from the ref, read with `git show REF:path/to/file`",
    "never-copy-path-line": (
        "Never copy a `path:line` from the guidance file, older plans, `docs/generated`, "
        "`DECISIONS.md` or a code comment"
    ),
    "line-drift-refind-symbol": "line drift in this repository is large, so re-find every cited symbol at the ref",
    "earlier-criteria-plan-search": (
        "search every `feature-plans/*.md` including the `.completed.md` and `.obsolete.md` "
        "suffix families and the suite folders"
    ),
    "earlier-criteria-other-sources": (
        "plus `DECISIONS.md`, `docs/generated/*.md`, `docs/audit/` and `docs/audit-inputs/`"
    ),
    "status-line-not-evidence": "A plan's Status line is not evidence its text is current",
    "later-decision-supersedes-plan": (
        "because a later decision supersedes plan text without editing it"
    ),
    "ac-ids-collide": "AC ids collide across plans, so cite the plan file plus the AC id",
    "no-trade-boundary-per-surface": "**No-trade boundary, per surface and never repo-wide:**",
    "ai-advisor-advisory-only": "AI Advisor surfaces are advisory only",
    "only-route-to-symphony-creation": (
        "The only route that reaches Composer symphony creation is the proposal-approve route"
    ),
    "approve-creates-undeployed": "and it creates an undeployed symphony",
    "panic-sell-only-trade-button": "The panic-sell route is the only dashboard trade button",
    "managed-sleeves-order-path": (
        "The Managed Sleeves module is an approved order path with its own arming ladder"
    ),
    "live-trading-two-flags": (
        "Effective live trading is the global `LIVE_EXECUTION` flag AND the symphony's `live_mode`"
    ),
    "is-live-never-default": "`is_live` is never a default",
    "never-settings-writable": "`LIVE_EXECUTION` and credentials are never settings-writable",
    "protected-surface-listed": (
        "A spec that adds any path near these states lists the boundary as a protected surface"
    ),
    "names-boundary-tests": (
        "names the existing boundary tests it extends, so a reviewer can see the boundary "
        "stays closed"
    ),
    "engine-no-blocking-io": "the 1-minute execution engine allows no blocking I/O",
    "engine-files-unchanged-protected": (
        "A display, advisory or analysis feature states `alpha_bot_execution.py` and "
        "`math_engine.py` as unchanged protected surfaces"
    ),
    "engine-change-exception": "unless the plan's purpose is an engine change",
    "stray-edit-reaches-live-orders": "because a stray edit there reaches live orders",
    "number-states-five-attributes": "states its basis, unit, window, subject and source",
    "none-distinct-from-zero": "`None` stays distinct from `0`",
    "absent-distinct-from-null": "and absent from null, as separate scenario rows",
    "render-consumer-required": "A field with no render consumer does not satisfy a criterion",
    "criterion-names-onscreen-element": "the criterion names the on-screen text or element",
    "counterfactual-labelled-not-realized": (
        "A counterfactual result (shadow history, if-held, backtest) is labelled and never "
        "presented as realized"
    ),
    "shadow-history-go-live-floor": "begins at the production go-live floor",
    "extending-retention-recovers-nothing": "so a criterion to extend retention recovers nothing",
    "composer-series-reach-invested-since": (
        "Composer's account-scoped series reach each symphony's invested-since date"
    ),
    "symphony-id-kind-stated": (
        "every criterion naming a symphony id says whether it is the Composer hash or the "
        "normalized display name"
    ),
    "hash-and-name-do-not-join": "because the two do not join",
    "percent-vs-fraction-scale": "Returns are percent-scale while Composer drawdown is a fraction",
    "unit-is-part-of-observable": "so the unit is part of the observable",
    "production-data-not-confirmable": (
        "row counts, history depth, churn and live Composer behaviour cannot be confirmed from code"
    ),
    "known-unconfirmed-with-query": (
        "They go under Known-Unconfirmed and Standing Risk with the read-only query that would "
        "settle each"
    ),
    "unless-pm-live-read": "unless the brief carries the PM's live read",
    "security-covers": "security covers auth, CSRF, the settings allowlist and credentials",
    "money-definition": (
        "Money covers anything that can move money or drives a real-money keep, retire or "
        "trade decision"
    ),
    "display-only-dollar-not-money": "A display-only dollar figure is not money",
    "marking-display-only-hides-real-surfaces": "so marking it so would hide the surfaces that are",
    "dod-ruff-format": "`ruff format --check`",
    "dod-ruff-check": "`ruff check`",
    "dod-scoped-pytest-worker-cap": (
        "a scoped pytest run (never `-n auto` and never more than 4 workers, because the full "
        "suite can exhaust memory)"
    ),
    "dod-ci-on-exact-sha": "CI on the exact SHA",
    "dod-pm-live-functional-check": "the PM's live functional check after the PR review",
    "dod-tests-green-not-sufficient": "because tests-green is necessary and never sufficient",
    "fixtures-from-captured-output": "Fixtures come from captured producer output",
    "fixtures-never-hand-built": "are never hand-built to match the parser",
    "parser-bug-cannot-pass-own-test": "so a parser bug cannot pass its own test",
    "docs-generated-per-module": "`docs/generated/` with one file per touched module",
    "docs-generated-index": "`docs/generated/INDEX.md`",
    "docs-readme": "`README.md`",
    "docs-decisions-entry-minted-by-doc-writer": (
        "an appended `DECISIONS.md` entry (minted at ship by the doc-writer and never by the "
        "spec, so ids stay unique)"
    ),
    "docs-key-files-row-for-approval": (
        "a drafted `.claude/CLAUDE.md` Key Files row for PM approval"
    ),
    "prose-no-dashes-or-spaced-hyphens": (
        "no em dashes, en dashes or spaced hyphens in the spec or in any message"
    ),
    "examples-use-invented-names": "Examples use invented names only",
    "real-name-copied-into-code": (
        "because a real name in an example is copied into code and commits"
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
    if keys.get("name") != "spec-writer":
        problems.append(f"name is {keys.get('name')!r}, expected 'spec-writer'")
    desc = keys.get("description", "")
    if not desc:
        problems.append("description missing")
    elif "spec-auditor" not in desc or "feature-plans/" not in desc:
        problems.append("description does not name the paired auditor and the plans folder")
    return problems


def check_tools(text: str) -> list[str]:
    keys = split_frontmatter(text)
    tools = [t.strip() for t in str(keys.get("tools", "")).split(",") if t.strip()]
    if tools != EXPECTED_TOOLS:
        return [f"tools {tools} differ from the parent's {EXPECTED_TOOLS}"]
    return []


def check_model(text: str) -> list[str]:
    keys = split_frontmatter(text)
    problems = []
    if keys.get("model") != EXPECTED_MODEL:
        problems.append(f"model is {keys.get('model')!r}, expected {EXPECTED_MODEL!r}")
    if keys.get("effort") != "high":
        problems.append(f"effort is {keys.get('effort')!r}, expected 'high'")
    return problems


def check_no_unrequested_keys(text: str) -> list[str]:
    keys = split_frontmatter(text)
    return [f"unrequested key {k!r}" for k in ("memory", "mcpServers") if k in keys]


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


def test_tools_match_parent():
    assert check_tools(read_agent()) == []


def test_model_and_effort_follow_template():
    assert check_model(read_agent()) == []


def test_pins_record_present():
    assert contract.check_pins_record(read_agent()) == []


def test_no_memory_or_mcp_keys():
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
        pytest.skip(
            "global template ~/.claude/agents/spec-writer.md is not installed on this runner"
        )
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
    nested = tmp_path / "spec-writer" / "AGENT.md"
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
    return "---\nname: spec-writer\n---\n\n" + str(contract.compiled_template_body(text)) + "\n"


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
        pytest.skip(
            "global template ~/.claude/agents/spec-writer.md is not installed on this runner"
        )
    template = contract.read_lf(TEMPLATE_FILE)
    broken = contract.without_template_body(read_agent())
    assert contract.check_template_body_matches(broken, template) != []


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


def test_mutation_binding_phrase_only_in_another_binding():
    text = read_agent()
    section = contract.bullet_of(contract.bindings_section(text), "Sensitive marking")
    moved = text.replace(section, section.replace("CSRF", "REMOVED"), 1) + "\nCSRF\n"
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
    assert contract.check_anchor_table(
        text + "\nthe plans root is this repository's root\n", BINDING_RULES
    )
    overlapping = {"a": "Money covers", "b": "Money covers anything"}
    assert contract.check_anchor_table(text, overlapping)


def test_mutation_placeholder_added():
    broken = read_agent() + "\n- Plans go to <plan-name>.\n"
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
    broken = read_agent().replace("effort: high", "effort: high\nnote: decided 2026-01-02", 1)
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
    stale = tmp_path / "spec-writer.md"
    text = read_agent()
    stale.write_text(
        re.sub(r"sha256:[0-9a-f]{12}", "sha256:000000000000", text, count=1), encoding="utf-8"
    )
    result = run_compile_check(stale)
    assert result.returncode == 1, (result.returncode, result.stderr)
