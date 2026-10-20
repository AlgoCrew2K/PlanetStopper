"""Contract tests for the project behavioral-evidence-capturer agent definition.

Each rule the file must keep is one pure checker returning a list of problems, plus one
mutation control that feeds the checker a deliberately broken variant and asserts it
reports a problem (a check that cannot fail proves nothing). One real child-process case
runs the compile freshness check; it is skipped where the harness scripts are not installed.

The agent file is a compiled specialization: the global template body plus a
``## Project bindings (PlanetStopper)`` section this repository owns. The compiler's own
``--check`` verifies provenance, not body presence, so the template-body checks here are the
only guard against a body that was cut out.

This role drives a browser, so its frontmatter also carries the shared-browser server and the
Playwright tool grant; the tool list is the safety boundary (no typing tools, no tool that ends
the shared browser or hangs it), so it is checked exactly.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.harness import _agent_contract as contract

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "behavioral-evidence-capturer.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "behavioral-evidence-capturer" / "AGENT.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "behavioral-evidence-capturer.md"
BINDING_REF = Path.home() / ".claude" / "agents" / "_refs" / "shared-browser-binding.md"

PARENT_TOOLS = ["Read", "Write", "Glob", "Grep", "Bash", "SendMessage", "TaskUpdate", "TaskList"]
PLAYWRIGHT_PREFIX = "mcp__playwright__"
PLAYWRIGHT_TOOLS = [
    "browser_tabs",
    "browser_navigate",
    "browser_snapshot",
    "browser_take_screenshot",
    "browser_click",
    "browser_hover",
    "browser_select_option",
    "browser_evaluate",
    "browser_wait_for",
    "browser_network_requests",
    "browser_console_messages",
    "browser_handle_dialog",
]
EXPECTED_TOOLS = PARENT_TOOLS + [PLAYWRIGHT_PREFIX + t for t in PLAYWRIGHT_TOOLS]
# Tools that must never be granted: each either ends or hangs the shared browser, or types
# text into the app.
FORBIDDEN_TOOL_NAMES = [
    "browser_close",
    "browser_resize",
    "browser_type",
    "browser_fill_form",
    "browser_press_key",
    "browser_run_code_unsafe",
    "browser_file_upload",
    "browser_drop",
    "browser_drag",
]
FORBIDDEN_PREFIXES = ["mcp__plugin_playwright_playwright__", "mcp__claude-in-chrome__"]
EXPECTED_MODEL = "haiku"
EXPECTED_EFFORT = "low"
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# behavioral-evidence-capturer"
DIRECTIVE_MARKER = "**Prime Directive:**"

# The server definition the shared-browser binding requires, as it must appear in frontmatter.
MUX_REQUIRE = "require(require('path').join(require('os').homedir(),'.claude','scripts','browser-mcp-mux.js'))"
SERVER_ARGS = [
    "@playwright/mcp@0.0.75",
    "--browser",
    "firefox",
    "--endpoint",
    "harness-shared",
]

# One entry per binding the project must supply: (label, substrings that must all appear in
# that binding's own bullet).
REQUIRED_BINDINGS = [
    (
        "Browser server and engine",
        [
            "mcp__playwright__browser_",
            "multiplexer",
            "mcp__plugin_playwright_playwright",
            "mcp__claude-in-chrome",
            "Preview MCP",
            "browser_tabs new",
            "browser_tabs close",
            "browser identity (checked by the harness)",
            "browser identity check failed",
        ],
    ),
    (
        "Tools added to the parent's set",
        [
            "browser_tabs",
            "browser_evaluate",
            "browser_select_option",
            "browser_snapshot",
            "browser_close",
            "browser_resize",
            "browser_type",
            "browser_fill_form",
            "browser_press_key",
        ],
    ),
    ("Capture instance", ["5099", "127.0.0.1", "launcher", "copy of the database", "no scheduler"]),
    ("No process control", ["python app.py", "restart.ps1", "browser-shared", "curl"]),
    (
        "Never-click list",
        [
            "cash-now-btn",
            "#emergency-liquidate",
            "#panic-confirm-btn",
            "#live-toggle",
            "#sym-confirm-go-live",
            "#btn-save",
            "#btn-flush-confirm",
            "force-run-btn",
            "sleeve-arm-control",
            "retirement-approve-btn",
            "fr-approve-btn",
            "fr-run-btn",
            "#get-suggestions-btn",
            "#candidate-alert-indicator",
            "#fleet-dismiss-btn",
            "BLOCKED",
        ],
    ),
    (
        "No-trade boundary",
        [
            "AI Advisor",
            "proposal-approve",
            "panic-sell",
            "LIVE_EXECUTION",
            "live_mode",
        ],
    ),
    ("Dialogs", ["browser_handle_dialog", "accept", "never accept"]),
    ("Stop conditions", ["mode-pill", "data-live", "/login", "503", "Never sign in"]),
    (
        "Viewport and theme",
        [
            "1440x900",
            "1400 px",
            "browser-shared",
            "window.innerWidth",
            "exit 9",
            "exit 10",
            "window.studioTweaks.set",
            "data-theme",
            "setAttribute",
        ],
    ),
    ("Waiting", ["browser_wait_for", "network idle", "ET clock"]),
    ("Network and console", ["POST", "console"]),
    ("Stored tweaks", ["localStorage['studioTweaks']", "write the exact value back"]),
    ("Chart and font presence", ["typeof Chart", "document.fonts.check", "CDNs"]),
    ("Unreachable data states", ["BLOCKED", "read-only"]),
    (
        "Instance honesty",
        ["Observations", "`None`", "structural evidence", "unconfirmed", "post_mortems"],
    ),
    (
        "Ground truth and sources",
        ["git rev-parse HEAD", "git status -sb", ".claude/CLAUDE.md", "DECISIONS.md", "line drift"],
    ),
    ("Evidence files", [".claude/review/", "12 characters", "/evidence/", "UUID"]),
    (
        "Staging and test runs",
        ["git add -A", "gitignored", "qa scenarios", "-n0", "-o addopts=", "-n auto", "4 workers"],
    ),
    ("Prose", ["em dashes", "en dashes", "spaced hyphens", "invented names"]),
]

# One anchor per distinct rule in the bindings section. Each anchor occurs exactly once in the
# bindings, and removing it alone must make the rule check report exactly that rule.
BINDING_RULES: dict[str, str] = {
    "only-browser-tools-are-shared-server": (
        "the only browser tools are the `mcp__playwright__browser_*` tools of the `playwright` "
        "server declared in the frontmatter"
    ),
    "shared-firefox-through-multiplexer": (
        "attaches to the harness's shared Firefox through the browser multiplexer"
    ),
    "harness-verifies-engine-and-tabs": (
        "because the harness verifies the engine and tracks tab ownership for that server alone"
    ),
    "banned-mcps": (
        "The plugin Playwright server (`mcp__plugin_playwright_playwright`), "
        "`mcp__claude-in-chrome` and any Preview MCP are banned"
    ),
    "banned-mcps-reason": (
        "because they cannot share one agent with it and bypass the tab guard and the engine check"
    ),
    "open-own-tab-first": "Open your own tab first with `browser_tabs new`",
    "close-own-tab-last": "close it last with `browser_tabs close`",
    "quote-harness-identity-line": (
        "Quote the `browser identity (checked by the harness)` line from that first result "
        "as the report's UA line"
    ),
    "typed-user-agent-not-evidence": ("because a user agent typed by the model is not evidence"),
    "identity-check-failure-hard-stop": (
        "A refusal beginning `browser identity check failed` is a hard stop, never retried"
    ),
    "tool-additions-listed": (
        "**Tools added to the parent's set:** `browser_tabs` opens and closes your own tab"
    ),
    "step-and-evidence-tools": ("run the checklist steps and record the evidence"),
    "evaluate-runs-probes-and-storage": (
        "`browser_evaluate` runs the verbatim probes and the storage reads"
    ),
    "select-option-existing-only": (
        "`browser_select_option` picks an existing option of a select the checklist names"
    ),
    "snapshot-locates-only": ("`browser_snapshot` only locates an element by role or name"),
    "close-left-out": (
        "`browser_close` is left out because on Firefox it ends the shared browser for every agent"
    ),
    "resize-left-out": "`browser_resize` is left out because it hangs on Firefox",
    "typing-tools-left-out": (
        "`browser_type`, `browser_fill_form` and `browser_press_key` are left out because you "
        "never enter text into the app"
    ),
    "capture-only-against-brief-url": ("capture only against the base URL the brief supplies"),
    "safe-launcher-shape": (
        "a safe launcher instance that imports the app on a copy of the database with no "
        "scheduler and authentication bypassed by a flag, on port 5099"
    ),
    "literal-loopback-address": (
        "write it as `127.0.0.1` literally because `localhost` can resolve to IPv6 while the "
        "server binds IPv4"
    ),
    "no-launcher-url-means-ask": (
        "A brief that names no launcher URL gets a stop and a question to the requester"
    ),
    "never-start-instance-yourself": ("because starting an instance yourself is forbidden"),
    "never-python-app": (
        "never run `python app.py`, because it starts the 1-minute engine scheduler with the "
        "real credentials"
    ),
    "never-restart-ps1": (
        "never run `restart.ps1`, because it stops every process whose command line matches "
        "`app.py` and so kills the operator's daemon"
    ),
    "never-control-processes": "Never start, stop or restart any process",
    "bash-scope": (
        "Bash is for read-only git, the `browser-shared` viewport helper and GET requests"
    ),
    "curl-post-is-a-write": "because a `curl` POST is the same write as a click",
    "never-click-list-intro": (
        "never click or open these controls, because each is a write, a metered Anthropic or "
        "Composer call, or a Discord alert"
    ),
    "never-click-cash-now": 'Cash Now (`[data-testid="cash-now-btn"]`)',
    "never-click-liquidation": (
        "the liquidation controls (`#emergency-liquidate`, `#panic-phrase`, `#panic-confirm-btn`)"
    ),
    "never-click-live-toggles": (
        "the LIVE toggles (`#live-toggle`, the symphony settings modal toggle and "
        "`#sym-confirm-go-live`)"
    ),
    "never-click-settings-save-flush": (
        "settings save and flush (`#btn-save`, `#btn-flush-trigger`, `#btn-flush-confirm`)"
    ),
    "never-click-force-run": 'force run (`[data-testid="force-run-btn"]`)',
    "never-click-sleeve-controls": (
        "the sleeve controls (`sleeve-arm-control`, `sleeve-disarm-control`, "
        "`sleeve-delete-control`)"
    ),
    "never-click-retirement-frontrunner": (
        "the retirement and Frontrunner buttons (`retirement-approve-btn`, "
        "`retirement-reject-btn`, `fr-approve-btn`, `fr-reject-btn`, `fr-run-btn`)"
    ),
    "never-click-suggestions-and-runs": (
        "`#get-suggestions-btn`, the Strategy Builder run, the Evaluate buttons, chat send"
    ),
    "never-click-alert-indicator-and-dismiss": (
        "`#candidate-alert-indicator` and `#fleet-dismiss-btn`"
    ),
    "named-step-is-blocked": (
        "A step that names one is `BLOCKED` with the step and the reason, never performed"
    ),
    "safe-reads-list": (
        "Tab buttons, window-selector buttons, `details` summaries, the Tweaks panel and the "
        "workspace switcher are reads"
    ),
    "advisor-and-route-boundary": (
        "AI Advisor surfaces are advisory only, the proposal-approve route is the only path to "
        "Composer symphony creation, and the panic-sell route is the only dashboard trade button"
    ),
    "live-trading-two-flags": (
        "Effective live trading is the global `LIVE_EXECUTION` flag AND the symphony's `live_mode`"
    ),
    "drive-none-of-those-routes": (
        "You drive none of those routes, so a state that needs one is reported `BLOCKED`"
    ),
    "dismiss-dialogs": (
        "dismiss a dialog with `browser_handle_dialog` and `accept` false, never accept one"
    ),
    "accepting-executes-the-write": (
        "because the app puts a native confirm in front of its writes and accepting executes "
        "the write that follows"
    ),
    "pending-dialog-before-click-is-stop": (
        "A dialog already pending before a click is a stop, because it wedges every later page call"
    ),
    "stop-conditions-list": (
        "stop and report the reason line, without continuing, when the mode pill "
        '(`[data-testid="mode-pill"]`) reads LIVE or carries `data-live="true"`'
    ),
    "stop-on-login-503-no-browser": (
        "when a page lands on `/login`, when a response is the 503 misconfigured page, or when "
        "the shared browser is not running"
    ),
    "never-sign-in": (
        "Never sign in inside the tab, because the launcher's authentication bypass failed"
    ),
    "credential-not-in-evidence-run": "a credential does not belong in an evidence run",
    "read-pill-and-path-every-item": "Read the pill and the page path on every item",
    "default-viewport-and-theme": (
        "unless an item says otherwise, use 1440x900 and the light theme"
    ),
    "header-breakpoint-reason": (
        "because 1400 px is the header breakpoint below which the ET clock and workspace "
        "switcher disappear"
    ),
    "viewport-helper": (
        "Set a viewport with the harness `browser-shared` helper's `viewport` subcommand "
        "(tab index, exact tab URL, width, height)"
    ),
    "viewport-confirmation": "confirm it with `browser_evaluate` on `window.innerWidth`",
    "viewport-exit-codes": (
        "exit 9 means an open dialog or a busy page, and exit 10 means a wedged tab, so open a "
        "new tab"
    ),
    "theme-through-app-api": (
        "Set a theme with `window.studioTweaks.set('theme', ...)` in `browser_evaluate`"
    ),
    "theme-reload-and-wait": (
        "then reload and wait until `document.documentElement.getAttribute('data-theme')` "
        "equals the theme and the target selector exists"
    ),
    "theme-attribute-empty-until-ready": (
        "because the attribute is empty until `DOMContentLoaded` and the charts read their "
        "colors once"
    ),
    "never-set-theme-attribute": "Never set `data-theme` with `setAttribute`",
    "attribute-and-storage-disagree": (
        "because the stored state and the charts then disagree with the attribute"
    ),
    "wait-on-selector-not-network-idle": (
        "wait on a selector or text with `browser_wait_for`, never on network idle"
    ),
    "streams-and-polls-keep-network-busy": (
        "because an open event stream and 30-second polls keep the network busy"
    ),
    "clock-differences-are-observations": (
        "Two screenshots of one state differ in the ET clock and the NEXT countdown, so report "
        "that difference as an observation and not as a failure"
    ),
    "record-post-status-and-console": (
        "record the status of every POST the page issues on its own, and the console output, "
        "per item"
    ),
    "ui-reports-success-regardless": (
        "because the UI reports success on a resolved fetch whatever its status"
    ),
    "record-found-tweaks-first": (
        "read `localStorage['studioTweaks']` with `browser_evaluate` before the first item"
    ),
    "restore-tweaks-last": "write the exact value back last",
    "end-on-defaults": "then end on the default viewport, theme and route",
    "profile-storage-persists": (
        "because the shared browser profile keeps storage across agents and sessions"
    ),
    "probe-chart-and-fonts": (
        "probe `typeof Chart` and `document.fonts.check('12px Manrope')` once per page and "
        "report the raw results"
    ),
    "cdn-degrades-silently": (
        "because Chart.js and the fonts load from CDNs and degrade silently when offline"
    ),
    "absent-state-is-blocked": (
        "report a state the instance's data does not hold as `BLOCKED` with the missing state, "
        "never create it"
    ),
    "writes-are-not-yours": (
        "because you are read-only and Approve, Reject, Run and Save are writes"
    ),
    "observations-open-with-instance-line": (
        "open the Observations list with one line naming the instance base URL, checkout and "
        "database copy the brief gave"
    ),
    "figures-verbatim-none-vs-zero": (
        "report on-screen figures verbatim, with `None`, a dash, `n/a`, blank and `0` kept as "
        "different observations"
    ),
    "counterfactual-label-kept": (
        "A figure the screen labels counterfactual (shadow history, if-held, backtest) is "
        "reported with that label and never described as realized"
    ),
    "counterfactual-label-reason": "because an unlabeled hypothetical reads as a real result",
    "db-copy-values-structural-only": (
        "Dollar and percent values from a database copy are structural evidence only"
    ),
    "db-copy-reasons": (
        "because the scheduler-fed account cache is empty and the `post_mortems` folder is local"
    ),
    "production-facts-unconfirmed": (
        "Production counts and live Composer behaviour cannot be confirmed from the code or "
        "from the copy, so mark them unconfirmed"
    ),
    "preamble-commands": (
        "report the branch tip from `git rev-parse HEAD` and the working tree from `git status -sb`"
    ),
    "preamble-in-named-checkout": (
        "both run this turn in the checkout the brief names, beside the instance line"
    ),
    "instance-may-serve-another-tree": (
        "because a running instance can serve a different tree than the one HEAD names"
    ),
    "large-files-never-read-whole": (
        "Never read `.claude/CLAUDE.md` (about 67k tokens) or `DECISIONS.md` (about 11k lines, "
        "append-only) whole"
    ),
    "never-copy-path-line": "never copy a `path:line` from them",
    "line-drift-reason": (
        "because the checklist already carries every selector and line drift in this repository "
        "is large"
    ),
    "evidence-directory-from-brief": "save screenshots to the directory the brief names",
    "evidence-default-directory": (
        "when it names none, use `.claude/review/` plus the first 12 characters of HEAD plus "
        "`/evidence/` in the checkout the brief names"
    ),
    "evidence-file-name-parts": (
        "with the item id, viewport, theme and `full` or `close` in each file name"
    ),
    "unwritable-directory-fallback": (
        "If the MCP cannot write there, report it and write where it can, naming the path"
    ),
    "paths-only-in-messages": "Messages carry the paths only, never the images",
    "images-show-balances": ("because they show real balances and account UUID fragments"),
    "commit-and-stage-nothing": "you commit and stage nothing",
    "stage-by-name": (
        "if a stage is ever asked of you, stage files by name and never `git add -A`"
    ),
    "review-folder-not-gitignored": (
        "because `.claude/review/` is not gitignored and a broad add would commit the evidence "
        "PNGs and the review artifacts, including the hidden qa scenarios"
    ),
    "test-runs-append-n0": ("If a test run is ever requested, append `-n0` to the default addopts"),
    "never-override-addopts": (
        "never pass `-o addopts=`, because that also drops the live, slow and perf deselection"
    ),
    "worker-cap": "never use `-n auto` or more than 4 workers",
    "prose-no-dashes-or-spaced-hyphens": (
        "no em dashes, en dashes or spaced hyphens in a report or any message"
    ),
    "invented-names-only": "invented names only in examples",
    "real-name-copied-into-code": (
        "because a real name in an example is copied into code and commits"
    ),
    "production-facts-name-settling-query": "name the read-only query that would settle each",
}


def read_agent() -> str:
    return contract.read_lf(AGENT_FILE)


def split_frontmatter(text: str) -> dict[str, str | list[str]]:
    return contract.split_frontmatter(text)[0]


def tool_list(text: str) -> list[str]:
    keys = split_frontmatter(text)
    return [t.strip() for t in str(keys.get("tools", "")).split(",") if t.strip()]


# ---- checkers: each returns a list of problems, empty when the rule holds ----


def check_frontmatter(text: str) -> list[str]:
    try:
        keys = split_frontmatter(text)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    if keys.get("name") != "behavioral-evidence-capturer":
        problems.append(f"name is {keys.get('name')!r}, expected 'behavioral-evidence-capturer'")
    desc = keys.get("description", "")
    if not desc:
        problems.append("description missing")
    elif "ux-expert" not in desc or "never clicks" not in desc:
        problems.append("description does not name the paired ux-expert and the never-click rule")
    return problems


def check_tools(text: str) -> list[str]:
    tools = tool_list(text)
    if tools != EXPECTED_TOOLS:
        return [f"tools {tools} differ from the designed grant {EXPECTED_TOOLS}"]
    return []


def check_no_forbidden_tools(text: str) -> list[str]:
    """No tool that types text, ends or hangs the shared browser, or comes from a banned server."""
    problems = []
    for tool in tool_list(text):
        name = tool.rsplit("__", 1)[-1]
        if name in FORBIDDEN_TOOL_NAMES:
            problems.append(f"forbidden tool granted: {tool}")
        if any(tool.startswith(prefix) for prefix in FORBIDDEN_PREFIXES):
            problems.append(f"tool from a banned server granted: {tool}")
    return problems


def check_model(text: str) -> list[str]:
    keys = split_frontmatter(text)
    problems = []
    if keys.get("model") != EXPECTED_MODEL:
        problems.append(f"model is {keys.get('model')!r}, expected {EXPECTED_MODEL!r}")
    if keys.get("effort") != EXPECTED_EFFORT:
        problems.append(f"effort is {keys.get('effort')!r}, expected {EXPECTED_EFFORT!r}")
    return problems


def check_mcp_server(text: str) -> list[str]:
    """The frontmatter declares the shared-browser server behind the multiplexer, with the
    endpoint in place of --isolated, as the shared-browser binding requires."""
    keys = split_frontmatter(text)
    block = contract.frontmatter_text(text)
    problems = []
    if keys.get("mcpServers") != ["playwright:"]:
        problems.append(f"mcpServers is {keys.get('mcpServers')!r}, expected one playwright server")
    if "type: stdio" not in block or "command: node" not in block:
        problems.append("server is not a node stdio command")
    if MUX_REQUIRE not in block:
        problems.append("server does not start through browser-mcp-mux.js")
    for arg in SERVER_ARGS:
        if f'"{arg}"' not in block:
            problems.append(f"server args lack {arg!r}")
    if "--isolated" in block:
        problems.append("server uses --isolated; it must attach to the shared browser")
    return problems


def check_no_unrequested_keys(text: str) -> list[str]:
    keys = split_frontmatter(text)
    return [f"unrequested key {k!r}" for k in ("memory", "skills", "experimental") if k in keys]


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


def test_tools_match_design():
    assert check_tools(read_agent()) == []


def test_no_forbidden_tool_granted():
    assert check_no_forbidden_tools(read_agent()) == []


def test_model_and_effort_follow_template():
    assert check_model(read_agent()) == []


def test_pins_record_present():
    assert contract.check_pins_record(read_agent()) == []


def test_mcp_server_declared_behind_multiplexer():
    assert check_mcp_server(read_agent()) == []


def test_mcp_server_matches_shared_binding_reference():
    if not BINDING_REF.is_file():
        pytest.skip("shared-browser-binding.md is not installed on this runner")
    reference = contract.read_lf(BINDING_REF)
    block = contract.frontmatter_text(read_agent())
    declared = next(line for line in block.splitlines() if line.strip().startswith("args:"))
    assert declared.strip().removeprefix("args: ") in reference


def test_no_memory_skills_or_experimental_keys():
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
            "global template ~/.claude/agents/behavioral-evidence-capturer.md is not installed "
            "on this runner"
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
    broken = read_agent().replace("name: behavioral-evidence-capturer", "name: other-capturer", 1)
    assert check_frontmatter(broken) != []


def test_mutation_tools_extra_tool():
    broken = read_agent().replace(
        "mcp__playwright__browser_handle_dialog\n",
        "mcp__playwright__browser_handle_dialog, Skill\n",
        1,
    )
    assert check_tools(broken) != []


def test_mutation_tools_dropped_tool():
    broken = read_agent().replace("mcp__playwright__browser_tabs, ", "", 1)
    assert check_tools(broken) != []


@pytest.mark.parametrize("name", FORBIDDEN_TOOL_NAMES)
def test_mutation_forbidden_tool_detected(name):
    broken = read_agent().replace(
        "mcp__playwright__browser_handle_dialog\n",
        f"mcp__playwright__browser_handle_dialog, mcp__playwright__{name}\n",
        1,
    )
    assert broken != read_agent()
    assert check_no_forbidden_tools(broken) != []


@pytest.mark.parametrize("prefix", FORBIDDEN_PREFIXES)
def test_mutation_banned_server_tool_detected(prefix):
    broken = read_agent().replace("mcp__playwright__browser_tabs", f"{prefix}browser_tabs", 1)
    assert broken != read_agent()
    assert check_no_forbidden_tools(broken) != []


def test_mutation_model_changed():
    broken = read_agent().replace("model: haiku", "model: sonnet", 1)
    assert check_model(broken) != []


def test_mutation_effort_changed():
    broken = read_agent().replace("effort: low", "effort: high", 1)
    assert check_model(broken) != []


def test_mutation_memory_key_added():
    broken = read_agent().replace("model: haiku", "model: haiku\nmemory: project", 1)
    assert check_no_unrequested_keys(broken) != []


def test_mutation_mcp_server_removed():
    text = read_agent()
    start = text.index("mcpServers:")
    end = text.index("model: haiku")
    broken = text[:start] + text[end:]
    assert check_mcp_server(broken) != []


def test_mutation_mcp_server_isolated_variant():
    broken = read_agent().replace('"--endpoint", "harness-shared"', '"--isolated"', 1)
    problems = check_mcp_server(broken)
    assert problems != []
    assert any("--isolated" in p for p in problems)


def test_mutation_mcp_server_bypasses_multiplexer():
    broken = read_agent().replace("browser-mcp-mux.js", "other.js", 1)
    assert check_mcp_server(broken) != []


def test_mutation_mcp_server_unpinned_package():
    broken = read_agent().replace("@playwright/mcp@0.0.75", "@playwright/mcp", 1)
    assert check_mcp_server(broken) != []


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
    nested = tmp_path / "behavioral-evidence-capturer" / "AGENT.md"
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
    return (
        "---\nname: behavioral-evidence-capturer\n---\n\n"
        + str(contract.compiled_template_body(text))
        + "\n"
    )


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
            "global template ~/.claude/agents/behavioral-evidence-capturer.md is not installed "
            "on this runner"
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
    broken = read_agent().replace("restart.ps1", "restart.sh")
    assert check_bindings_supplied(broken) != []


def test_mutation_binding_phrase_only_in_another_binding():
    text = read_agent()
    section = contract.bullet_of(contract.bindings_section(text), "Dialogs")
    moved = (
        text.replace(section, section.replace("never accept", "REMOVED"), 1) + "\nnever accept\n"
    )
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
    assert contract.check_anchor_table(text + "\nyou commit and stage nothing\n", BINDING_RULES)
    overlapping = {"a": "Read the pill", "b": "Read the pill and the page path"}
    assert contract.check_anchor_table(text, overlapping)


def test_mutation_placeholder_added():
    broken = read_agent() + "\n- Capture <route> next.\n"
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
    broken = read_agent().replace("effort: low", "effort: low\nnote: decided 2026-01-02", 1)
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
    stale = tmp_path / "behavioral-evidence-capturer.md"
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

REQUIRED_RULES_EXEMPT: frozenset[str] = contract.ENGINE_RULE_NAMES


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
