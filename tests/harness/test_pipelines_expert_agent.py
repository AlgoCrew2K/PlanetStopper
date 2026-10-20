"""Contract tests for ``.claude/agents/pipelines-expert.md``.

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
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "pipelines-expert.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "pipelines-expert" / "AGENT.md"
COMPILE_SCRIPT = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "pipelines-expert.md"

PROJECT_NAME = "PlanetStopper"
BINDINGS_HEADING = f"## Project bindings ({PROJECT_NAME})"
TEMPLATE_TITLE = "# pipelines-expert (generic template)"
DIRECTIVE_MARKER = "**Prime Directive:**"
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
# Template-owned pins; the stub omits them so a recompile re-derives them.
EXPECTED_MODEL = "opus"
EXPECTED_EFFORT = "high"

# One distinctive anchor per distinct rule in the bindings section, keyed by binding group
# (the number before the first dash) and rule. A rule is "supplied" when its anchor is present
# in the bindings section. Each anchor occurs exactly once there, and removing it alone must
# make the check report exactly that rule.
BINDING_ANCHORS: dict[str, str] = {
    "1-read-code-at-ref": "Read the code at the ref, never a remembered citation",
    "1-refind-cited-symbols": "Re-find every cited symbol in the code at the brief's ref",
    "1-line-numbers-drift": "line numbers drift by more than a thousand lines",
    "1-grep-large-files": "by symbol and decision id and never read either whole",
    "1-exclude-worktrees": "Exclude `.claude/worktrees/` copies from repo-wide searches",
    "1-worktrees-stale-duplicates": "they hold stale duplicates of every module",
    "2-stage-vocabulary": "**Stage vocabulary.**",
    "2-no-broker-queue-registry": (
        "This stack has no broker, queue, partition key, dead-letter store or schema registry"
    ),
    "2-stage-is-slot-or-phase": (
        "a stage is a schedule slot in `run_scheduler`, a systemd oneshot, an executor task or "
        "an engine phase"
    ),
    "2-partition-column": (
        "Fill the partition-key column with the stability of the idempotency key"
    ),
    "2-retry-dead-letter-column": (
        "the retry and dead-letter column with the bounded constant and the log-and-continue "
        "behavior"
    ),
    "2-schema-column": (
        "the schema column with additive-column and tolerant-JSON-key compatibility"
    ),
    "2-mapping-reason": "Without this mapping a reviewer invents PASS values",
    "3-scheduler-safety": "**Scheduler safety.**",
    "3-single-thread-no-try": "One scheduler thread calls `run_pending` with no try",
    "3-library-does-not-catch": "the schedule library does not catch job exceptions",
    "3-no-watchdog": "no watchdog exists",
    "3-raise-stops-later-slots": (
        "any job that can raise into it stops every later slot, including the per-minute engine "
        "spawn"
    ),
    "3-wrapper-except-retention": (
        "Every daily slot except the 02:00 retention slot uses the worker plus runner wrapper"
    ),
    "3-worker-lazy-imports-logs-type": (
        "a worker that lazy-imports and catches everything, logging only the exception type"
    ),
    "3-runner-daemon-thread": "a runner that spawns a daemon thread",
    "3-check-by-source": (
        "Check this by reading the registration and the slot's source, because the scheduler "
        "loop never returns"
    ),
    "3-slot-tests-inspect-source": (
        "the existing slot tests inspect `run_scheduler` source instead of running it"
    ),
    "3-retention-unwrapped": (
        "The retention slot runs inline and parses an operator-editable `.env` integer outside "
        "any try"
    ),
    "3-retention-not-precedent": "so it is the known exception and not precedent",
    "3-stagger": "Stagger a new slot to its own minute",
    "4-lock-label": "**Engine lock does not exclude overlap.**",
    "4-stale-expiry": "`execution_lock` has a 60 second stale expiry measured from acquisition",
    "4-no-owner-unconditional-release": "no owner token and an unconditional release",
    "4-eod-and-autotune-in-engine": (
        "the end-of-day step and the weekly autotune run inside the engine subprocess"
    ),
    "4-later-engine-acquires": (
        "A later per-minute engine therefore acquires a lock that a long phase still holds"
    ),
    "4-release-clears-lock": "its release clears the lock for the long phase",
    "4-done-flag-first": (
        "Slow work added to the engine process must persist its done-flag before the slow part"
    ),
    "4-safe-to-overlap": "be safe to overlap, as the end-of-day `post_mortem_run` flag is",
    "4-spawn-no-timeout": (
        "The engine spawn has no timeout, so also flag any new subprocess call without one"
    ),
    "5-dual-producers-label": "**Dual producers of one nightly row.**",
    "5-must-coordinate": "When two producers write the same nightly row, they must coordinate",
    "5-market-prism-precedent": (
        "the Market Prism row is the precedent: the daemon lens pipeline at 03:00 and the "
        "council timer both write it"
    ),
    "5-council-guard-any-producer": (
        "The council guard skips when the latest row of any producer has today's UTC date"
    ),
    "5-daemon-path-unguarded": "the daemon path has no guard",
    "5-disable-flag-any-nonempty": (
        "the disable flag counts any non-empty string, including 0 and false"
    ),
    "5-order-outcomes": "The daemon running first makes the council skip",
    "5-council-first-daemon-later": (
        "the council running first lets a later daemon row become the one readers take"
    ),
    "5-same-minute-two-rows": "a same-minute start can write two rows",
    "6-idempotency-label": "**Idempotency and duplicates against append-only semantics.**",
    "6-append-only-no-unique-key": (
        "Advisory observation rows are append-only with no unique key and no update accessor"
    ),
    "6-duplicate-not-corruption": (
        "a rerun adds a row and a duplicate is not a corruption by itself"
    ),
    "6-judge-by-reader": "Judge a duplicate by what the reader does with it",
    "6-reader-tolerance": (
        "a reader taking the latest row, a read-side latest-date filter or a count each "
        "tolerate it differently"
    ),
    "6-classify-tiers": (
        "Classify every producer as structural (unique key with `INSERT OR IGNORE` or "
        "`ON CONFLICT DO UPDATE`)"
    ),
    "6-read-guarded-forms": "read-guarded (ISO week, UTC date, run id)",
    "6-require-structural": (
        "flag-guarded or unguarded, and require a new producer to be structural"
    ),
    "6-reject-read-then-insert": (
        "Never accept read-then-insert, `INSERT OR REPLACE` or a catch-up timer rerun as "
        "idempotency"
    ),
    "6-reruns-produce-duplicates": "overlapping processes and reruns both produce duplicates",
    "7-time-zones-label": "**Time zones.**",
    "7-host-local-time": (
        "Daily slots in `run_scheduler` run in host-local time while the systemd timers name "
        "America/New_York"
    ),
    "7-et-claim-proves-host-zone": (
        "a claim that a daemon slot and a timer align in ET proves the host timezone first"
    ),
    "7-host-zone-undocumented": "No document in the repository states the host zone",
    "7-mark-unconfirmed": (
        "so mark the claim unconfirmed with the read-only command that settles it"
    ),
    "8-oneshot-label": "**Oneshot behavior.**",
    "8-council-bounded": (
        "The council is bounded by attempts and a cost budget, not wall-clock time"
    ),
    "8-weekly-exits-zero": ("The weekly suggestions run exits 0 even when every engine failed"),
    "8-weekly-reruns": "is rerun by its catch-up timer and by a manual start",
    "8-weekly-guards-first-step": "guards only its first step",
    "8-exit-zero-not-success": (
        "Never treat exit 0 as success for a oneshot that swallows failures"
    ),
    "8-restart-times-retry": (
        "never stack a service restart policy on an in-script retry without computing the "
        "product of attempts"
    ),
    "9-memory-caps-label": "**Memory caps.**",
    "9-cap-3g-never-raised": (
        "Each systemd unit is capped at 3G on a 2 vCPU, 4 GB droplet, and the cap is never raised"
    ),
    "9-higher-cap-wrong": "a finding that asks for a higher cap is wrong by construction",
    "9-standalone-unit-dropin": (
        "A standalone systemd unit needs its own memory drop-in in `deploy/`"
    ),
    "9-weekly-unit-has-none": "(the weekly suggestions unit has none)",
    "9-no-new-fanout": "a new daemon-side parallel fan-out is a finding",
    "9-shared-cap": ("the engine, its autotune and the executors all share the daemon unit's cap"),
    "9-autotune-one-worker": "Autotune parallelism stays at one worker",
    "10-restart-label": "**Restart and shutdown.**",
    "10-restart-kills-batch": "A deploy restart of the daemon kills an in-flight batch",
    "10-control-group-kill": "the unit stop ends every process in its control group",
    "10-no-resume": "no checkpoint or resume exists for the autotune batch",
    "10-executor-waits": "Executor shutdown also waits for queued work before that kill",
    "10-no-restart-friday": (
        "Never restart during the Friday autotune window or while a queued build runs"
    ),
    "10-idempotent-restart-story": (
        "require an idempotent restart story or an operational restriction for any new long job"
    ),
    "11-executors-label": "**Executors and threads.**",
    "11-single-worker-per-class": "Use one single-worker executor per workload class",
    "11-log-and-swallow": (
        "wrap submitted work in a log-and-swallow closure because an unread Future exception "
        "vanishes"
    ),
    "11-duplicate-request-policy": (
        "state the duplicate-request policy because every queued run executes"
    ),
    "11-raw-threads-exceptions": (
        "Raw per-request threads for heavy work (forced end-of-day, panic liquidation) are "
        "existing exceptions, not precedent"
    ),
    "12-cross-process-label": "**Cross-process state.**",
    "12-bot-state-two-writers": (
        "`bot_state` is one JSON blob rewritten whole by the engine and by the daemon with no "
        "cross-process lock"
    ),
    "12-failed-save-rolls-back": (
        "a failed save rolls the next cycle back and can re-fire the same exit"
    ),
    "12-no-new-writer": ("Never add a writer outside the engine without addressing lost updates"),
    "12-json-native-finite": "write only JSON-native finite values",
    "13-start-cost-label": "**Process start cost.**",
    "13-import-runs-migrations": (
        "Every process that imports `database` runs table creation and migrations"
    ),
    "13-failed-migration-skipped": "a failed migration is logged and skipped",
    "13-boot-race": ("A oneshot or the per-minute engine therefore races the daemon at boot"),
    "13-review-start-up-order": "review start-up order for any new process",
    "14-no-trade-label": "**No-trade boundary.**",
    "14-advisor-advisory-only": "AI Advisor surfaces are advisory only",
    "14-proposal-approve-undeployed": (
        "the proposal-approve route is the only one that reaches symphony creation and it "
        "creates an undeployed symphony"
    ),
    "14-panic-sell-only-button": ("the panic-sell route is the only dashboard trade button"),
    "14-managed-sleeves": "Managed Sleeves is the one approved order path",
    "14-scheduled-job-no-orders": "A scheduled job never places, deploys or arms anything",
    "14-engine-no-blocking-io": "The 1-minute engine allows no blocking I/O",
    "14-zero-diff-content-pin": (
        "an engine change needs a content-pinned zero-diff test when the plan claims none"
    ),
    "15-evidence-label": "**Evidence standard.**",
    "15-fixture-proves-shape": "A fixture-only or mocked pass proves shape only",
    "15-no-broker-phase": "this repository has no broker phase",
    "15-live-phase-pm-evidence": (
        "report the live-phase line as the PM's read-only droplet evidence"
    ),
    "15-unconfirmed-with-command": (
        "Production facts such as timezone, applied drop-ins, durations and overlap counts are "
        "marked unconfirmed with the settling command"
    ),
    "15-unless-pm-read": "unless the brief carries a PM read",
    "17-findings-label": "**Findings format.**",
    "17-contract-fields": (
        "Each finding carries the review contract fields (id, class, trigger, scenario, "
        "sibling search)"
    ),
    "17-blocking-basis": "with blocking only on a stated basis",
    "17-pr-gate-reads-blocking": "because the PR gate reads the blocking field",
    "18-tests-staging-label": "**Tests and staging.**",
    "18-append-n0": "Test runs append `-n0` to the default addopts",
    "18-never-replace-addopts": "never replace them with `-o addopts=`",
    "18-deselection-reason": "which also drops the live, slow and perf deselection",
    "18-worker-cap": "never use `-n auto` or more than 4 workers",
    "18-stage-by-name": "Stage files by name and never `git add -A`",
    "18-review-not-gitignored": "`.claude/review/` is not gitignored",
    "18-hidden-qa-scenarios": (
        "a broad add would commit review artifacts including the hidden qa scenarios"
    ),
    "19-prose-label": "**Prose.**",
    "19-prose": (
        "No em dashes, en dashes or spaced hyphens in the review artifact or in anything you write"
    ),
    "16-data-honesty-label": "**Data honesty in what a pipeline produces.**",
    "16-number-states-basis-unit-window-subject-source": (
        "Every number a stage writes or a report row carries states its basis, unit, window, "
        "subject and source"
    ),
    "16-none-distinct-from-zero": "`None` stays distinct from `0` and an absent key from a null",
    "16-counterfactual-labelled": (
        "A counterfactual figure (shadow history, if-held, backtest) is labelled as such and "
        "never shown as realized"
    ),
    "16-backtest-not-a-fill": "a backtest is not a realized fill",
    "16-percent-versus-fraction": (
        "Returns are percent-scale while Composer drawdown is a fraction"
    ),
    "16-check-scale-and-key-space": (
        "check the scale and the key space at every hand-off from producer to reader"
    ),
    "19-prose-invented-names": "invented names only in examples",
    "19-prose-real-name-copied": "a real name in an example is copied into code and commits",
}


def read_agent() -> str:
    return contract.read_lf(AGENT_FILE)


# ---------------------------------------------------------------------------
# Checks: each returns a list of problems (empty means the rule holds)
# ---------------------------------------------------------------------------


def check_frontmatter(text: str, stem: str = "pipelines-expert") -> list[str]:
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


def check_pins(text: str) -> list[str]:
    fm, _ = contract.split_frontmatter(text)
    problems = []
    if fm.get("model") != EXPECTED_MODEL:
        problems.append(f"model is {fm.get('model')!r}, expected {EXPECTED_MODEL!r}")
    if fm.get("effort") != EXPECTED_EFFORT:
        problems.append(f"effort is {fm.get('effort')!r}, expected {EXPECTED_EFFORT!r}")
    if "skills" in fm:
        problems.append("frontmatter carries skills, which this role does not use")
    return problems


def check_compiled_shape(text: str) -> list[str]:
    problems = []
    provenance = re.findall(
        r"^<!-- compiled-from: ~/\.claude/agents/pipelines-expert\.md sha256:[0-9a-f]{12} -->$",
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
    nested = tmp_path / "pipelines-expert" / "AGENT.md"
    nested.parent.mkdir()
    nested.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(AGENT_FILE, nested), "a nested AGENT.md must be flagged"
    assert contract.check_flat_layout(tmp_path / "absent.md", tmp_path / "none.md"), (
        "an absent flat file must be flagged"
    )
    elsewhere = tmp_path / "other" / "pipelines-expert.md"
    elsewhere.parent.mkdir()
    elsewhere.write_text("x", encoding="utf-8")
    assert contract.check_flat_layout(elsewhere, tmp_path / "none.md"), (
        "a file outside an agents folder must be flagged"
    )


def test_frontmatter_parses(agent_text):
    assert check_frontmatter(agent_text) == []


def test_frontmatter_mutation_control(agent_text):
    broken = agent_text.replace("name: pipelines-expert", "name: pipelines-expert-v2", 1)
    assert check_frontmatter(broken), "wrong name must be flagged"
    assert check_frontmatter(agent_text.replace("---", "", 1)), "missing fence must be flagged"
    assert check_frontmatter(agent_text.replace("\ntools:", "\nmemory: project\ntools:", 1))


def test_tools_match_the_design(agent_text):
    assert check_tools(agent_text) == []


def test_tools_mutation_control(agent_text):
    removed = agent_text.replace("Write, SendMessage,", "Write,", 1)
    assert check_tools(removed), "a removed tool must be flagged"
    added = agent_text.replace("TaskList\n", "TaskList, WebFetch\n", 1)
    assert check_tools(added), "an added tool must be flagged"


def test_model_and_effort_follow_the_template(agent_text):
    assert check_pins(agent_text) == []


def test_pins_mutation_control(agent_text):
    assert check_pins(agent_text.replace("model: opus", "model: sonnet", 1))
    assert check_pins(agent_text.replace("effort: high", "effort: low", 1))
    assert check_pins(agent_text.replace("\nmodel:", "\nskills:\n  - x\nmodel:", 1))


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
        "-->\n\n# pipelines-expert (generic template)",
        "-->\n<!-- compiled-from: ~/.claude/agents/pipelines-expert.md sha256:000000000000 -->\n\n# pipelines-expert (generic template)",
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
    template = "---\nname: pipelines-expert\n---\n\n" + str(
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
    if not TEMPLATE_FILE.is_file():
        pytest.skip(f"harness template not installed: {TEMPLATE_FILE}")
    broken = contract.without_template_body(agent_text)
    assert contract.check_template_body_matches(broken, contract.read_lf(TEMPLATE_FILE))


def test_every_project_binding_is_supplied(agent_text):
    assert missing_bindings(agent_text) == []


def test_binding_anchors_are_unique_and_disjoint(agent_text):
    assert contract.check_anchor_table(agent_text, BINDING_ANCHORS) == []


def test_binding_anchor_table_mutation_control(agent_text):
    duplicated = agent_text + "\nStagger a new slot to its own minute\n"
    assert contract.check_anchor_table(duplicated, BINDING_ANCHORS)
    assert contract.check_anchor_table(
        agent_text, {"a": "Stagger a new", "b": "Stagger a new slot"}
    )


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


def test_binding_anchors_cover_all_binding_groups():
    groups = {int(k.split("-", 1)[0]) for k in BINDING_ANCHORS}
    assert groups == set(range(1, 20))


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
    tampered = tmp_path / "pipelines-expert.md"
    tampered.write_text(
        re.sub(r"sha256:[0-9a-f]{12}", "sha256:000000000000", agent_text, count=1),
        encoding="utf-8",
    )
    result = _run_check(tampered)
    assert result.returncode == 1, (
        f"stale copy should exit 1, got {result.returncode} stderr={result.stderr!r}"
    )


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
