"""Contract tests for the project api-expert agent definition.

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
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "api-expert.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "api-expert" / "AGENT.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "api-expert.md"

EXPECTED_TOOLS = ["Read", "Glob", "Grep", "Bash", "Write", "SendMessage", "TaskUpdate", "TaskList"]
EXPECTED_MODEL = "opus"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# api-expert (generic template)"
DIRECTIVE_MARKER = "**Prime Directive:**"

# One entry per binding the project must supply: (label, substrings that must all appear in
# that binding's own bullet).
REQUIRED_BINDINGS = [
    (
        "Ground truth and locators",
        ["git rev-parse HEAD", "git status -sb", "git show", "path:line"],
    ),
    ("Large guidance files", [".claude/CLAUDE.md", "DECISIONS.md", "decision id", "append-only"]),
    (
        "Stack and inventory",
        ["Flask 3.1", "Werkzeug", "route decorators", "sleeves", "trade-adjacent"],
    ),
    ("Schema location", ["schema file", "static/", "templates/", "docs/generated/app.md"]),
    (
        "Auth model",
        [
            "no named scopes",
            "_AUTH_EXEMPT_ENDPOINTS",
            "_SETTINGS_WRITE_ALLOWLIST",
            "LIVE_EXECUTION",
            "PM escalation",
        ],
    ),
    ("Refusal order", ["302", "401", "503", "403", "64 KiB", "get_json", "JSON error handler"]),
    (
        "Route tests hide refusals",
        ["autouse", "tests/conftest.py", "exceptions propagating", "re-enables"],
    ),
    (
        "Advisory envelope",
        ["HTTP 200", "`error` key", "body", "names the field the client reads", "nearest sibling"],
    ),
    ("Failure text", ["exception type name", "`str(exc)`"]),
    (
        "Strict JSON",
        ["allow_nan", "per route", "every new float field", "database._finite_or_none"],
    ),
    (
        "Body shape",
        ["get_json(silent=True) or {}", "AttributeError", "isinstance(body, dict)", "415"],
    ),
    (
        "Backtest error translator",
        ["substring 413", "HTML tag", "first 200 characters", "raw upstream"],
    ),
    (
        "Absent versus null",
        ["`provenance`", "success only", "every return path", "absent or null"],
    ),
    (
        "Data honesty on the wire",
        [
            "basis, unit, window, subject and source",
            "`None` stays distinct from `0`",
            "render",
            "counterfactual",
            "percent-scale",
            "go-live floor",
        ],
    ),
    ("Key spaces", ["Composer hash", "normalized display name", "normalize_name", "bot_state"]),
    (
        "Long work and status writes",
        ["single-worker executor", "202", "duplicate-request policy", "last-writer-wins"],
    ),
    (
        "Persistence boundary",
        ["own connection", "partial-failure", "outbound HTTP", "sqlite-specialist"],
    ),
    (
        "No-trade boundary, per route",
        [
            "AI Advisor",
            "proposal-approve",
            "panic-sell",
            "Managed Sleeves",
            "LIVE_EXECUTION",
            "live_mode",
            "is_live",
            "single-hop",
        ],
    ),
    ("Execution engine", ["alpha_bot_execution.py", "math_engine.py", "blocking I/O"]),
    ("Production-data facts", ["read-only query", "live read"]),
    ("Findings format", ["sibling_search", "`blocking`", "PR gate"]),
    ("Overlap", ["composer-alpaca-integration", "flask-dashboard-specialist"]),
    (
        "Test runs and staging",
        ["-n0", "-o addopts=", "-n auto", "4 workers", "git add -A", ".claude/review/"],
    ),
    ("Prose", ["em dashes", "en dashes", "spaced hyphens", "invented names"]),
]

# One anchor per distinct rule in the bindings section. Each anchor occurs exactly once in the
# bindings, and removing it alone must make the rule check report exactly that rule.
BINDING_RULES: dict[str, str] = {
    "ground-truth-is-code-at-ref": "the code at the brief's ref decides every finding",
    "preamble-commands": "Run `git rev-parse HEAD` and `git status -sb` first",
    "git-show-at-ref": "read with `git show REF:path/to/file`",
    "never-copy-path-line": (
        "Never copy a `path:line` from `.claude/CLAUDE.md`, `DECISIONS.md`, `docs/generated`, "
        "older plans or a code comment"
    ),
    "line-drift-size": "line drift in this repository is often over a thousand lines",
    "refind-symbol-at-ref": "re-find every route and symbol by its decorator or name at the ref",
    "large-files-not-read-whole": (
        "are too large to read whole, so grep each by symbol and by decision id"
    ),
    "read-hit-to-its-end": "read every hit entry to its end",
    "decisions-append-only": "is append-only and a correction arrives as a later entry",
    "first-hit-not-last-word": "so the first hit is never the last word",
    "single-flask-process": "one Flask 3.1 process in `app.py` on the threaded Werkzeug server",
    "in-process-state": (
        "(scheduler thread, caches, the CSRF token, executors, event-stream queues)"
    ),
    "long-handler-holds-thread": "A long handler therefore holds a server thread",
    "no-second-process": "no design may assume a second process",
    "inventory-by-route-decorators": (
        "Build the endpoint inventory by searching `app.py` for route decorators at the ref"
    ),
    "inventory-includes-sleeves-and-stubs": "including the sleeves family and the redirect stubs",
    "inventory-classes": (
        "classify each endpoint as read, advisory write, config write or trade-adjacent"
    ),
    "inventory-completeness-reason": "the audit checklist is only as complete as that inventory",
    "no-route-schema-file": (
        "no route in `app.py` is guarded by a schema file or a validation library"
    ),
    "contract-is-handler-dict-and-consumer": (
        "the browser code in `static/` and `templates/` that reads it"
    ),
    "compare-field-by-field": "Compare the two field by field",
    "generated-docs-are-a-lead": "treat `docs/generated/app.md` as a lead and never as evidence",
    "unread-field-not-delivered": "a field the server computes and no consumer reads is not delivered",
    "no-named-scopes": "there are no named scopes, only one operator session behind the global auth hook",
    "exempt-set": (
        "whose exempt set is `_AUTH_EXEMPT_ENDPOINTS` (login, logout, static, the CSRF token "
        "endpoint and health)"
    ),
    "mutating-route-outside-exempt-set": (
        "Verify a mutating route by confirming it is outside the exempt set"
    ),
    "no-validator-call-in-route": "relies on the global CSRF hook without calling the validator itself",
    "not-added-to-write-allowlist": "is not added to `_SETTINGS_WRITE_ALLOWLIST`",
    "allowlist-never-admits-live-or-credentials": ("never admits `LIVE_EXECUTION` or credentials"),
    "exempt-or-allowlist-change-is-escalation": (
        "Adding an exempt endpoint or an allowlist key is a PM escalation"
    ),
    "escalation-reason-security-boundary": "because both move the security boundary",
    "auth-hook-first": (
        "a request meets the auth hook first (302 to the login page for pages, 401 JSON for "
        "`/api` and XHR, 503 when misconfigured)"
    ),
    "csrf-then-cap-then-validation": (
        "then the CSRF hook (403), then the 64 KiB body cap, then handler validation"
    ),
    "cap-only-when-body-read": "The body cap applies only when a handler reads the body",
    "413-needs-get-json-handler": "a 413 scenario needs a handler that calls `get_json`",
    "unread-body-not-capped": "a new POST that never reads the body is not capped",
    "refusals-are-framework-html": (
        "The 403 and 413 bodies are framework HTML because no JSON error handler exists"
    ),
    "json-client-fails-on-html": "so a client that parses them as JSON fails",
    "tests-disable-auth-and-csrf": (
        "route tests run with auth and CSRF disabled by autouse fixtures in `tests/conftest.py`"
    ),
    "tests-propagate-exceptions": "with exceptions propagating under the TESTING config",
    "refusal-finding-needs-scenario": "A finding about a refusal status or a 500 body",
    "scenario-reenables-flags": (
        "needs a scenario that re-enables the auth or CSRF flag and restores production "
        "exception handling"
    ),
    "default-suite-blind": "because the default suite cannot see those behaviours",
    "advisory-200-with-error-key": (
        "advisory POST routes answer HTTP 200 with an `error` key on failure"
    ),
    "success-read-from-body": (
        "success is read from the body (`error`, `success`, `status`) and never from the "
        "status code"
    ),
    "contract-change-names-client-field": "A contract change therefore names the field the client reads",
    "judge-against-nearest-sibling": (
        "a route is judged against its nearest sibling and not against a generic 4xx expectation"
    ),
    "executor-202": "Executor dispatch answers 202",
    "validation-400-routes": (
        "config, sleeve, exit-turnover, performance and chat validation answer 400"
    ),
    "family-contract-not-swallowed-error": ("is the family contract and not a swallowed error"),
    "failure-text-type-name-only": "a failure response carries the exception type name only",
    "detail-logged-server-side": "with the detail logged server side",
    "str-exc-is-finding": "`str(exc)` or raw upstream text in a response field is a finding",
    "exception-text-leaks": "because exception messages carry keys, paths and vendor detail",
    "allow-nan-default": "Flask's JSON provider leaves `allow_nan` at its default",
    "bare-token-breaks-body": (
        "a NaN or infinity serializes as a bare token and a browser rejects the whole body"
    ),
    "guard-per-route": "the guard is written per route",
    "local-helpers-differ": (
        "The local helpers differ (one raises TypeError on a non-numeric, one handles floats "
        "only, one recurses dicts but not lists)"
    ),
    "check-every-new-float-field": (
        "every new float field is checked against the helper its own route uses"
    ),
    "prefer-finite-or-none": "Prefer `database._finite_or_none` over a fourth local copy",
    "each-copy-has-a-gap": "because each copy has its own gap",
    "array-body-crashes-get": (
        "`request.get_json(silent=True) or {}` followed by `body.get(...)` raises "
        "AttributeError on a non-empty JSON array body"
    ),
    "array-body-answers-500-html": "Flask answers a 500 HTML page",
    "validate-body-shape": (
        "validates the body shape with an `isinstance(body, dict)` check before the first `.get`"
    ),
    "json-routes-read-silent": "a route that must answer JSON reads with `silent=True`",
    "non-silent-read-415": "outside a try block answers an HTML 415",
    "translator-keys-on-substring": (
        "the shared translator tests the error text for the substring 413"
    ),
    "translator-exact-bare-html-tag": (
        "exact lowercased text of a bare html opening tag with no attributes"
    ),
    "upstream-string-shape": (
        "the upstream strings are the HTTP status plus the first 200 characters of the "
        "response body"
    ),
    "html-error-reads-as-too-large": "Any error text holding 413 or that bare opening tag",
    "attributed-html-tag-falls-through": (
        "a page whose html tag carries attributes falls through unchanged"
    ),
    "too-large-misclassification": "reads as a symphony that is too large",
    "structural-classification-only": (
        "A new case needs classification from a status carried on the result and never another "
        "substring test"
    ),
    "no-raw-upstream-text-field": (
        "a response field that forwards raw upstream or transport exception text is a finding"
    ),
    "advisory-routes-disagree": "the advisory routes disagree",
    "sb-run-provenance-success-only": (
        "Strategy Builder run carries `provenance` on success only, as a dict or null"
    ),
    "sb-run-omits-on-error": "omits it on both error returns",
    "sibling-routes-default-everywhere": "put a route-minted default on every return path",
    "additive-field-states-absent-or-null": (
        "An additive field states whether it is absent or null when unavailable"
    ),
    "additive-field-names-convention": "which sibling convention it follows",
    "mixed-convention-is-finding": "a mixed convention inside one route is a finding",
    "consumers-distinguish-absent-null": "because consumers distinguish absent from null",
    "number-states-five-attributes": "states its basis, unit, window, subject and source",
    "none-distinct-from-zero": "`None` stays distinct from `0`",
    "zero-needs-sibling-field": "A zero in an empty state is acceptable only when a sibling field",
    "sibling-field-kinds": "(count, coverage days, insufficient flag, basis label)",
    "honesty-field-must-render": "a computed honesty field nobody renders is a defect",
    "counterfactual-labelled-not-realized": (
        "A counterfactual result (shadow history, if-held, backtest) is labelled and never "
        "presented as realized"
    ),
    "percent-vs-fraction-scale": "returns are percent-scale while Composer drawdown is a fraction",
    "shadow-history-go-live-floor": "begins at the production go-live floor",
    "hash-is-case-sensitive-bot-state-key": "a Composer hash is case sensitive and keys `bot_state`",
    "display-name-keys-advisor-writes": (
        "a normalized display name (strip and lowercase) keys the advisor accept and suggest writes"
    ),
    "resolve-hash-from-bot-state": (
        "needs the hash resolved from `bot_state` through the symphony-entry shape check"
    ),
    "hash-never-normalized": "a hash never goes through `normalize_name`",
    "wrong-key-space-silent": "silently returns zero rows or a Composer 400",
    "long-work-executor-pattern": (
        "follows the single-worker executor plus 202 pattern with a log-and-swallow closure"
    ),
    "duplicate-request-policy": "a stated duplicate-request policy",
    "unread-future-exception-vanishes": (
        "an unread Future exception vanishes and every queued submit runs"
    ),
    "evaluate-routes-not-precedent": "The synchronous evaluate routes are not precedent",
    "status-writes-last-writer-wins": (
        "Status-write routes are last-writer-wins with no transition check"
    ),
    "review-status-derived-counters": "review every counter or cap derived from a status value",
    "accessor-owns-its-connection": (
        "each database accessor opens, commits and closes its own connection"
    ),
    "handler-not-one-transaction": "a handler is not one transaction",
    "verify-ordering-and-partial-failure": (
        "verify the ordering and the partial-failure outcome of every multi-store write"
    ),
    "no-http-inside-open-write": "no outbound HTTP call waits inside an open write",
    "settings-route-no-rollback": (
        "the settings route writes `.env` and then SQLite with no rollback"
    ),
    "ddl-belongs-to-sqlite-specialist": "Migration and WAL design belong to sqlite-specialist",
    "ai-advisor-advisory-only": "AI Advisor surfaces are advisory only",
    "only-route-to-symphony-creation": (
        "The only route that reaches Composer symphony creation is proposal-approve"
    ),
    "approve-creates-undeployed": "it creates an undeployed symphony",
    "panic-sell-only-trade-button": "The panic-sell route is the only dashboard trade button",
    "managed-sleeves-order-path": (
        "Managed Sleeves is the one approved order path with its own arming ladder"
    ),
    "live-trading-two-flags": (
        "Effective live trading is the global `LIVE_EXECUTION` flag AND the symphony's `live_mode`"
    ),
    "is-live-never-default": "`is_live` is never a default",
    "never-settings-writable": "`LIVE_EXECUTION` and credentials are never settings-writable",
    "new-route-named-in-transitive-scan": (
        "must be named in a transitive call-graph scan under `tests/security/`"
    ),
    "dashboard-test-is-single-hop": "`tests/app/test_dashboard_no_order_path.py` is single-hop",
    "engine-no-blocking-io": "the 1-minute engine allows no blocking I/O",
    "engine-files-unchanged": "states `alpha_bot_execution.py` and `math_engine.py` as unchanged",
    "engine-change-exception": "unless the cycle's purpose is an engine change",
    "stray-edit-reaches-live-orders": "a stray edit there reaches live orders",
    "production-data-not-confirmable": (
        "row counts, history depth and live Composer behaviour cannot be confirmed from code"
    ),
    "name-the-settling-query": "name the read-only query that would settle it",
    "unless-pm-live-read": "unless the brief carries the PM's live read",
    "finding-contract-fields": (
        "carries the review contract fields (id, class, trigger, scenario, sibling_search)"
    ),
    "blocking-needs-basis": "`blocking` is true only with a stated basis",
    "pr-gate-reads-blocking": "because the PR gate reads the blocking field",
    "defer-client-mechanics": (
        "defer client retry, timeout and live-flag mechanics to composer-alpaca-integration"
    ),
    "defer-route-implementation": "route implementation to flask-dashboard-specialist",
    "adopt-code-over-older-files": "Adopt the code where either file disagrees with it",
    "older-files-narrower": "those files describe a narrower and older surface",
    "tests-append-n0": "appends `-n0` to the default addopts",
    "never-override-addopts": "never uses `-o addopts=`",
    "addopts-deselection-reason": "drops the live, slow and perf deselection",
    "worker-cap": "never uses `-n auto` or more than 4 workers",
    "git-add-names-files": "Any `git add` you run or recommend names files",
    "never-git-add-all": "`git add -A` is never used",
    "review-dir-not-gitignored": "`.claude/review/` is not gitignored",
    "hidden-qa-scenarios": "hidden qa scenarios",
    "prose-no-dashes-or-spaced-hyphens": (
        "no em dashes, en dashes or spaced hyphens in an artifact or a message"
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
    if keys.get("name") != "api-expert":
        problems.append(f"name is {keys.get('name')!r}, expected 'api-expert'")
    desc = keys.get("description", "")
    if not desc:
        problems.append("description missing")
    elif "Flask" not in desc or "review artifact" not in desc:
        problems.append("description does not name the Flask routes and the review artifact")
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
            "global template ~/.claude/agents/api-expert.md is not installed on this runner"
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
    broken = read_agent().replace("name: api-expert", "name: other-expert", 1)
    assert check_frontmatter(broken) != []


def test_mutation_tools_extra_tool():
    broken = read_agent().replace("TaskList\n", "TaskList, Skill\n", 1)
    assert broken != read_agent()
    assert check_tools(broken) != []


def test_mutation_tools_dropped_tool():
    broken = read_agent().replace("TaskUpdate, TaskList", "TaskList", 1)
    assert broken != read_agent()
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
    nested = tmp_path / "api-expert" / "AGENT.md"
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
    return "---\nname: api-expert\n---\n\n" + str(contract.compiled_template_body(text)) + "\n"


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
            "global template ~/.claude/agents/api-expert.md is not installed on this runner"
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
    broken = read_agent().replace("allow_nan", "allow_other")
    assert check_bindings_supplied(broken) != []


def test_mutation_binding_phrase_only_in_another_binding():
    text = read_agent()
    section = contract.bullet_of(contract.bindings_section(text), "Strict JSON")
    moved = text.replace(section, section.replace("allow_nan", "REMOVED"), 1) + "\nallow_nan\n"
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
        text + "\nAdding an exempt endpoint or an allowlist key is a PM escalation\n",
        BINDING_RULES,
    )
    overlapping = {"a": "Strict JSON", "b": "Strict JSON:** Flask's"}
    assert contract.check_anchor_table(text, overlapping)


def test_mutation_placeholder_added():
    broken = read_agent() + "\n- Findings go to <review-file>.\n"
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
    stale = tmp_path / "api-expert.md"
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
