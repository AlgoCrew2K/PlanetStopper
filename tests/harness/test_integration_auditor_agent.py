"""Contract test for the integration-auditor agent definition.

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
AGENT_PATH = REPO_ROOT / ".claude" / "agents" / "integration-auditor.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "integration-auditor" / "AGENT.md"
TEMPLATE_PATH = Path.home() / ".claude" / "agents" / "integration-auditor.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"

EXPECTED_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "Bash",
    "Write",
    "SendMessage",
    "TaskUpdate",
    "TaskList",
]
EXPECTED_MODEL = "opus"
EXPECTED_EFFORT = "high"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# Integration Auditor (Generic Template)"
DIRECTIVE_MARKER = "**Prime Directive:**"

# One distinctive phrase per distinct rule in the bindings section. Each occurs exactly once
# there, and removing it alone must make the rule check report exactly that rule.
BINDING_ANCHORS: dict[str, str] = {
    "ground-truth-at-ref": "Ground truth is the code at the brief's ref",
    "preamble-commands": "Run `git rev-parse HEAD` and `git status -sb` and quote both",
    "git-show-at-ref": "when HEAD differs from the ref, read with `git show SHA:path`",
    "doc-locators-are-historical": "is a historical locator only",
    "large-files-searched": "Large files are searched, not read whole",
    "grep-by-symbol-then-range": "are grepped by symbol and decision id and then read by range",
    "whole-read-truncates-silently": "a whole-file read truncates silently and the correction sits in the missed part",
    "one-repo-no-design-tool": "One repository, no design-tool server",
    "single-entry-preamble": "The multi-repo preamble has a single entry and the sibling-repo list is empty",
    "expected-source-order": "Build the expected model in this order",
    "prototypes-not-authority": "never as authority for a surface it does not contain",
    "no-design-source-tag": "`[ASSUMPTION: no design source]`",
    "environment-from-brief": "Environment comes from the brief",
    "missing-environment-is-blocker": "a brief missing one is a blocker reported to the dispatcher, not a guess",
    "expired-sign-in-stops": "a 302 to it means the sign-in expired: stop and report the URL reached, and never sign in",
    "click-rule": "Click rule",
    "post-controls-untouchable": "Treat every control whose handler issues a POST as untouchable",
    "read-method-before-click": "Read the handler's `fetch` method before every click",
    "post-inventory-mutating": "The POST inventory includes real liquidation, sleeve arming",
    "no-trade-boundary": "Audit the no-trade boundary per surface",
    "proposal-approve-only-composer-route": "only the proposal-approve route reaches Composer symphony creation",
    "panic-sell-only-trade-button": "the panic-sell route is the only dashboard trade button",
    "managed-sleeves-order-path": "Managed Sleeves (`sleeves/alpaca_orders.py`) is the one approved order path",
    "live-trading-two-flags": "Effective live trading is the global `LIVE_EXECUTION` AND the symphony's `live_mode`",
    "other-order-path-is-critical": "a chain that reaches an order path outside these is a Critical finding",
    "no-side-effecting-entry": "No side-effecting entry",
    "never-start-daemon-or-import-app": "Never start the daemon, never import `app` outside a throwaway copy with a temp `DB_PATH`",
    "import-initializes-database": "an import runs module-level database initialization",
    "engine-no-blocking-io": "The one-minute engine takes no blocking I/O",
    "engine-unchanged-unless-purpose": "stay unchanged unless the plan's purpose is an engine change",
    "zero-diff-needs-content-pin": "accept a zero-diff claim only with a content pin",
    "three-source-action-enumeration": "Enumerate actions from three sources for this stack",
    "union-aria-onclick-exports": "Union the ARIA tree, the `onclick=` attributes",
    "testid-stable-anchor": "using `data-testid` as the stable anchor",
    "matrix-before-prose": "write the coverage matrix before any finding prose",
    "trace-chains-expected-vs-actual": "Trace UI to API to job chains with expected-versus-actual evidence",
    "ui-api-hops": "the hops are `templates/ai_advisor.html` (first paint)",
    "job-producer-side": "the producer side is the slot registered in `run_scheduler`",
    "cite-every-hop": "Cite `path:line` at every hop, re-found at the ref",
    "each-route-signals-success-its-own-way": "Each route signals success its own way",
    "success-shapes-in-use": "The shapes in use are an absent `error` key, a `success` flag, a `status` field and HTTP 202",
    "advisory-errors-are-http-200": "advisory errors arrive as HTTP 200 with an `error` key",
    "null-error-differs-from-absent": "an `error` key present with a null value is a different shape from an absent key",
    "frontrunner-202-reads-error-only": "the frontrunner run answers 202 with a `status`",
    "non-json-body-throws": "A body that is not JSON",
    "signed-out-post-302": "A signed-out fetch POST to a non-`/api` path gets a 302 to `/login`, not a 401",
    "auth-401-only-for-api-or-xhr": "The auth hook answers 401 JSON only for paths under `/api/`",
    "grep-for-xhr-header": "grep `static/` and `templates/` for that header at the ref",
    "no-client-sets-xhr-header": "no client sets it",
    "csrf-token-process-lifetime": "The CSRF token lives for the daemon process, so a page open across a daemon restart posts a dead token",
    "token-refetch-only-when-empty": "refetching only when its cached value is empty",
    "unset-secret-key-session": (
        "the auth hook refuses every request while `SECRET_KEY` and `FLASK_SECRET_KEY` are "
        "both unset"
    ),
    "only-csrf-token-goes-stale": "only the CSRF token goes stale",
    "never-restart-daemon-to-observe": "Never restart the daemon to observe this",
    "read-routes-free-of-composer": "Read routes must stay free of Composer requests where the plan says so",
    "named-read-routes": (
        "`GET /api/incubation` reads the stored ledger through `database.get_incubation_overview()`"
    ),
    "incubation-emits-no-display-name": "no display name and no `bot_state` read",
    "retirement-route-resolves-names-from-bot-state": (
        "`GET /api/retirement-recommendations` reads stored recommendation rows and resolves "
        "display names from `bot_state`"
    ),
    "browser-capture-hides-vendor-calls": "a browser capture shows only browser to daemon calls and never a vendor call",
    "composer-read-is-finding": "A read that reaches Composer is a finding; a path you cannot trace stays a QUESTION",
    "record-payload-panel-agree": "The job's run record, the API payload and the panel must agree field for field",
    "one-row-per-field": "Tabulate one row per field",
    "request-time-overlays": "request-time overlays such as approval status and display names",
    "no-render-consumer-is-finding": "A field with no render consumer is a finding because no user sees it",
    "payload-panel-disagree-both-layers": "a payload and panel that disagree are a finding on both layers",
    "data-honesty-per-field": "Data honesty is checked per field",
    "none-distinct-from-zero-and-absent": "`None` stays distinct from 0 and from an absent key",
    "counterfactual-labelled": "counterfactual figures (shadow history, if-held, backtest) are labelled",
    "percent-vs-fraction": "percent-scale returns are not mixed with fraction-scale drawdown",
    "picker-key-space": "each picker's key space (Composer hash or normalized display name)",
    "first-paint-vs-poll": "Compare first paint with the poll",
    "poll-and-frozen-states": "the state after the `/api/state` poll and the closed-market frozen branch",
    "separate-renderers": "each is a separate renderer",
    "jobs-evidenced-by-rows": "Jobs are evidenced by rows, never by running them",
    "record-producer-registration": "Record each producer with its `schedule` registration, runner and worker",
    "no-handler-around-run-pending": "The scheduler loop has no exception handler around `run_pending`",
    "exception-ends-loop": "an exception it raises ends the loop",
    "host-local-slot-times": "The daily slots use the host's local time",
    "timezone-is-question": "list the timezone as a QUESTION",
    "job-status-from-rows": "Job status comes from table rows the dispatcher reads",
    "production-data-is-question": "Production data shape is a QUESTION",
    "production-data-not-confirmable": "Counts, history depth and live vendor behaviour cannot be confirmed from code or a throwaway copy",
    "name-settling-query": "name the settling read-only query",
    "vendor-client-scope": "A vendor client is in scope when the route's call graph reaches it",
    "finding-rows-name-missing-consumer": "Finding rows name the missing consumer",
    "missing-consumer-evidence": "for a missing consumer cite the producer line and name the absent consumer in `evidence`",
    "stage-files-by-name": "Stage files by name and never `git add -A`",
    "review-dir-not-gitignored": "`.claude/review/` is not gitignored",
    "hidden-qa-scenarios": "including the hidden qa scenarios",
    "test-runs-append-n0": "Test runs append `-n0` to the default addopts",
    "never-override-addopts": "Never use `-o addopts=`",
    "worker-cap": "never use `-n auto` or more than 4 workers",
    "memory-exhaustion-reason": "the full suite can exhaust memory",
    "report-prose-rules": "Report prose avoids em dashes, en dashes and spaced hyphens",
    "invented-names-reason": "agents copy real names from examples into code and commits",
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
    if "PlanetStopper" not in description or "integration auditor" not in description:
        problems.append("description must name the project and the integration auditor")
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
        check_frontmatter_parses(agent_text.replace("name: integration-auditor", "name: other", 1))
        != []
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
    nested = tmp_path / "integration-auditor" / "AGENT.md"
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
        pytest.skip("global template ~/.claude/agents/integration-auditor.md is not installed here")
    assert contract.check_template_body_matches(agent_text, contract.read_lf(TEMPLATE_PATH)) == []


def test_template_body_comparison_mutation(agent_text):
    template = "---\nname: integration-auditor\n---\n\n" + str(
        contract.compiled_template_body(agent_text)
    )
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
        pytest.skip("global template ~/.claude/agents/integration-auditor.md is not installed here")
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
# The reviewer-role markers the PR gate reads.
# --------------------------------------------------------------------------


def check_review_artifact_paragraph(text: str) -> list[str]:
    """The gate recognizes a reviewer role by its own Review artifact paragraph."""
    problems = []
    if "**Review artifact.**" not in text:
        problems.append("Review artifact paragraph missing")
    if len(re.findall(r"^<!-- compiled-from: ", text, flags=re.M)) != 1:
        problems.append("compiled-from marker missing or duplicated")
    return problems


def test_review_artifact_paragraph_kept(agent_text):
    assert check_review_artifact_paragraph(agent_text) == []


def test_review_artifact_paragraph_mutation(agent_text):
    assert check_review_artifact_paragraph(
        agent_text.replace("**Review artifact.**", "**Artifact.**", 1)
    )
    assert check_review_artifact_paragraph(
        re.sub(r"^<!-- compiled-from: .*$", "", agent_text, flags=re.M)
    )


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
    tampered = tmp_path / "integration-auditor.md"
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
