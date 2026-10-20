"""Contract tests for ``.claude/agents/implementer.md``.

The agent file is a compiled specialization: the harness template's body plus a
``## Project bindings (PlanetStopper)`` section that this repository owns. These
tests pin the bindings and the frontmatter so an edit that drops a rule, adds a
placeholder or writes history text fails here.

Every check is a pure function over the file text and returns a list of
problems. Each check has one mutation control: a deliberately broken variant of
the text that the same check must flag, so a check that cannot fail is caught.

The compiler's ``--check`` verifies provenance, not body presence, so the
template-body checks here are the only guard against a body that was cut out.

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

from tests.harness import _agent_contract as contract

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "implementer.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "implementer" / "AGENT.md"
COMPILE_SCRIPT = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "implementer.md"

PROJECT_NAME = "PlanetStopper"
BINDINGS_HEADING = f"## Project bindings ({PROJECT_NAME})"
TEMPLATE_TITLE = "# implementer"
DIRECTIVE_MARKER = "**Prime Directive:**"
# The tool list is the parent's, unchanged: the specialization adds and removes nothing.
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
# Template-owned pins; the stub omits them so a recompile re-derives them.
EXPECTED_MODEL = "sonnet"
EXPECTED_EFFORT = "medium"
EXPECTED_CACHE_TTL = "1h"

# One distinctive anchor per distinct rule in the bindings section, keyed by binding group
# (the number before the first dash) and rule. A rule is "supplied" when its anchor is present
# in the bindings section. Each anchor occurs exactly once there, and removing it alone must
# make the check report exactly that rule.
BINDING_ANCHORS: dict[str, str] = {
    "01-label": "**Tests and the handoff file are the whole spec.**",
    "01-plans-denied": "The plans folder (`feature-plans/`) is denied to this role",
    "01-intent-to-test-writer": "A question about intent goes to the test-writer by `SendMessage`",
    "01-no-behaviour-from-maps": (
        "Take no behaviour from `.claude/CLAUDE.md`, `DECISIONS.md` or `docs/generated`"
    ),
    "02-label": "**Ground truth is the code at the brief's ref.**",
    "02-refind-symbols": "Re-find every symbol the tests or handoff cite",
    "02-grep-large-docs": "by symbol and never read either whole",
    "02-exclude-worktrees": "Exclude `.claude/worktrees/` from every search",
    "03-label": "**Reuse before adding.**",
    "03-search-name-variants": "Search under every name variant before writing a helper",
    "03-report-reuse": "Report in the handoff what you reused",
    "04-label": "**No-trade boundary, per surface.**",
    "04-advisory-only": "AI Advisor surfaces are advisory only",
    "04-approved-order-path": "the Managed Sleeves module is the one approved order path",
    "04-effective-live-trading": (
        "Effective live trading is the global `LIVE_EXECUTION` AND the symphony's `live_mode`"
    ),
    "04-report-helpers-near-boundary": (
        "Report every new helper near these surfaces to the test-writer"
    ),
    "05-label": "**Engine files stay untouched by advisory work.**",
    "05-no-engine-symbols": (
        "Add no symbol to `alpha_bot_execution.py` or `math_engine.py` for an advisory or "
        "analysis feature"
    ),
    "05-engine-no-io": "The 1-minute engine path allows no blocking I/O",
    "05-grep-callers": (
        "grep every caller across `alpha_bot_execution.py`, `reporting.py`, "
        "`synthetic_history.py` and `autotuner.py`"
    ),
    "06-label": "**Math constants.**",
    "06-named-constants": (
        "Every constant in math code is a named constant with a source comment and a unit"
    ),
    "07-label": "**Lazy imports.**",
    "07-lazy-in-app": "Import advisors and the autotuner inside the `app.py` functions that use them",
    "08-label": "**Advisory modules never raise.**",
    "08-typed-degraded": "Entry points return typed degraded results with honest availability flags",
    "08-log-exception-type": "Log only `type(exc).__name__` at WARNING or above",
    "08-post-routes-type-name": "Advisory POST routes return the exception type name",
    "09-label": "**Nullable numbers.**",
    "09-is-none": "Use `is None` checks and never falsiness",
    "09-strict-json": "Emit strict JSON by converting NaN and Infinity to null",
    "09-absent-vs-null": "Keep absent distinct from null when the tests pin both",
    "10-label": "**Data honesty in what you emit.**",
    "10-counterfactual": (
        "never present a counterfactual (shadow history, if-held, backtest) as realized"
    ),
    "10-units-and-key-spaces": "Returns are percent-scale while Composer drawdown is a fraction",
    "11-label": "**Persistence seams.**",
    "11-advisor-observation": "Advisory output goes through `database.insert_advisor_observation`",
    "11-insert-or-ignore": "An idempotent append uses a UNIQUE constraint with `INSERT OR IGNORE`",
    "11-upsert-form": "an upsert uses `ON CONFLICT DO UPDATE`, never `INSERT OR REPLACE`",
    "11-database-imports": "`database.py` never imports `advisors.*`",
    "11-no-cross-database-joins": "app code never joins across the three SQLite files",
    "12-label": "**`bot_state` has writers in two processes.**",
    "12-no-new-writer": "Add no `bot_state` writer outside the engine",
    "12-shape-check": "Iterate it only through the symphony-entry shape check",
    "13-label": "**Migrations.**",
    "13-numbering": "numbered one above the highest file in `migrations/` at the ref",
    "13-list-append": "an entry appended at the end of `database._MIGRATION_FILES`",
    "13-never-reorder": (
        "Never reorder the list (it deliberately has 021 before 020), renumber, or edit an "
        "applied file"
    ),
    "13-one-alter": "Keep one ALTER per file",
    "13-additive-columns": "Columns are additive, NULLable, with a constant DEFAULT only",
    "14-label": "**Scheduler work uses the two-function wrapper.**",
    "14-worker": "A worker lazy-imports and catches everything, logging only the exception type",
    "14-runner": "a runner spawns a daemon thread",
    "14-register-staggered": "register the runner in `run_scheduler` at a slot staggered",
    "14-schedule-does-not-catch": "The `schedule` library does not catch exceptions",
    "14-host-local-time": "Daily slots run in host-local time",
    "14-single-worker-executor": "A long on-demand run uses the single-worker executor that returns 202",
    "15-label": "**Web layer.**",
    "15-global-hooks": "Auth and CSRF are global `before_request` hooks",
    "15-never-call-validator": "a new route inherits both and never calls the CSRF validator",
    "15-auth-exempt": "An auth-exempt endpoint is a security-boundary change",
    "15-settings-allowlist": "The settings write allowlist takes only `.env` settings keys",
    "15-body-cap": "The request body cap is 64 KiB",
    "15-ids-not-trees": "a route takes ids and resolves trees server-side",
    "15-read-routes-degrade": "Read routes degrade to an honest empty 200",
    "16-label": "**No code written to satisfy a test stand-in.**",
    "16-no-mock-guards": (
        "Do not add an `isinstance` branch, a `getattr` default or any guard that exists only "
        "to survive a bare mock"
    ),
    "16-ask-for-stand-in": "ask the test-writer for a `SimpleNamespace` or autospec stand-in",
    "17-label": "**Module-level mutable state is reported.**",
    "17-name-new-state": "Name every new cache, throttle or registry in the GREEN report",
    "18-label": "**Logging carries no secrets.**",
    "18-no-secrets": "Log no response bodies, account ids, webhook URLs or tokens",
    "18-allowlist-fixture": "`tests/fixtures/log-redaction-allowlist.txt`",
    "19-label": "**Resource bounds.**",
    "19-bounded-resolvers": "bounded `n_jobs` resolvers",
    "19-no-all-core": "never use `n_jobs=-1`",
    "19-memory-cap": "cap `MemoryMax` at 3G on a 4 GB droplet",
    "20-label": "**Templates and JS.**",
    "20-autoescape": "Keep autoescape (no `| safe`)",
    "20-is-defined": "guard possibly degraded dicts with `is defined and`",
    "20-testid-names": "Use the exact `data-testid` names the tests pin",
    "20-scripts-under-static": "put scripts under `static/`",
    "21-label": "**The developer `.env` is real and is never written.**",
    "21-set-key-writes": "`set_key` in the settings-save route writes to it",
    "21-never-create-edit-print": "Never create, edit or print it",
    "21-settings-save-patched": "never run a settings-save path unpatched",
    "21-report-env-failure": "report an env-sensitive failure to the test-writer",
    "22-label": "**Run discipline.**",
    "22-named-files-one-pytest": "Run only the named spec files, one pytest at a time",
    "22-append-n0": "`-n0` appended to the default addopts",
    "22-no-addopts-override": "Never use `-o addopts=`",
    "22-worker-cap": "never `-n auto` or more than 4 workers",
    "22-consumer-suites": "also run the consumer directories the handoff names",
    "22-report-separately": "report named-file, consumer-suite and lint results separately with the SHA",
    "23-label": "**Style gates are CI gates.**",
    "23-run-ruff": "Run `ruff format --check` and `ruff check` over the changed files",
    "23-ruff-rules": "Ruff line length is 100 with rules E, F, I, B, UP and SIM",
    "23-no-config-edit": "Never edit the ruff configuration",
    "24-label": "**Stage files by name.**",
    "24-pathspec": "Commit by pathspec and never `git add -A`",
    "25-label": "**Names describe behaviour.**",
    "25-no-copied-ids": "Do not copy the plan, acceptance-criterion or cycle identifiers",
    "26-label": "**Prose.**",
    "26-dashes": "No em dashes, en dashes or spaced hyphens in code comments",
    "26-invented-names": "examples use invented names only",
}


def read_agent() -> str:
    return contract.read_lf(AGENT_FILE)


# ---------------------------------------------------------------------------
# Checks: each returns a list of problems (empty means the rule holds)
# ---------------------------------------------------------------------------


def check_frontmatter(text: str, stem: str = "implementer") -> list[str]:
    try:
        fm, _ = contract.split_frontmatter(text)
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
    for forbidden in ("memory", "mcpServers", "skills"):
        if forbidden in fm:
            problems.append(f"frontmatter carries {forbidden}, which this role does not use")
    return problems


def parse_tools(text: str) -> list[str]:
    fm, _ = contract.split_frontmatter(text)
    return [t.strip() for t in str(fm.get("tools", "")).split(",") if t.strip()]


def check_tools(text: str, expected: list[str] = EXPECTED_TOOLS) -> list[str]:
    tools = parse_tools(text)
    if tools != expected:
        return [f"tools is {tools}, expected {expected}"]
    return []


def check_pins(text: str) -> list[str]:
    fm, _ = contract.split_frontmatter(text)
    problems = []
    if fm.get("model") != EXPECTED_MODEL:
        problems.append(f"model is {fm.get('model')!r}, expected {EXPECTED_MODEL!r}")
    if fm.get("effort") != EXPECTED_EFFORT:
        problems.append(f"effort is {fm.get('effort')!r}, expected {EXPECTED_EFFORT!r}")
    if not re.search(
        rf"^experimental:\n[ \t]+cacheTtl: {EXPECTED_CACHE_TTL}$",
        contract.frontmatter_text(text),
        re.MULTILINE,
    ):
        problems.append(f"experimental cacheTtl is not {EXPECTED_CACHE_TTL!r}")
    return problems


def check_compiled_shape(text: str) -> list[str]:
    problems = []
    provenance = re.findall(
        r"^<!-- compiled-from: ~/\.claude/agents/implementer\.md sha256:[0-9a-f]{12} -->$",
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
    return contract.check_rule_anchors(text, BINDING_ANCHORS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def agent_text() -> str:
    assert AGENT_FILE.is_file(), f"agent file missing: {AGENT_FILE}"
    return read_agent()


# ---------------------------------------------------------------------------
# Checks against the real file, each with its mutation control
# ---------------------------------------------------------------------------


def test_agent_file_is_flat_and_registered_by_stem():
    assert contract.check_flat_layout(AGENT_FILE, NESTED_LAYOUT) == []


def test_flat_layout_mutation_control(tmp_path):
    nested = tmp_path / "implementer" / "AGENT.md"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(AGENT_FILE, nested), "a nested AGENT.md must be flagged"
    assert contract.check_flat_layout(tmp_path / "absent.md", tmp_path / "none.md"), (
        "an absent flat file must be flagged"
    )
    elsewhere = tmp_path / "other" / "implementer.md"
    elsewhere.parent.mkdir()
    elsewhere.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(elsewhere, tmp_path / "none.md"), (
        "a file outside an agents folder must be flagged"
    )


def test_frontmatter_parses(agent_text):
    assert check_frontmatter(agent_text) == []


def test_frontmatter_mutation_control(agent_text):
    broken = agent_text.replace("name: implementer", "name: implementer-v2", 1)
    assert check_frontmatter(broken), "wrong name must be flagged"
    assert check_frontmatter(agent_text.replace("---", "", 1)), "missing fence must be flagged"
    assert check_frontmatter(agent_text.replace("\ntools:", "\nmemory: project\ntools:", 1))
    assert check_frontmatter(agent_text.replace("\ntools:", "\nmcpServers: x\ntools:", 1))


def test_tools_match_the_design(agent_text):
    assert check_tools(agent_text) == []


def test_tools_mutation_control(agent_text):
    removed = agent_text.replace("Read, Write, Edit,", "Read, Edit,", 1)
    assert check_tools(removed), "a removed tool must be flagged"
    added = agent_text.replace(", TaskList,", ", TaskList, WebFetch,", 1)
    assert check_tools(added), "an added tool must be flagged"
    no_graph = agent_text.replace(", mcp__codebase-memory-mcp__get_architecture", "", 1)
    assert check_tools(no_graph), "a removed graph tool must be flagged"


def test_tools_equal_the_template_tools(agent_text):
    if not TEMPLATE_FILE.is_file():
        pytest.skip(f"harness template not installed: {TEMPLATE_FILE}")
    assert check_tools(agent_text, parse_tools(contract.read_lf(TEMPLATE_FILE))) == []


def test_template_tools_comparison_mutation_control(agent_text):
    narrowed = [t for t in parse_tools(agent_text) if t != "Bash"]
    assert check_tools(agent_text, narrowed), "a tool the template lacks must be flagged"


def test_model_and_effort_follow_the_template(agent_text):
    assert check_pins(agent_text) == []


def test_pins_mutation_control(agent_text):
    assert check_pins(agent_text.replace("model: sonnet", "model: opus", 1))
    assert check_pins(agent_text.replace("effort: medium", "effort: low", 1))
    assert check_pins(agent_text.replace("cacheTtl: 1h", "cacheTtl: 5m", 1))


def test_pins_record_present(agent_text):
    assert contract.check_pins_record(agent_text) == []


def test_pins_record_mutation_control(agent_text):
    removed = re.sub(r"^<!-- template-owned pins:.*$", "", agent_text, flags=re.MULTILINE)
    assert removed != agent_text
    assert contract.check_pins_record(removed), "a removed pins record must be flagged"
    assert contract.check_pins_record(re.sub(r"model@[0-9a-f]+,?[ \t]*", "", agent_text))
    assert contract.check_pins_record(re.sub(r",?[ \t]*effort@[0-9a-f]+", "", agent_text))


def test_compiled_shape(agent_text):
    assert check_compiled_shape(agent_text) == []


def test_compiled_shape_mutation_control(agent_text):
    no_heading = agent_text.replace(BINDINGS_HEADING, "## Bindings", 1)
    assert check_compiled_shape(no_heading)
    doubled = agent_text.replace(
        "-->\n\n# implementer",
        "-->\n<!-- compiled-from: ~/.claude/agents/implementer.md sha256:000000000000 -->\n\n"
        "# implementer",
        1,
    )
    assert doubled != agent_text
    assert check_compiled_shape(doubled)
    extends = agent_text.replace(BINDINGS_HEADING, "## Extends\n\n" + BINDINGS_HEADING, 1)
    assert check_compiled_shape(extends)


def test_template_body_present(agent_text):
    assert contract.check_template_body_present(agent_text, TEMPLATE_TITLE, DIRECTIVE_MARKER) == []


def test_template_body_present_mutation_control(agent_text):
    broken = contract.without_template_body(agent_text)
    assert broken != agent_text
    assert contract.check_template_body_present(broken, TEMPLATE_TITLE, DIRECTIVE_MARKER)
    for marker in (TEMPLATE_TITLE, DIRECTIVE_MARKER):
        removed = agent_text.replace(marker, "REMOVED", 1)
        assert contract.check_template_body_present(removed, TEMPLATE_TITLE, DIRECTIVE_MARKER)


def test_template_body_matches_global_template(agent_text):
    if not TEMPLATE_FILE.is_file():
        pytest.skip(f"harness template not installed: {TEMPLATE_FILE}")
    assert contract.check_template_body_matches(agent_text, contract.read_lf(TEMPLATE_FILE)) == []


def test_template_body_comparison_mutation_control(agent_text):
    template = "---\nname: implementer\n---\n\n" + str(contract.compiled_template_body(agent_text))
    assert contract.check_template_body_matches(agent_text, template) == []
    assert contract.check_template_body_matches(
        contract.without_template_body(agent_text), template
    )
    edited = agent_text.replace(DIRECTIVE_MARKER, DIRECTIVE_MARKER + " edited", 1)
    assert contract.check_template_body_matches(edited, template)
    crlf = agent_text.replace("\n", "\r\n")
    assert contract.check_template_body_matches(crlf, template.replace("\n", "\r\n")) == []


def test_template_body_removal_fails_against_global_template(agent_text):
    if not TEMPLATE_FILE.is_file():
        pytest.skip(f"harness template not installed: {TEMPLATE_FILE}")
    broken = contract.without_template_body(agent_text)
    assert contract.check_template_body_matches(broken, contract.read_lf(TEMPLATE_FILE))


def test_every_project_binding_is_supplied(agent_text):
    assert missing_bindings(agent_text) == []


def test_binding_anchors_are_unique_and_disjoint(agent_text):
    assert contract.check_anchor_table(agent_text, BINDING_ANCHORS) == []


def test_binding_anchor_table_mutation_control(agent_text):
    duplicated = agent_text + "\nNever edit the ruff configuration\n"
    assert contract.check_anchor_table(duplicated, BINDING_ANCHORS)
    assert contract.check_anchor_table(agent_text, {"a": "Ruff line", "b": "Ruff line length"})


@pytest.mark.parametrize("key", list(BINDING_ANCHORS))
def test_binding_mutation_control(agent_text, key):
    """Removing one rule's anchor text must make exactly that rule newly missing."""
    broken = contract.without_bindings_phrase(agent_text, BINDING_ANCHORS[key])
    assert broken != agent_text
    newly_missing = set(missing_bindings(broken)) - set(missing_bindings(agent_text))
    assert key in missing_bindings(broken)
    assert newly_missing <= {key}


@pytest.mark.parametrize(
    "sentence",
    contract.binding_sentences(contract.read_lf(AGENT_FILE)),
    ids=lambda s: re.sub(r"\W+", "-", s)[:48],
)
def test_deleting_any_binding_sentence_is_detected(agent_text, sentence):
    """Every sentence of the bindings carries at least one anchor, so a deleted rule fails."""
    broken = contract.without_bindings_sentence(agent_text, sentence)
    assert broken != agent_text
    assert missing_bindings(broken)


def test_every_sentence_is_anchored_control(agent_text):
    """Mutation control: an unanchored sentence must not be detectable, proving the check
    above would fail for a sentence that carries no anchor."""
    extra = "\n- A sentence that no anchor covers."
    with_extra = agent_text + extra
    sentence = "A sentence that no anchor covers."
    assert sentence in contract.binding_sentences(with_extra)
    broken = contract.without_bindings_sentence(with_extra, sentence)
    assert broken != with_extra
    assert missing_bindings(broken) == [], "an unanchored sentence deletion goes unnoticed"


def test_binding_anchors_cover_all_binding_groups():
    groups = {int(k.split("-", 1)[0]) for k in BINDING_ANCHORS}
    assert groups == set(range(1, 27))


def test_no_placeholder_in_bindings(agent_text):
    assert contract.check_no_placeholder(agent_text) == []


def test_placeholder_mutation_control(agent_text):
    assert contract.check_no_placeholder(agent_text + "\n- Framework: <framework>\n")
    assert contract.check_no_placeholder(agent_text + "\n- TODO fill in\n")


def test_no_history_text(agent_text):
    assert contract.check_no_history_or_machine_text(agent_text) == []


def test_every_history_pattern_has_a_mutation_sample():
    assert set(contract.HISTORY_SAMPLES) == set(contract.HISTORY_PATTERNS)
    assert set(contract.PROSE_SAMPLES) == set(contract.PROSE_PATTERNS)


@pytest.mark.parametrize(
    ("pattern", "injection"),
    [(name, s) for name, samples in contract.HISTORY_SAMPLES.items() for s in samples],
)
def test_history_mutation_control(agent_text, pattern, injection):
    problems = contract.check_no_history_or_machine_text(agent_text + "\n- " + injection + "\n")
    assert any(p.startswith(f"{pattern}:") for p in problems), problems


def test_history_mutation_control_covers_frontmatter(agent_text):
    broken = agent_text.replace("effort: medium", "effort: medium\nnote: decided 2026-01-02", 1)
    assert contract.check_no_history_or_machine_text(broken)


def test_prose_has_no_em_dash_en_dash_or_spaced_hyphen(agent_text):
    assert contract.check_prose_rules(agent_text) == []


@pytest.mark.parametrize(("pattern", "injection"), list(contract.PROSE_SAMPLES.items()))
def test_prose_mutation_control(agent_text, pattern, injection):
    problems = contract.check_prose_rules(agent_text + "\n- " + injection + "\n")
    assert any(p.startswith(f"{pattern}:") for p in problems), problems


def test_prose_mutation_control_in_nested_bullet_is_not_a_false_alarm(agent_text):
    # Indented list markers are bullets, not spaced hyphens.
    nested = contract.check_prose_rules(agent_text + "\n  - a nested bullet\n")
    assert nested == contract.check_prose_rules(agent_text)


def test_bindings_section_missing_is_reported(agent_text):
    broken = agent_text.replace(BINDINGS_HEADING, "## Bindings")
    assert contract.check_prose_rules(broken)
    assert contract.check_no_history_or_machine_text(broken)
    assert contract.check_no_placeholder(broken)
    assert missing_bindings(broken)


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
    tampered = tmp_path / "implementer.md"
    tampered.write_text(
        re.sub(r"sha256:[0-9a-f]{12}", "sha256:000000000000", agent_text, count=1),
        encoding="utf-8",
    )
    result = _run_check(tampered)
    assert result.returncode == 1, (
        f"stale copy should exit 1, got {result.returncode} stderr={result.stderr!r}"
    )
