"""Contract tests for the project test-writer agent definition.

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
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "test-writer.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "test-writer" / "AGENT.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "test-writer.md"

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
    "mcp__codebase-memory-mcp__search_code",
    "mcp__codebase-memory-mcp__trace_path",
    "mcp__codebase-memory-mcp__get_architecture",
]
EXPECTED_MODEL = "sonnet"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# test-writer"
DIRECTIVE_MARKER = "**Prime Directive:**"

# One entry per binding the project must supply: (label, substrings that must all appear in
# that binding's own bullet).
REQUIRED_BINDINGS = [
    ("Ground truth", ["brief's ref", "path:line", ".claude/worktrees/", "drift"]),
    (
        "Guidance and decision sources",
        [".claude/CLAUDE.md", "DECISIONS.md", "decision id", "append-only"],
    ),
    (
        "No-trade boundary, per surface and never repo-wide",
        [
            "AI Advisor",
            "proposal-approve",
            "panic-sell",
            "Managed Sleeves",
            "LIVE_EXECUTION",
            "live_mode",
            "is_live",
            "settings-writable",
        ],
    ),
    (
        "Boundary scans",
        [
            "no_trade_boundary.py",
            "test_dashboard_no_order_path.py",
            "self-contained",
            "plants a forbidden call",
        ],
    ),
    (
        "Execution engine",
        [
            "blocking I/O",
            "alpha_bot_execution.py",
            "math_engine.py",
            "CRLF normalized to LF",
            "git-history guard",
            "depth one",
        ],
    ),
    (
        "Data honesty observables",
        [
            "basis, unit, window, subject and source",
            "`None` stays distinct from `0`",
            "render consumer",
            "counterfactual",
            "go-live floor",
            "invested-since",
            "percent-scale",
            "Composer hash",
            "normalized display name",
        ],
    ),
    ("Production-data facts", ["read-only query", "live read", "history depth"]),
    ("Test runs", ["-n0", "-o addopts=", "-p no:xdist", "-n auto", "4 workers"]),
    ("Staging", ["pathspec", "git add -A", "not gitignored", "hidden qa scenarios"]),
    (
        "Route tests",
        [
            "database.init_db",
            "!= 404",
            "_auth_check_enabled",
            "_csrf_check_enabled",
            "/api/csrf-token",
            "X-CSRF-Token",
            "real producer",
        ],
    ),
    (
        "Result stand-ins",
        [
            "MagicMock",
            "auto-vivifies",
            "isinstance",
            "SimpleNamespace",
            "create_autospec",
            "`getattr` default",
        ],
    ),
    (
        "Fixture provenance",
        [
            "captured producer output",
            "captured route-shaped body",
            "pinned golden fixture",
            "explicitly labelled",
            "never a mock built to match the parser",
            "*.db",
        ],
    ),
    ("Developer env file", [".env", "ENV_FILE_PATH", "never reads or writes it", "EXIT_AUTHORITY"]),
    (
        "Live seams",
        ["true network boundary", "execution_seam_detector.py", "session-wide sentinel"],
    ),
    (
        "Clock and calendar",
        [
            "get_current_et",
            "ZoneInfo",
            "day-rollover",
            "is_trading_day",
            "`date`, not a `datetime`",
        ],
    ),
    (
        "Migrations",
        ["migrations/", "_MIGRATION_FILES", "never that it is last", "run_migrations()"],
    ),
    (
        "RED integrity",
        ["`skip`, `xfail` or `importorskip`", "--strict-markers", "pyproject.toml", "ask the PM"],
    ),
    ("Naming", ["global naming rule", "acceptance-criterion", "neighbours"]),
    (
        "Handoff and seams",
        ["not the plan", ".claude/review/", "patch target", "threading.Thread", "reset fixture"],
    ),
    ("Consumer suites", ["tests/execution/", "tests/engine/", "whole `math_engine` attribute"]),
    ("UI and JS", ["data-testid", "node -e", "test_js_syntax.py"]),
    ("Branch integration", ["origin/main", "never merge to main", "push owner"]),
    ("Math-layer division", ["quant-test-writer", "tests/fixtures/math/", "one closer"]),
    ("Prose", ["em dashes", "en dashes", "spaced hyphens", "invented names"]),
]

# One anchor per distinct rule in the bindings section: the opening words of each sentence.
# Each anchor occurs exactly once in the bindings, and removing it alone must make the rule
# check report exactly that rule.
BINDING_RULES: dict[str, str] = {
    "ground-truth-the-code-at-the": "**Ground truth:** the code at the brief's ref.",
    "re-find-every-symbol-at-that": "Re-find every symbol at that ref and never copy a",
    "exclude-claude-worktrees-from-repo-wide": "Exclude `.claude/worktrees/` from repo-wide",
    "guidance-and-decision-sources-claude-claude": "**Guidance and decision sources:**",
    "decisions-md-is-append-only-and": "`DECISIONS.md` is append-only and a correction",
    "no-trade-boundary-per-surface-and": "**No-trade boundary, per surface and never",
    "the-only-route-that-reaches-composer": "The only route that reaches Composer symphony",
    "the-panic-sell-route-is-the": "The panic-sell route is the only dashboard trade",
    "the-managed-sleeves-module-sleeves-alpaca": "The Managed Sleeves module",
    "effective-live-trading-is-the-global": "Effective live trading is the global",
    "boundary-scans-a-new-route-or": "**Boundary scans:** a new route or helper near",
    "each-scanner-stays-self-contained-duplicating": "Each scanner stays self-contained, duplicating its",
    "an-extension-plants-a-forbidden-call": "An extension plants a forbidden call and shows the",
    "execution-engine-the-1-minute-execution": "**Execution engine:** the 1-minute execution engine",
    "a-zero-diff-claim-is-a": "A zero-diff claim is a content pin: the sha256 of",
    "a-git-history-guard-is-never": "A git-history guard is never used, because CI",
    "data-honesty-observables-every-number-a": "**Data honesty observables:** every number a test",
    "none-stays-distinct-from-0-and": "`None` stays distinct from `0`, and absent from",
    "a-computed-field-with-no-render": "A computed field with no render consumer is not",
    "a-counterfactual-result-shadow-history-if": "A counterfactual result (shadow history, if-held",
    "shadow-history-begins-at-the-production": "Shadow history begins at the production go-live",
    "returns-are-percent-scale-while-composer": "Returns are percent-scale while Composer drawdown",
    "production-data-facts-row-counts-history": "**Production-data facts:** row counts, history",
    "a-criterion-that-needs-one-goes": "A criterion that needs one goes to `main` with the",
    "test-runs-run-only-the-named": "**Test runs:** run only the named spec files, one",
    "never-use-o-addopts": "Never use `-o addopts=`, because the live, slow and perf deselection",
    "never-use-p-no-xdist": (
        "never use `-p no:xdist`, because the default addopts carry `-n 2 --dist loadfile`"
    ),
    "no-xdist-run-stops-unrecognized-argument": (
        "a run without the plugin stops with an unrecognized-argument error instead of running"
    ),
    "never-use-n-auto-or-more": "Never use `-n auto` or more than 4 workers, because",
    "staging-stage-and-commit-by-pathspec": "**Staging:** stage and commit by pathspec and never",
    "route-tests-import-app-under-a": "**Route tests:** import `app` under a patched",
    "assert-status-code-404-first-so": "Assert `status_code != 404` first, so a missing",
    "the-root-conftest-turns-the-auth": "The root conftest turns the auth and CSRF hooks off",
    "a-route-that-calls-a-new": "A route that calls a new producer function also",
    "result-stand-ins-a-magicmock-auto": "**Result stand-ins:** a `MagicMock` auto-vivifies",
    "a-test-builds-a-route-s": "A test builds a route's result object as a",
    "fixture-provenance-a-fixture-is-one": "**Fixture provenance:** a fixture is one of four",
    "it-is-never-a-mock-built": "It is never a mock built to match the parser",
    "database-fixtures-are-json-seeded-at": "Database fixtures are JSON seeded at test time",
    "developer-env-file-the-developer-env": "**Developer env file:** the developer `.env` is",
    "each-test-sets-or-deletes-every": "Each test sets or deletes every env var its subject",
    "live-seams-mock-the-llm-composer": "**Live seams:** mock the LLM, Composer, Alpaca and",
    "only-mongo-has-a-session-wide": "Only Mongo has a session-wide sentinel, and a",
    "clock-and-calendar-pin-the-clock": "**Clock and calendar:** pin the clock the subject",
    "market-calendar-is-trading-day-takes": "`market_calendar.is_trading_day` takes a `date`",
    "its-results-are-cached-so-a": "Its results are cached, so a test that changes the",
    "migrations-take-the-number-from-the": "**Migrations:** take the number from the",
    "apply-it-through-the-real-init": "Apply it through the real `init_db()` and",
    "red-integrity-a-red-test-fails": "**RED integrity:** a RED test fails on an assertion",
    "add-no-pytest-marker-because-strict": "Add no pytest marker, because `--strict-markers`",
    "never-mark-a-contract-or-security": "Never mark a contract or security test `slow` or",
    "naming-the-global-naming-rule-wins": "**Naming:** the global naming rule wins over",
    "handoff-and-seams-the-implementer-reads": "**Handoff and seams:** the implementer reads the",
    "name-each-patch-target-the-source": "Name each patch target (the source module for",
    "consumer-suites-when-production-changes-reach": "**Consumer suites:** when production changes reach",
    "ui-and-js-assert-server-rendered": "**UI and JS:** assert server-rendered UI through",
    "add-no-per-file-syntax-test": "Add no per-file syntax test, because",
    "branch-integration-merge-origin-main-into": "**Branch integration:** merge `origin/main` into",
    "math-layer-division-when-the-brief": "**Math-layer division:** when the brief names",
    "prose-no-em-dashes-en-dashes": "**Prose:** no em dashes, en dashes or spaced",
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
    if keys.get("name") != "test-writer":
        problems.append(f"name is {keys.get('name')!r}, expected 'test-writer'")
    desc = keys.get("description", "")
    if not desc:
        problems.append("description missing")
    elif "failing" not in desc or "implementer" not in desc or "PlanetStopper" not in desc:
        problems.append("description does not name the project, the failing tests and implementer")
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
            "global template ~/.claude/agents/test-writer.md is not installed on this runner"
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
    assert broken != read_agent()
    assert check_frontmatter(broken) != []


def test_mutation_frontmatter_wrong_name():
    broken = read_agent().replace("name: test-writer", "name: other-writer", 1)
    assert broken != read_agent()
    assert check_frontmatter(broken) != []


def test_mutation_tools_extra_tool():
    broken = read_agent().replace("get_architecture\n", "get_architecture, Skill\n", 1)
    assert broken != read_agent()
    assert check_tools(broken) != []


def test_mutation_tools_dropped_tool():
    broken = read_agent().replace("Read, Write, Edit", "Read, Edit", 1)
    assert broken != read_agent()
    assert check_tools(broken) != []


def test_mutation_model_changed():
    broken = read_agent().replace("model: sonnet", "model: opus", 1)
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
    nested = tmp_path / "test-writer" / "AGENT.md"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(AGENT_FILE, nested) != []
    assert contract.check_flat_layout(tmp_path / "absent.md", tmp_path / "none.md") != []


def test_mutation_compiled_shape_extends_section():
    broken = read_agent().replace(BINDINGS_HEADING, BINDINGS_HEADING + "\n\n# Extends `x`", 1)
    assert broken != read_agent()
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
    assert broken != read_agent()
    assert contract.check_template_body_present(broken, TEMPLATE_TITLE, DIRECTIVE_MARKER) != []


def synthetic_template(text: str) -> str:
    """A template whose body is the compiled file's body: the unbroken side of a comparison."""
    return "---\nname: test-writer\n---\n\n" + str(contract.compiled_template_body(text)) + "\n"


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
            "global template ~/.claude/agents/test-writer.md is not installed on this runner"
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
    assert broken != read_agent()
    assert check_bindings_supplied(broken) != []


def test_mutation_binding_phrase_only_in_another_binding():
    text = read_agent()
    section = contract.bullet_of(contract.bindings_section(text), "Staging")
    assert "pathspec" in section
    moved = text.replace(section, section.replace("pathspec", "REMOVED"), 1) + "\npathspec\n"
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
    duplicate = next(iter(BINDING_RULES.values()))
    assert contract.check_anchor_table(text + "\n" + duplicate + "\n", BINDING_RULES)
    overlapping = {"a": "**Staging:** stage", "b": "**Staging:** stage and commit by pathspec"}
    assert all(contract.bindings_section(text).count(a) == 1 for a in overlapping.values())
    assert contract.check_anchor_table(text, overlapping)


def test_mutation_placeholder_added():
    broken = read_agent() + "\n- Handoffs go to <handoff-path>.\n"
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
    assert broken != read_agent()
    assert contract.check_no_history_or_machine_text(broken) != []


def test_mutation_bindings_section_missing():
    broken = read_agent().replace(BINDINGS_HEADING, "## Something else")
    assert broken != read_agent()
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
    stale = tmp_path / "test-writer.md"
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
