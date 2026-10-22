"""Contract tests for ``.claude/agents/architect.md``.

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
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "architect.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "architect" / "AGENT.md"
COMPILE_SCRIPT = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "architect.md"

PROJECT_NAME = "PlanetStopper"
BINDINGS_HEADING = f"## Project bindings ({PROJECT_NAME})"
TEMPLATE_TITLE = "# Architect"
DIRECTIVE_MARKER = "**Prime Directive:**"
EXPECTED_TOOLS = ["Read", "Glob", "Grep", "Bash", "Write", "Edit", "TaskUpdate", "TaskList"]
# Template-owned pins; the stub omits them so a recompile re-derives them.
EXPECTED_MODEL = "opus"
EXPECTED_EFFORT = "high"
EXPECTED_SKILLS = ["test-design-contract"]

# One distinctive anchor per distinct rule in the bindings section, keyed by binding group
# (the number before the first dash) and rule. A rule is "supplied" when its anchor is present
# in the bindings section. Each anchor occurs exactly once there, and removing it alone must
# make the check report exactly that rule.
BINDING_ANCHORS: dict[str, str] = {
    "1-read-code-at-ref": "Read the code at the ref, never a remembered citation",
    "1-reread-cited-files": (
        "Re-read every cited file at the plan's ref (`git show` with the plan's sha and the path "
        "when HEAD differs)"
    ),
    "1-no-copied-path-line": (
        "Never copy a `path:line` from `.claude/CLAUDE.md`, code comments or docstrings"
    ),
    "1-line-numbers-drift": "line numbers drift heavily",
    "1-key-files-not-exhaustive": "Key Files table is not exhaustive",
    "1-packages-without-rows": (
        "(packages such as `sleeves/`, `engine/` and `dashboard/` exist with little or no row)"
    ),
    "1-search-name-variants": (
        "Glob and Grep for existing implementations under every name variant"
    ),
    "1-adopt-code-over-doc": "Where a project agent file or doc contradicts the code, adopt the code",
    "1-exclude-worktrees": "Exclude `.claude/worktrees/` copies from repo-wide searches",
    "1-worktrees-hold-stale-duplicates": "they hold stale duplicates of every module",
    "2-adoption-map": "Adoption map: reuse these seams before designing anything new",
    "2-advisory-output-seam": "Advisory output goes through `database.insert_advisor_observation`",
    "2-advisory-observation-append-only": "(append-only, forces advisory-only, no update accessor)",
    "2-mutable-status-table": "Mutable status lives in a separate table live-joined at read time",
    "2-insert-or-ignore": (
        "An idempotent daily append uses a UNIQUE constraint with `INSERT OR IGNORE`"
    ),
    "2-upsert-form": "An upsert uses `ON CONFLICT DO UPDATE`, never `INSERT OR REPLACE`",
    "2-replace-resets-safety-columns": (
        "which deletes and reinserts and has reset preserved safety columns"
    ),
    "2-composer-clients": (
        "Composer calls reuse the existing clients (backtest, draft create, undeployed verify)"
    ),
    "2-composer-header-seam": "the header and base-URL seam in the execution module",
    "2-backtest-error-translator": "operator-facing errors go through the backtest error translator",
    "2-scheduler-wrapper": "Scheduled work uses the scheduler's two-function wrapper",
    "2-worker-lazy-imports-logs-type": (
        "a worker that lazy-imports and catches everything, logging only the exception type"
    ),
    "2-runner-daemon-thread": "a runner that spawns a daemon thread",
    "2-register-staggered": (
        "registered in `run_scheduler` staggered from the existing daily slots"
    ),
    "2-single-worker-executor": "single-worker executor that returns 202",
    "2-shared-helpers": (
        "Reuse the shared hash-to-display-name resolver, the tree identity hash helper and the "
        "JSON-safe float helpers"
    ),
    "2-no-parallel-helpers": "instead of writing parallel ones",
    "3-migrations": "**Migrations.**",
    "3-migration-file": "A new state-DB table or column needs a `migrations/NNN_name.sql` file",
    "3-migration-numbering": "numbered one above the highest existing file",
    "3-migration-list": "an entry appended at the end of `database._MIGRATION_FILES`",
    "3-never-reorder": "Never reorder the list (it deliberately has 021 before 020)",
    "3-never-renumber-or-edit-applied": "renumber, or edit an applied file",
    "3-edited-applied-file-skipped": "because an edited applied file is silently skipped",
    "3-new-tables-migration-only": "New tables are migration-only",
    "3-one-alter": "Keep one ALTER per migration file",
    "3-duplicate-column-marks-applied": (
        "the runner marks the whole file applied on a duplicate-column error, so later "
        "statements never run"
    ),
    "3-add-column-limits": (
        "SQLite `ADD COLUMN` cannot add PRIMARY KEY or UNIQUE columns or expression defaults"
    ),
    "3-additive-nullable-columns": "so columns are additive, NULLable, with a constant DEFAULT only",
    "3-database-imports": "`database.py` never imports `advisors.*`",
    "4-bot-state-writers": "`bot_state` has writers in two processes",
    "4-engine-and-daemon-write": "The engine subprocess and the daemon both write the JSON blob",
    "4-lock-does-not-span": "the in-process lock does not span them",
    "4-no-new-writer": (
        "A design never adds a `bot_state` writer outside the engine without addressing lost updates"
    ),
    "4-bot-state-shape": (
        "Iterate `bot_state` only through the symphony-entry shape check (a dict carrying a name)"
    ),
    "4-non-symphony-top-level-keys": "the blob also holds non-symphony top-level keys",
    "5-web-layer": "**Web layer.**",
    "5-auth-csrf": "Auth and CSRF are global `before_request` hooks",
    "5-new-route-inherits-both": "so a new route inherits both and never calls the CSRF validator itself",
    "5-auth-exempt": (
        "Adding an auth-exempt endpoint is a security-boundary change to raise to the PM, never a "
        "silent design choice"
    ),
    "5-settings-allowlist": (
        "The settings write allowlist is only for `.env` settings keys and never takes advisory writes"
    ),
    "5-body-cap": "The app-wide request body cap is 64 KiB",
    "5-ids-not-trees": (
        "so routes take ids and resolve trees server-side; a tree is never posted from the browser"
    ),
    "5-route-degrade": "Read routes degrade to an honest empty state with HTTP 200",
    "5-exception-type": "Advisory POST routes return the exception type name, never `str(exc)`",
    "5-strict-json": "JSON is strict (NaN and Infinity become null)",
    "5-is-none-checks": "Nullable numbers use `is None` checks, never falsiness",
    "5-none-never-zero": "None is never coerced to 0",
    "5-name-to-hash": "Composer-calling routes resolve name to hash from `bot_state`",
    "5-name-keyed-writes": "accept and suggest write paths are keyed by normalized display name",
    "6-imports-and-blast-radius": "**Imports and blast radius.**",
    "6-lazy-imports": "Advisors and the autotuner are imported inside functions in `app.py`",
    "6-execution-imports-pinned": "tests pin the execution module's module-level imports",
    "6-no-engine-symbols": (
        "Advisory and analysis features put no symbols in `alpha_bot_execution.py` or `math_engine.py`"
    ),
    "6-blast-radius": "name a blast-radius allow-list test for the feature",
    "6-engine-no-io": "The 1-minute engine path allows no blocking I/O",
    "7-never-raises": "Advisory modules never raise",
    "7-typed-degraded-results": (
        "Entry points return typed degraded results with honest availability flags"
    ),
    "7-log-exception-type-only": "log only the exception type at WARNING or above",
    "7-no-trade-primitive": (
        "No invest, deploy, order or `LIVE_EXECUTION` primitive appears on any advisory path"
    ),
    "7-no-trade-scan": (
        "the no-trade boundary AST scans are extended to every new helper, not only to route bodies"
    ),
    "8-resources-and-deploy": "**Resources and deploy.**",
    "8-memory-cap": "The daemon, prism and council systemd units each cap `MemoryMax` at 3G",
    "8-small-droplet": "on a small droplet",
    "8-cap-never-raised": "the cap is never raised",
    "8-memory-dropin": "a new standalone systemd unit needs its own memory drop-in in `deploy/`",
    "8-host-timezone": "Daily scheduler slots run in host-local time",
    "8-et-slot-proves-timezone": "a design that claims an ET slot proves the host timezone",
    "9-composer-limits": "**Composer limits.**",
    "9-composer-413": "Request bodies above about 1 MB are rejected with HTTP 413",
    "9-compact-json": "Compact JSON separators shrink a large tree by several percent",
    "9-no-by-reference-form": "there is no by-reference form and no pre-flight guard today",
    "9-rate-limits-conflict": "Documented rate limits conflict across sources",
    "9-existing-429-handling": "pacing relies on the existing 429 handling as the authority",
    "9-budget-call-count": "a design budgets its call count",
    "9-backtest-dates": "`/backtest` honors `start_date` and `end_date`",
    "9-put-symphony": "`PUT /symphonies/{id}` updates a symphony while keeping version history",
    "9-observed-working-live": "(both observed working live)",
    "9-counterfactual": "`/backtest` output is counterfactual, never realized history",
    "9-realized-from-account-endpoints": (
        "which comes only from the account-scoped portfolio endpoints"
    ),
    "10-concurrency-and-sqlite": "**Concurrency and SQLite.**",
    "10-wal-single-writer": "WAL allows one writer at a time",
    "10-select-then-insert": (
        "a SELECT-then-INSERT check is not atomic under Python's legacy transaction control"
    ),
    "10-use-a-constraint": "so use a constraint",
    "10-connection-timeouts": "Connection timeouts differ per database",
    "10-three-databases": "There are three SQLite files (state, optimization, warehouse)",
    "10-no-cross-database-joins": "app code never joins across them",
    "11-test-plan-against-real-harness": "**Test plan against the real harness.**",
    "11-db-path-sentinel": "`tests/conftest.py` sets a per-xdist-worker `DB_PATH`",
    "11-production-db-refused": "a pytest sentinel refuses the production DB file",
    "11-auth-csrf-fixtures": "Autouse fixtures disable auth and CSRF",
    "11-re-enable-flag": "a test of either behaviour re-enables the flag",
    "11-patched-init-db": "Route tests import `app` under a patched `init_db`",
    "11-markers": "Default addopts deselect the live, slow and perf markers",
    "11-worker-cap": "Never plan `-n auto` or more than 4 workers",
    "11-js-syntax": "JS syntax checks go only through the single parametrized test module",
    "11-math-golden": "A math-layer change needs a golden fixture",
    "11-named-constants": "named constants with a source comment",
    "11-ruff-length": "Ruff line length is 100",
    "12-live-functional-check": "Merge Criteria name the PM's live functional check",
    "12-after-pr-review-real-environment": (
        "After the PR review the PM runs the feature against the real environment"
    ),
    "12-mocked-green-diverges": (
        "because mocked-green has repeatedly diverged from live behaviour in this repo"
    ),
    "13-prose-label": "**Prose.**",
    "13-prose": (
        "No em dashes, en dashes or spaced hyphens in the approach document or in anything you write"
    ),
}


def read_agent() -> str:
    return contract.read_lf(AGENT_FILE)


# ---------------------------------------------------------------------------
# Checks: each returns a list of problems (empty means the rule holds)
# ---------------------------------------------------------------------------


def check_frontmatter(text: str, stem: str = "architect") -> list[str]:
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
    if "mcp__" in str(fm.get("tools", "")) and "mcpServers" not in fm:
        problems.append("tools names an mcp__ entry but mcpServers is absent")
    for forbidden in ("memory", "mcpServers"):
        if forbidden in fm:
            problems.append(f"frontmatter carries {forbidden}, which this role does not use")
    return problems


def check_tools(text: str) -> list[str]:
    fm, _ = contract.split_frontmatter(text)
    tools = [t.strip() for t in str(fm.get("tools", "")).split(",") if t.strip()]
    if tools != EXPECTED_TOOLS:
        return [f"tools is {tools}, expected {EXPECTED_TOOLS}"]
    return []


def check_pins_and_skills(text: str) -> list[str]:
    fm, _ = contract.split_frontmatter(text)
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
    nested = tmp_path / "architect" / "AGENT.md"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(AGENT_FILE, nested), "a nested AGENT.md must be flagged"
    assert contract.check_flat_layout(tmp_path / "absent.md", tmp_path / "none.md"), (
        "an absent flat file must be flagged"
    )
    elsewhere = tmp_path / "other" / "architect.md"
    elsewhere.parent.mkdir()
    elsewhere.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(elsewhere, tmp_path / "none.md"), (
        "a file outside an agents folder must be flagged"
    )


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
        "-->\n\n# Architect",
        "-->\n<!-- compiled-from: ~/.claude/agents/architect.md sha256:000000000000 -->\n\n# Architect",
        1,
    )
    assert check_compiled_shape(doubled)


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
    template = "---\nname: architect\n---\n\n" + str(contract.compiled_template_body(agent_text))
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
    duplicated = agent_text + "\nRuff line length is 100\n"
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


def test_binding_anchors_cover_all_thirteen_binding_groups():
    groups = {int(k.split("-", 1)[0]) for k in BINDING_ANCHORS}
    assert groups == set(range(1, 14))


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
    broken = agent_text.replace("effort: high", "effort: high\nnote: decided 2026-01-02", 1)
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
    tampered = tmp_path / "architect.md"
    tampered.write_text(
        re.sub(r"sha256:[0-9a-f]{12}", "sha256:000000000000", agent_text, count=1),
        encoding="utf-8",
    )
    result = _run_check(tampered)
    assert result.returncode == 1, (
        f"stale copy should exit 1, got {result.returncode} stderr={result.stderr!r}"
    )
