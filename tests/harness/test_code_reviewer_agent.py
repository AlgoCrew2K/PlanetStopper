"""Contract tests for the project code-reviewer agent definition.

Each rule the file must keep is one pure checker returning a list of problems, plus one
mutation control that feeds the checker a deliberately broken variant and asserts it reports a
problem (a check that cannot fail proves nothing). One real child-process case runs the compile
freshness check; it is skipped where the harness scripts are not installed.

The agent file is a compiled specialization: the global template body plus a
``## Project bindings (PlanetStopper)`` section this repository owns. The compiler's own
``--check`` verifies provenance, not body presence, so the template-body checks here are the
only guard against a body that was cut out. The PR gate reads the verdict file this reviewer
writes, so the template's review-artifact paragraph is checked as part of the body.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.harness import _agent_contract as contract

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "code-reviewer.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "code-reviewer" / "AGENT.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "code-reviewer.md"

EXPECTED_TOOLS = ["Read", "Grep", "Glob", "Bash", "Write"]
EXPECTED_MODEL = "opus"
EXPECTED_EFFORT = "high"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# code-reviewer (generic template)"
DIRECTIVE_MARKER = "**Prime Directive:**"
ARTIFACT_MARKER = "**Review artifact.**"

# One entry per binding the project must supply: (label, substrings that must all appear in
# that binding's own bullet).
REQUIRED_BINDINGS = [
    ("Ground truth", ["git rev-parse HEAD", "git status -sb", "git show", "line drift"]),
    ("Guidance sources", [".claude/CLAUDE.md", "DECISIONS.md", "append-only", "to its end"]),
    ("CI already enforces", ["line length 100", "E, F, I, B, UP and SIM", "never a row"]),
    ("Universal standards", ["added lines only", "five-row advisory cap"]),
    (
        "Named constants",
        ["math_engine.py", "source comment", "golden fixture", "tests/fixtures/", "blocking"],
    ),
    ("Evidence-value math", ["quant-code-reviewer", "decision item", "PR gate"]),
    ("The never-raises", ["broad `except`", "honest unavailable marker", "1-minute engine"]),
    (
        "No exception text",
        ["type(exc).__name__", "str(exc)", "WARNING or above", "log-redaction-allowlist.txt"],
    ),
    ("Nullable numbers", ["`is None`", "never coerce `None` to `0`", "NaN and Infinity"]),
    (
        "Web layer",
        ["CSRF hook checks POST only", "_AUTH_EXEMPT_ENDPOINTS", "allowlist", "honest empty 200"],
    ),
    (
        "Lazy imports",
        ["advisors", "autotuner", "two-function wrapper", "does not catch exceptions"],
    ),
    (
        "Additive migrations",
        ["_MIGRATION_FILES", "one ALTER per file", "021 before 020", "ON CONFLICT DO UPDATE"],
    ),
    (
        "The engine files",
        ["alpha_bot_execution.py", "math_engine.py", "content pin", "plan's purpose"],
    ),
    (
        "No-trade boundary",
        [
            "is_live",
            "submit_order",
            "save_symphony",
            "LIVE_EXECUTION",
            "live_mode",
            "proposal-approve",
            "panic-sell",
            "Managed Sleeves",
        ],
    ),
    ("A new helper", ["tests/security/", "non-vacuity", "single-hop"]),
    (
        "Data honesty",
        [
            "basis, unit, window, subject and source",
            "counterfactual",
            "percent-scale",
            "Composer hash",
        ],
    ),
    (
        "Recurring escape classes",
        [
            "parser and fixture co-designed",
            "computed but never rendered",
            "built but never wired",
            "swallowed failure read as data",
            "consumer never enumerated",
            "sibling search",
        ],
    ),
    ("Test strength", ["advisory", "WEAK-TEST", "survivor"]),
    ("Production-data facts", ["unconfirmed", "read-only query"]),
    ("Overlap", ["security audit", "decision item"]),
    (
        "The verdict file",
        ["code-reviewer.json", "role", "40 characters", "path:line", "five advisory rows"],
    ),
    ("Stage by name", ["git add -A", ".claude/review/", "-n0", "-o addopts=", "-n auto"]),
    ("Prose and examples", ["em dashes", "en dashes", "spaced hyphens", "invented names"]),
]

# One anchor per distinct rule in the bindings section. Each anchor occurs exactly once in the
# bindings, and removing it alone must make the rule check report exactly that rule.
BINDING_RULES: dict[str, str] = {
    "ground-truth-is-code-at-ref": "**Ground truth is the code at the brief's ref.**",
    "preamble-bracket-and-git-show": (
        "Run `git rev-parse HEAD` and `git status -sb` before and after the review"
    ),
    "dirty-read-voids-verdict": "a dirty read voids the verdict",
    "refind-symbols-never-copy-line": (
        "Re-find every symbol at the ref and never copy a `path:line` from "
        "`.claude/CLAUDE.md`, `DECISIONS.md`, `docs/generated` or a code comment"
    ),
    "line-drift-is-large": "because line drift in this repository is large",
    "guidance-sources-searched": "**Guidance sources are searched, not read whole.**",
    "guidance-file-and-sizes": (
        "The project guidance file is `.claude/CLAUDE.md` (about 67k tokens), not a root CLAUDE.md"
    ),
    "grep-sections-and-decisions": (
        "grep its Coding Standards, Architecture Constraints and Known Gotchas sections"
    ),
    "decisions-append-only-read-to-end": (
        "`DECISIONS.md` (about 11k lines, append-only) by symbol, then read each hit entry to "
        "its end"
    ),
    "correction-in-later-entry": "because a correction arrives in a later entry",
    "ci-enforces-format-lint-tests": "**CI already enforces format, lint and tests.**",
    "ruff-config": "Ruff runs with line length 100 and rules E, F, I, B, UP and SIM",
    "lint-or-test-failure-never-a-row": "so a lint or test failure is never a row",
    "unenforced-items-read-for": (
        "Docstrings, annotations, comment style and magic numbers are not machine-enforced"
    ),
    "universal-standards-added-lines": "**Universal standards are judged on added lines only.**",
    "old-code-one-advisory-class-row": (
        "Old code carries comments that cite plan or finding ids and functions without "
        "annotations, so flag only added lines, as one advisory class row listing the sites"
    ),
    "advisory-cap-reason": "because a row per site exhausts the five-row advisory cap",
    "named-constants-and-golden-fixtures": "**Named constants and golden fixtures.**",
    "math-literal-needs-name-and-source": (
        "A numeric literal added to `math_engine.py` needs a name and a source comment"
    ),
    "advisor-derivation-comment": (
        "an advisor module follows the same practice with a derivation comment"
    ),
    "math-change-needs-golden-fixture": (
        "A change to a math layer needs a golden fixture under `tests/fixtures/` captured from "
        "the producer, and a math change without one is blocking"
    ),
    "unpinned-output-drifts": (
        "because a constant or formula with no pinned output can drift unseen"
    ),
    "evidence-math-not-judged-here": "**Evidence-value math is not judged here.**",
    "quant-reviewer-owns-statistics": (
        "Statistics, thresholds and gate or composite parity are reviewed by `quant-code-reviewer`"
    ),
    "check-constants-and-fixtures-only": (
        "so this reviewer checks only that constants are named and fixtures exist"
    ),
    "route-to-pm-decision-item": (
        "sends such a finding to the PM as a decision item naming that reviewer"
    ),
    "quant-verdict-outside-gate": (
        "because its verdict is outside the PR gate and a second judgment here would contradict "
        "it in another vocabulary"
    ),
    "never-raises-contract": "**The never-raises advisory contract.**",
    "broad-except-is-the-contract": (
        "A broad `except` at an advisory entry point is the contract, not a finding"
    ),
    "plausible-value-swallow-flagged": (
        "Flag a swallow that returns a plausible value (0, 0.0, or an empty list read as "
        "success) instead of an honest unavailable marker"
    ),
    "engine-path-new-swallow-flagged": "and any new swallow on the 1-minute engine path",
    "swallow-hides-outage": (
        "because a swallowed failure read as data hides the outage from every consumer"
    ),
    "no-exception-text": "**No exception text in responses or logs.**",
    "type-name-only-detail-to-debug": (
        "A response body and a log record at WARNING or above carry `type(exc).__name__` only, "
        "never `str(exc)`, the exception object formatted into the message, or an f-string of "
        "it, and full detail goes to a DEBUG record with `exc_info`"
    ),
    "exception-text-carries-secrets": (
        "because exception text can carry keys, file paths and vendor response bodies"
    ),
    "older-logging-added-lines-only": (
        "Older code logs the exception object at error level, so flag added lines only"
    ),
    "log-field-allowlist": (
        "a new field in a Composer or Alpaca client log line must appear in "
        "`tests/fixtures/log-redaction-allowlist.txt`"
    ),
    "nullable-and-strict-json": "**Nullable numbers and strict JSON.**",
    "is-none-never-falsiness": (
        "Use `is None` and never falsiness for a nullable number, and never coerce `None` to `0`"
    ),
    "zero-is-a-real-value": "because 0 is a real value and the consumers tell the two apart",
    "strict-json-nan-infinity": (
        "A JSON response converts NaN and Infinity to null before `jsonify`"
    ),
    "bare-token-breaks-payload": (
        "because a bare token breaks `response.json()` in the browser for the whole payload"
    ),
    "web-layer": "**Web layer.**",
    "post-route-inherits-hooks": (
        "A new POST route inherits the global auth and CSRF hooks and must call neither"
    ),
    "non-post-mutating-route": (
        "flag a mutating route that is not POST (the CSRF hook checks POST only)"
    ),
    "auth-exempt-and-allowlist-changes": (
        "any change to `_AUTH_EXEMPT_ENDPOINTS` or the settings write allowlist (a decision "
        "item for the PM)"
    ),
    "internal-key-in-public-json": (
        "a handler that returns an internal key in public JSON or a persisted snapshot"
    ),
    "advisory-post-and-read-route-shapes": (
        "An advisory POST returns HTTP 200 with the exception type name in `error` and a "
        "boolean or status word, and a read route degrades to an honest empty 200"
    ),
    "consumers-read-those-shapes": "because the existing consumers read those shapes",
    "lazy-imports-in-app": "**Lazy imports in `app.py`.**",
    "advisors-and-autotuner-inside-functions": (
        "Modules under `advisors` and `autotuner` are imported inside functions, and the "
        "module-level `ai_advisor` import is existing, not a finding"
    ),
    "heavy-optional-dependencies": (
        "because a module-level import puts heavy optional dependencies on daemon start"
    ),
    "scheduled-job-wrapper-and-slot": (
        "A new scheduled job uses the two-function wrapper (a worker that lazy-imports and "
        "catches everything, and a runner that spawns a daemon thread) in a slot staggered "
        "from the existing daily slots"
    ),
    "schedule-does-not-catch": (
        "because the schedule does not catch exceptions and one raise stops every job "
        "including the minute engine spawn"
    ),
    "additive-migrations-append-rule": "**Additive migrations and the append rule.**",
    "only-schema-change-needs-migration": (
        "Only a schema change needs a migration, and an accessor-only change needs none"
    ),
    "migration-numbering-append-additive": (
        "The file goes in `migrations/` one number above the highest numbered file, is "
        "appended at the end of `database._MIGRATION_FILES`, is additive (nullable with a "
        "default, one ALTER per file)"
    ),
    "existing-order-untouched": "the existing order stays untouched with 021 before 020",
    "reordering-corrupts": "because reordering corrupts databases that already applied 021",
    "upsert-and-layering": (
        "Upserts use `ON CONFLICT DO UPDATE` and never `INSERT OR REPLACE`, and `database.py` "
        "imports nothing from `advisors`"
    ),
    "engine-files-out-of-advisory-work": "**The engine files stay out of advisory work.**",
    "no-engine-symbol-for-advisory": (
        "A display, advisory or analysis diff adds no symbol to `alpha_bot_execution.py` or "
        "`math_engine.py`, and neither file imports from `advisors`"
    ),
    "engine-diff-blocking-unless-engine-plan": (
        "so a diff to either is blocking unless the plan's purpose is an engine change"
    ),
    "stray-edit-reaches-live-orders": "because a stray edit there reaches live orders",
    "zero-diff-needs-content-pin": (
        "A zero-diff claim needs the content pin in "
        "`tests/test_mdd_window_truth_zero_diff_guard.py`"
    ),
    "green-tests-not-evidence-of-zero-diff": "and green tests are not evidence of it",
    "no-trade-boundary-wide-vocabulary": (
        "**No-trade boundary, searched with the wide vocabulary.**"
    ),
    "vocabulary-list": (
        "Search the diff for `is_live`, `submit_order`, `liquidate`, `go-to-cash`, `/deploy/`, "
        "`/invest`, `/sell`, `/v2/orders`, `save_symphony`, `composer_draft_client`, "
        "`alpha_bot_execution`, `LIVE_EXECUTION`, `live_mode`, `set_symphony_live_mode` and "
        "`_build_client`, plus the live Alpaca host string even in prose"
    ),
    "scans-cover-overlapping-subsets": (
        "because the boundary tests each scan an overlapping subset of that vocabulary and a "
        "narrower list lets a reachable path through"
    ),
    "per-surface-boundary": (
        "AI Advisor surfaces are advisory only, the proposal-approve route is the only route "
        "that reaches Composer symphony creation (undeployed), the panic-sell route is the "
        "only dashboard trade button, and Managed Sleeves is the one approved order path"
    ),
    "other-path-is-blocking": "so a diff adding any other path is blocking",
    "new-helper-needs-reaching-scan": (
        "**A new helper on an advisory path needs a scan that reaches it.**"
    ),
    "entry-point-list-or-non-vacuity": (
        "Confirm an entry-point list in `tests/security/` reaches the helper, or require the "
        "list extended with a non-vacuity assertion"
    ),
    "scans-cover-only-named-functions": (
        "because those scans cover only the functions they name and the dashboard no-order "
        "test is single-hop"
    ),
    "data-honesty-added-numbers": "**Data honesty on every added number.**",
    "number-states-five-attributes": (
        "A displayed number states its basis, unit, window, subject and source"
    ),
    "counterfactual-labelled": (
        "a counterfactual figure (shadow history, if-held, backtest) is labelled and never "
        "shown as realized"
    ),
    "percent-vs-fraction": "a percent-scale return is not mixed with a fraction-scale drawdown",
    "hash-vs-display-name": "a Composer hash is not mixed with a normalized display name",
    "mixed-bases-look-valid": (
        "because mixed bases produce comparisons that look valid and are not"
    ),
    "escape-classes-sibling-search": "**Recurring escape classes each get a sibling search.**",
    "file-every-site-under-one-class": "File every site the search finds under the one class",
    "fix-at-one-site-leaves-siblings": (
        "because a fix at one site leaves its sibling sites broken"
    ),
    "parser-and-fixture-co-designed": (
        "A parser and fixture co-designed: compare the fixture to a producer capture under "
        "`tests/fixtures/` or to the producer source, and search every test that feeds the "
        "parser"
    ),
    "computed-but-never-rendered": (
        "A value computed but never rendered: grep `templates/` and `static/` for a consumer "
        "of every added field, and a field with no consumer is blocking"
    ),
    "built-but-never-wired": (
        "A function built but never wired: search for a production caller of every added function"
    ),
    "swallowed-failure-read-as-data": (
        "A swallowed failure read as data: search every `except` in the diff"
    ),
    "consumer-never-enumerated": (
        "A consumer never enumerated: when the diff adds a field to a hand-built "
        "per-symphony dict or changes a response shape, search every builder site and every "
        "reader"
    ),
    "opt-in-silent-omission": (
        "a design that needs each caller to opt in and fails silently on omission is a finding"
    ),
    "test-strength-blocking-only-with-weak-test": (
        "**Test strength is blocking only where qa records a WEAK-TEST row.**"
    ),
    "weak-test-examples-advisory": (
        "A tautological helper test, an aggregate asserted only `is not None`, and a mock "
        "whose shape the production path stopped returning are advisory rows"
    ),
    "fault-survivor-blocking-only-as-weak-test": (
        "A fault survivor is blocking only where qa records it as a WEAK-TEST row"
    ),
    "artifact-contract-keeps-it-non-blocking": (
        "because the artifact contract keeps test strength non-blocking without a surviving fault"
    ),
    "js-runtime-survivor-not-blocking": (
        "A surviving JavaScript fault that changes only runtime behaviour is not blocking by itself"
    ),
    "js-covered-by-pins-and-node-stub-dom": (
        "rendered JavaScript is covered by source-text pins and a few node tests against a stub DOM"
    ),
    "production-data-unconfirmed": "**Production-data facts are unconfirmed.**",
    "counts-depth-live-behavior": (
        "Row counts, history depth and live Composer behavior cannot be confirmed from code"
    ),
    "state-unconfirmed-with-query": (
        "so state the claim as unconfirmed with the read-only query that would settle it, "
        "never as a row"
    ),
    "overlap-routed": "**Overlap is routed, not duplicated.**",
    "whole-repo-concern-to-pm": (
        "A whole-repo secret, injection or dependency concern goes to the PM as a decision "
        "item for the security audit rather than as rows"
    ),
    "findings-scoped-to-diff": "because findings are scoped to the diff",
    "verdict-file-follows-contract": "**The verdict file follows the review artifact contract.**",
    "gate-reads-role-file": (
        "The PR gate reads `.claude/review/SHA12/code-reviewer.json` and nothing else about "
        "this reviewer"
    ),
    "artifact-field-shape": (
        "so keep `role` equal to the file name, `sha` the full 40 characters, each `location` "
        "a repository-relative forward-slash `path:line` without spaces, and no more than "
        "five advisory rows"
    ),
    "rejected-file-blocks-pr": "because a file the validator rejects blocks the PR",
    "stage-by-name-write-only-artifact": "**Stage by name and write only the artifact.**",
    "never-git-add-all": (
        "Never `git add -A`, because `.claude/review/` is not gitignored and a broad add "
        "commits review artifacts including the hidden qa scenarios"
    ),
    "test-run-addopts-and-workers": (
        "A test run appends `-n0` to the default addopts, never `-o addopts=` (which also "
        "drops the live, slow and perf deselection), and never uses `-n auto` or more than 4 "
        "workers"
    ),
    "full-suite-exhausts-memory": "because the full suite can exhaust memory",
    "prose-and-examples": "**Prose and examples.**",
    "prose-no-dashes-or-spaced-hyphens": (
        "Use no em dashes, en dashes or spaced hyphens in rows or messages"
    ),
    "invented-names-only": "use invented names only in examples",
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
    if keys.get("name") != "code-reviewer":
        problems.append(f"name is {keys.get('name')!r}, expected 'code-reviewer'")
    if not keys.get("description"):
        problems.append("description missing")
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


def check_review_artifact_paragraph(text: str) -> list[str]:
    """The gate recognizes a reviewer by the compiled-from marker or its own artifact
    paragraph; keep both so a compile or edit cannot make the verdict file unrecognized."""
    body = contract.compiled_template_body(text)
    if body is None:
        return ["bindings heading missing, so the template body cannot be located"]
    problems = []
    if ARTIFACT_MARKER not in body:
        problems.append(f"template body lacks {ARTIFACT_MARKER!r}")
    if "<role>.json" not in body:
        problems.append("template body lacks the <role>.json artifact path")
    if "review-artifact-contract.md" not in body:
        problems.append("template body does not cite the review artifact contract")
    if not re.search(r"^<!-- compiled-from: ~/\.claude/agents/code-reviewer\.md ", text, re.M):
        problems.append("compiled-from marker does not name the code-reviewer template")
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


def test_no_memory_mcp_or_skills_keys():
    assert check_no_unrequested_keys(read_agent()) == []


def test_flat_layout():
    assert contract.check_flat_layout(AGENT_FILE, NESTED_LAYOUT) == []


def test_compiled_shape():
    assert check_compiled_shape(read_agent()) == []


def test_template_body_present():
    assert (
        contract.check_template_body_present(read_agent(), TEMPLATE_TITLE, DIRECTIVE_MARKER) == []
    )


def test_review_artifact_paragraph_kept():
    assert check_review_artifact_paragraph(read_agent()) == []


def test_template_body_matches_global_template():
    if not TEMPLATE_FILE.is_file():
        pytest.skip(
            "global template ~/.claude/agents/code-reviewer.md is not installed on this runner"
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
    broken = read_agent().replace("name: code-reviewer", "name: other-reviewer", 1)
    assert check_frontmatter(broken) != []


def test_mutation_tools_extra_tool():
    broken = re.sub(r"^(tools: .*)$", r"\1, Edit", read_agent(), count=1, flags=re.M)
    assert broken != read_agent()
    assert check_tools(broken) != []


def test_mutation_tools_dropped_tool():
    broken = re.sub(r"^(tools: .*), Write$", r"\1", read_agent(), count=1, flags=re.M)
    assert broken != read_agent()
    assert check_tools(broken) != []


def test_mutation_model_changed():
    broken = read_agent().replace("model: opus", "model: sonnet", 1)
    assert check_model(broken) != []


def test_mutation_effort_changed():
    broken = read_agent().replace("effort: high", "effort: low", 1)
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
    nested = tmp_path / "code-reviewer" / "AGENT.md"
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


@pytest.mark.parametrize("needle", [ARTIFACT_MARKER, "<role>.json", "review-artifact-contract.md"])
def test_mutation_review_artifact_paragraph_part_removed(needle):
    broken = read_agent().replace(needle, "REMOVED", 1)
    assert broken != read_agent()
    assert check_review_artifact_paragraph(broken) != []


def test_mutation_review_artifact_marker_for_wrong_template():
    broken = read_agent().replace("agents/code-reviewer.md sha256", "agents/other.md sha256", 1)
    assert broken != read_agent()
    assert check_review_artifact_paragraph(broken) != []


def synthetic_template(text: str) -> str:
    """A template whose body is the compiled file's body: the unbroken side of a comparison."""
    return "---\nname: code-reviewer\n---\n\n" + str(contract.compiled_template_body(text)) + "\n"


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
            "global template ~/.claude/agents/code-reviewer.md is not installed on this runner"
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
    section = contract.bullet_of(contract.bindings_section(text), "Overlap")
    moved = text.replace(section, section.replace("security audit", "REMOVED"), 1)
    assert check_bindings_supplied(moved + "\nsecurity audit\n") != []


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
    duplicated = text + "\n" + BINDING_RULES["ground-truth-is-code-at-ref"] + "\n"
    assert contract.check_anchor_table(duplicated, BINDING_RULES)
    overlapping = {"a": "Never `git add -A`", "b": "Never `git add -A`, because"}
    assert contract.check_anchor_table(text, overlapping)


def test_mutation_placeholder_added():
    broken = read_agent() + "\n- Findings go to <findings-path>.\n"
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
    stale = tmp_path / "code-reviewer.md"
    text = read_agent()
    stale.write_text(
        re.sub(r"sha256:[0-9a-f]{12}", "sha256:000000000000", text, count=1), encoding="utf-8"
    )
    result = run_compile_check(stale)
    assert result.returncode == 1, (result.returncode, result.stderr)


# --------------------------------------------------------------------------
# Required common rules. The rules live in tests/harness/_agent_contract.py and not in this
# file, and each is carried only when one bullet holds all of its tokens, so a rule this agent
# file never carries fails here even when no anchor above names it and other bullets share its words.
# --------------------------------------------------------------------------

REQUIRED_RULES_EXEMPT: frozenset[str] = frozenset()


def test_required_common_rules_exemption_is_only_the_engine_rule():
    assert REQUIRED_RULES_EXEMPT <= contract.ENGINE_RULE_NAMES


def test_required_common_rules_are_carried():
    assert contract.check_required_common_rules(read_agent(), REQUIRED_RULES_EXEMPT) == []


@pytest.mark.parametrize(
    "rule", [r for r in contract.COMMON_RULES if r not in REQUIRED_RULES_EXEMPT]
)
def test_required_common_rule_bullet_removal_is_detected(rule):
    text = read_agent()
    mutated, removed = contract.without_rule_bullets(text, rule)
    assert removed
    assert mutated != text
    reported = contract.check_required_common_rules(mutated, REQUIRED_RULES_EXEMPT)
    assert rule in reported
    # A deleted bullet can only take away the rules that bullet itself carried.
    assert all(any(contract.bullet_carries(b, r) for b in removed) for r in reported)


def test_required_common_rules_do_not_count_the_template_body():
    text = read_agent()
    head = text[: len(text) - len(contract.bindings_section(text))]
    stripped = head + "## Project bindings (PlanetStopper)\n"
    assert contract.check_required_common_rules(stripped, REQUIRED_RULES_EXEMPT) == [
        r for r in contract.COMMON_RULES if r not in REQUIRED_RULES_EXEMPT
    ]


def test_required_common_rules_report_a_missing_bindings_section():
    broken = read_agent().replace("## Project bindings (PlanetStopper)", "## Something else")
    assert contract.check_required_common_rules(broken, REQUIRED_RULES_EXEMPT) != []
