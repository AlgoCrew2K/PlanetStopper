"""Contract tests for the project ux-expert agent definition.

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
AGENT_FILE = REPO_ROOT / ".claude" / "agents" / "ux-expert.md"
NESTED_LAYOUT = REPO_ROOT / ".claude" / "agents" / "ux-expert" / "AGENT.md"
COMPILER = Path.home() / ".claude" / "scripts" / "compile-agent-specialization.py"
TEMPLATE_FILE = Path.home() / ".claude" / "agents" / "ux-expert.md"

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
BINDINGS_HEADING = "## Project bindings (PlanetStopper)"
TEMPLATE_TITLE = "# ux-expert"
DIRECTIVE_MARKER = "**Prime Directive:**"

# One entry per binding the project must supply: (label, substrings that must all appear in
# that binding's own bullet).
REQUIRED_BINDINGS = [
    ("Ground truth", ["git rev-parse HEAD", "git status -sb", "git show", "line drift"]),
    ("Large guidance files", [".claude/CLAUDE.md", "DECISIONS.md", "append-only"]),
    (
        "Design system",
        ["static/tokens.css", '[data-theme="dark"]', "data-density", "--studio-surface"],
    ),
    ("Density", [".studio-fluid-grid", "density tokens"]),
    ("Sibling set", ["templates/ai_advisor.html", "property by property"]),
    ("Capture set", ["1440x900", "375", ".studio-nav-wide"]),
    ("Contrast bar", ["WCAG AA", "4.5:1", "3:1", "--studio-ink-faint"]),
    ("Chip tints", ["color-mix", "--studio-pos", "--studio-neg-tint", "raw rgba"]),
    ("Accent override", ["--studio-accent", "inline", "computed"]),
    ("Theme steps", ["window.studioTweaks.set", "reloads", "data-theme", "DOMContentLoaded"]),
    ("Design references", [".design-handoff/screenshots/", "light", "absent", "never invent"]),
    ("Assertable rows", ["Flask test client", "source text", "layout-only", "RED row"]),
    ("Data states", ["data state", "UNVERIFIED", "capturer cannot create"]),
    ("Honest data on screen", ["`None`", "`0`", "basis and window", "counterfactual"]),
    ("Production numbers", ["structural evidence", "read-only query"]),
    ("Capture launcher", ["safe capture launcher", "operator's daemon", "blocker"]),
    ("Mode pill", ["mode-pill", "a LIVE pill", "a 503"]),
    ("Probes and controls", ["side-effect-free", "non-GET", "Cash Now", "seeded instance"]),
    ("Selectors", ["tab-overview", "strategy-builder-tab", "one action per step"]),
    ("Pre-existing deviations", ["both citations", "diff touched it"]),
    (
        "No-trade boundary on screen",
        ["AI Advisor", "proposal-approve", "panic-sell", "Managed Sleeves"],
    ),
    ("Staging", ["git add -A", ".claude/review/"]),
    ("Test runs", ["-n0", "-o addopts=", "-n auto", "4 workers"]),
    ("Prose", ["em dashes", "en dashes", "spaced hyphens", "invented names"]),
]

# One anchor per distinct rule in the bindings section. Each anchor occurs exactly once in the
# bindings, and removing it alone must make the rule check report exactly that rule. The label
# anchors keep a deleted label sentence from passing unnoticed.
BINDING_RULES: dict[str, str] = {
    "label-ground-truth": "**Ground truth.**",
    "ground-truth-is-code-at-ref": "The ground truth is the code at the brief's ref",
    "preamble-commands": "Run `git rev-parse HEAD` and `git status -sb` first",
    "git-show-at-ref": "read with `git show REF:path/to/file`",
    "never-copy-path-line": "Never copy a `path:line` from",
    "line-drift-refind-symbol": "line drift in this repository is large",
    "label-large-guidance-files": "**Large guidance files.**",
    "guidance-file-size": "(about 67k tokens)",
    "grep-by-symbol-and-decision-id": "grepped by symbol and decision id and never read whole",
    "decisions-append-only": "`DECISIONS.md` is append-only, so a correction arrives in a later entry",
    "first-hit-not-last-word": "the first hit is never the last word",
    "label-design-system": "**Design system.**",
    "design-system-is-token-set": "The design system is the studio token set in `static/tokens.css`",
    "no-component-library": "there is no component library",
    "light-and-dark-blocks": (
        'Light values sit in the `:root` block and dark values in the `[data-theme="dark"]` block'
    ),
    "tweaks-sets-theme-and-density-attributes": (
        "sets the `data-theme` and `data-density` attributes on the html element"
    ),
    "compare-resolved-values": (
        "Resolve every value for the theme under review and compare resolved values, "
        "never token names"
    ),
    "aliases-resolve-to-same-value": (
        "aliases such as `--studio-surface` and `--studio-border` that resolve to the same value"
    ),
    "label-density": "**Density.**",
    "density-single-consumer": "Only `.studio-fluid-grid` consumes the density tokens",
    "density-item-needs-probe": "is dropped when no consumer exists",
    "label-sibling-set": "**Sibling set.**",
    "judge-against-advisor-siblings": (
        "Judge new UI against the sibling advisor panels and cards in `templates/ai_advisor.html`"
    ),
    "compare-property-by-property": "comparing property by property",
    "sibling-from-same-page-family": (
        "the sibling comes from the same page family as the surface under review"
    ),
    "label-capture-set": "**Capture set.**",
    "capture-set-sizes": "The capture set is 1440x900 in light and dark plus 375 wide in light",
    "header-hidden-at-1400": "`.studio-nav-wide` is hidden at 1400 px and below",
    "different-set-flagged": "A different set is flagged to the PM",
    "label-contrast-bar": "**Contrast bar.**",
    "wcag-aa-bar": "WCAG AA contrast is the bar",
    "contrast-thresholds": "4.5:1 for body text and 3:1 for large text and UI parts",
    "composite-translucent-ink": (
        "composite the translucent ink tokens over the paper token yourself"
    ),
    "ink-tokens-are-rgba": "are rgba values and the ratio depends on what lies beneath",
    "label-chip-tints": "**Chip tints.**",
    "chips-tint-through-tokens": (
        "tint through tokens such as `color-mix` over `--studio-pos` and `--studio-neg` or the "
        "`--studio-neg-tint` token, never raw rgba literals"
    ),
    "literal-ignores-dark-theme": "because a literal does not follow the dark theme",
    "existing-literals-exist": "Raw rgba tints exist in `templates/ai_advisor.html`",
    "copied-literal-is-row-existing-is-decision": (
        "a literal the diff did not write goes to the PM"
    ),
    "label-accent-override": "**Accent override.**",
    "tweaks-writes-accent-inline": "writes `--studio-accent` inline on the html element",
    "inline-beats-dark-rule": "an inline value beats the dark theme rule",
    "probe-computed-and-inline-accent": (
        "Ask for both the computed accent and the inline accent in each theme and judge the "
        "computed value"
    ),
    "label-theme-steps": "**Theme steps.**",
    "set-theme-with-api-and-reload": (
        "sets the theme with `window.studioTweaks.set` and then reloads the page"
    ),
    "wait-for-stored-theme-and-selector": (
        "waits for the stored theme on the `data-theme` attribute and for the target selector"
    ),
    "attribute-empty-until-dom-ready": "The attribute is empty until DOMContentLoaded",
    "charts-draw-colors-once": "charts draw their colors once from tokens",
    "direct-attribute-set-desyncs": (
        "setting the attribute directly leaves stored state and charts disagreeing with it"
    ),
    "label-design-references": "**Design references.**",
    "references-light-only-partly-gitignored": "light theme only and partly gitignored",
    "screenshots-folder-absent-from-worktree": (
        "`.design-handoff/screenshots/` is gitignored and absent from a worktree"
    ),
    "committed-advisor-screenshots-light-only": (
        "`.design-handoff/advisor/screenshots/` are light only"
    ),
    "name-absent-reference-as-absent": "name an absent reference as absent",
    "never-invent-reference": (
        "never invent a reference for dark theme, the settings page or newer panels"
    ),
    "label-assertable-rows": "**Assertable rows.**",
    "suite-asserts-html-source-and-node-stub-dom": (
        "The default test suite asserts server-rendered HTML through the Flask test client, "
        "source text, and the output of one extracted JavaScript function run under node "
        "against a stub DOM"
    ),
    "node-tests-skip-without-node": "they skip where node is absent",
    "row-names-extractable-function-value": (
        "the value a single extractable function renders from a given payload"
    ),
    "whole-page-criterion-is-judgment": "A criterion about a whole rendered page",
    "only-a-capture-can-show-it": "because only a capture can show it",
    "no-browser-runner": "with no browser runner",
    "row-names-assertable-thing": "A row names a token or class used",
    "layout-only-is-judgment": "is a judgment finding to the PM and never a RED row",
    "label-data-states": "**Data states.**",
    "item-names-data-state-and-instance": (
        "Every checklist item names the data state it needs and the instance that holds it"
    ),
    "variants-depend-on-database-rows": "depend on database rows the capturer cannot create",
    "unheld-state-is-unverified": "is UNVERIFIED and is never judged from code",
    "label-honest-data": "**Honest data on screen.**",
    "none-renders-as-dash-not-zero": "`None` renders as a dash or `n/a` and never `0`",
    "figure-shows-basis-and-window": "each figure shows its basis and window",
    "counterfactual-labelled-not-realized": (
        "counterfactual figures (shadow history, if-held, backtest) are labelled and never "
        "shown as realized"
    ),
    "fabricated-zero-repeat-defect": "A fabricated zero or an unlabeled basis is a repeat defect class",
    "label-production-numbers": "**Production numbers.**",
    "db-copy-is-structural-only": ("database-copy capture instance are structural evidence only"),
    "db-copy-differs-from-production": (
        "scheduler-fed account cache and the local post_mortems folder differ from production"
    ),
    "production-data-not-confirmable": (
        "Counts, history depth and live Composer behaviour cannot be confirmed from code"
    ),
    "unconfirmed-with-settling-query": "with the settling read-only query",
    "label-capture-launcher": "**Capture launcher.**",
    "safe-launcher-only": "works only against the PM's safe capture launcher",
    "never-operator-daemon": "never against the operator's daemon",
    "starting-app-runs-scheduler": "Starting the app runs the minute scheduler with real credentials",
    "restart-kills-daemon": "restarting it kills the operator's daemon",
    "missing-launcher-url-is-blocker": "a missing launcher URL is a blocker to the PM",
    "label-mode-pill": "**Mode pill.**",
    "every-item-reads-mode-pill": ('Every item reads the mode pill (`data-testid="mode-pill"`)'),
    "live-login-503-stop-round": "a LIVE pill, a login page or a 503 stops the round",
    "label-probes-and-controls": "**Probes and controls.**",
    "side-effect-free-probes": "Use only side-effect-free probes",
    "no-page-function-or-non-get": (
        "never call a page function, send a non-GET fetch or dispatch an event"
    ),
    "never-click-write-controls": "A checklist step never clicks a control that writes or spends",
    "never-click-list": "Cash Now, panic liquidate, force run, sleeve arm, disarm and delete",
    "write-metered-side-effect-reason": (
        "each is a write, a metered call or a Composer or Discord side effect"
    ),
    "ask-pm-for-seeded-instance": "Ask the PM for a seeded instance instead",
    "label-selectors": "**Selectors.**",
    "exact-selectors-from-templates": "Use exact selectors read from the templates",
    "inconsistent-tab-testids": "`tab-overview` and `strategy-builder-tab`",
    "one-action-per-step": "one action per step with a checkpoint",
    "active-tab-panel-only": (
        "Only the active advisor tab panel is displayed, so a step clicks the tab first"
    ),
    "label-pre-existing-deviations": "**Pre-existing deviations.**",
    "report-conflicts-with-both-citations": (
        "Report documented-versus-shipped conflicts to the PM as decisions with both citations"
    ),
    "not-a-row-unless-diff-touched": "do not raise one as a row unless the diff touched it",
    "named-conflicts": (
        "The single-typeface note, the Title Case section label rule and the template "
        "inheritance note"
    ),
    "label-no-trade-boundary": "**No-trade boundary on screen.**",
    "ai-advisor-advisory-only": "AI Advisor surfaces are advisory only",
    "only-route-to-symphony-creation": (
        "only the proposal-approve route reaches Composer symphony creation"
    ),
    "approve-creates-undeployed": "and it creates an undeployed symphony",
    "panic-sell-only-trade-button": "the panic-sell route is the only dashboard trade button",
    "managed-sleeves-order-path": "Managed Sleeves is the one approved order path",
    "trade-looking-control-checked": (
        "A new control that reads as a trade or an approval is checked against this boundary"
    ),
    "label-staging": "**Staging.**",
    "stage-by-name": "Stage files by name and never `git add -A`",
    "review-folder-not-gitignored": "`.claude/review/` is not gitignored",
    "label-test-runs": "**Test runs.**",
    "append-n0-to-addopts": "appends `-n0` to the default addopts",
    "never-replace-addopts": "never replaces them with `-o addopts=`",
    "never-auto-or-over-four-workers": "never uses `-n auto` or more than 4 workers",
    "full-suite-memory-reason": "the full suite can exhaust memory",
    "label-prose": "**Prose.**",
    "prose-no-dashes-or-spaced-hyphens": (
        "Use no em dashes, en dashes or spaced hyphens in any row or message"
    ),
    "examples-use-invented-names": "use invented names only in examples",
    "real-name-copied-into-code": "a real name in an example is copied into code and commits",
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
    if keys.get("name") != "ux-expert":
        problems.append(f"name is {keys.get('name')!r}, expected 'ux-expert'")
    desc = keys.get("description", "")
    if not desc:
        problems.append("description missing")
    elif "PlanetStopper" not in desc or "capturer" not in desc:
        problems.append("description does not name the project and the capturer it pairs with")
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
        pytest.skip("global template ~/.claude/agents/ux-expert.md is not installed on this runner")
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
    broken = read_agent().replace("name: ux-expert", "name: other-expert", 1)
    assert check_frontmatter(broken) != []


def test_mutation_tools_extra_tool():
    broken = read_agent().replace("TaskList\n", "TaskList, Skill\n", 1)
    assert check_tools(broken) != []


def test_mutation_tools_dropped_tool():
    broken = read_agent().replace("SendMessage, TaskUpdate", "TaskUpdate", 1)
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
    nested = tmp_path / "ux-expert" / "AGENT.md"
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
    return "---\nname: ux-expert\n---\n\n" + str(contract.compiled_template_body(text)) + "\n"


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
        pytest.skip("global template ~/.claude/agents/ux-expert.md is not installed on this runner")
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
    broken = read_agent().replace("--studio-accent", "--studio-other")
    assert check_bindings_supplied(broken) != []


def test_mutation_binding_phrase_only_in_another_binding():
    text = read_agent()
    section = contract.bullet_of(contract.bindings_section(text), "Mode pill")
    moved = text.replace(section, section.replace("a 503", "REMOVED"), 1) + "\na 503\n"
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
    assert contract.check_anchor_table(text + "\nthere is no component library\n", BINDING_RULES)
    overlapping = {"a": "Stage files by name", "b": "Stage files by name and never"}
    assert contract.check_anchor_table(text, overlapping)


def test_mutation_placeholder_added():
    broken = read_agent() + "\n- Plans go to <probe-name>.\n"
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
    stale = tmp_path / "ux-expert.md"
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
