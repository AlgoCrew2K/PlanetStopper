"""Every repeated row group of every host meets its alignment rule at every width, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_aligned_tables_browser.py``, a ``-k`` of a family, a state or a width id picks a chunk).
Firefox headless through Playwright; the app in this process on the per-test database, seeded through
the real writers from the captured records (``tests/ui/_page_explainers.py`` and the advisor seeds
the host cycles use), every request to anything but the server refused. This is the one sweep over
the registry of ``tests/ui/_alignment.py``: each host family opens each state that holds a group,
once per width of ``al.WIDTHS`` (one width inside every layout band from 1001 px up, and 2560 px),
and runs the measure of every group of the state:

* a ``table`` group: one header row above all body rows, the body rows neither overlapping nor
  side by side, every column's text sharing its left edge (right edge for a numeric column), and, in
  the groups the layout rule names, no gap wider than 48 px between adjacent columns' text;
* a ``fields`` group: each named field at one offset from its row's or card's left edge on every row
  or card (numeric fields by their right edge), and the Baseline and Variant figure columns of a
  result card;
* a toolbar row: no text, caption or stamp starts more than 24 px after the element before it or off
  the row's left edge, controls excepted;
* a figure line: equal gaps of at most 48 px, and from 1440 px no figure's label or value breaking
  across lines.

Also here: a registry check that no group is left out of the sweep without a stated reason (no
browser); a long symphony name wrapping inside its own cell on every host that shows one; each empty
state; the dashboard after three polls and the Overview after three refreshes (no header row added
twice, no group moved); and no description, note or stamp placed inside another section's box.

Provenance: the worlds are the captured droplet records through the real writers. Two groups the page
script writes after a user action have no real producer to run, so the browser answers the action's
request with a labelled construction (``SUGGESTION_ANSWER``, ``LIVE_RUN_ANSWER``: the keys the
routes return, with made-up values); the page's own script draws the cards from it. The one edit made
to a seeded record is named at its call site.
"""

from __future__ import annotations

import copy
import json

import pytest

import database
from tests.app import _retirement_tab_world as w
from tests.app import _tenure_review_panel_builders as pb
from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_explainers as harness
from tests.ui import _page_explainers_advisor as adv
from tests.ui import _page_explainers_production_shapes as prod
from tests.ui import test_advisor_observations_browser as obs_module
from tests.ui import test_advisor_polish_browser as polish_module
from tests.ui import test_dashboard_alignment_browser as dash_alignment
from tests.ui import test_page_explainers_advisor_browser as advisor_module
from tests.ui import test_page_explainers_dashboard_browser as dashboard_module
from tests.ui import test_page_explainers_performance_history_browser as perf_module
from tests.ui import test_page_explainers_settings_browser as settings_module
from tests.ui import test_swap_logic_result_alignment_browser as swap_logic_module
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
)
from tests.ui.test_page_explainers_performance_history_browser import (  # noqa: F401
    seeded as performance_history_seeded,
)
from tests.ui.test_page_explainers_settings_browser import world  # noqa: F401  (dashboard world)

pytestmark = pytest.mark.browser

TABLE = "TABLE_GROUPS"
TOOLBAR = "TOOLBAR_ROWS"
LINE = "FIGURE_LINES"

# The groups whose columns must sit within 48 px of each other when their content is narrower than
# their panel (the layout rule names them); a result card's figure columns carry the same bound.
GAP_RULE = frozenset(
    {
        "comparison_rows",
        "risk_metrics",
        "preconditions",
        "exit_turnover",
        "exits",
        "autotune_runs",
        "autotune_older_runs",
        "observation_lines",
    }
)
# A group with a single row is aligned with itself, so a measure over fewer rows proves nothing.
MIN_ROWS = {"survivor_cards": 3, "pending_proposal_cards": 4, "live_run_cards": 3}
DEFAULT_MIN_ROWS = 2
# Lines that may wrap into rows below 1440 px: the rule asks for one line each from 1440 px.
LINE_FROM_WIDTH = {
    "tracked_armed_triggered": 1440,
    "dollar_saved_line": 1440,
    "sleeve_card_line": 1440,
    "performance_headline_strip": 1440,
    "history_hero_figures": 1440,
    "tenure_card_head": 1440,
}
NO_WRAP_FROM = 1440
SETTLE_MS = 400


def _group(registry: str, name: str, **override) -> tuple[str, str, dict]:
    return registry, name, override


# ---------------------------------------------------------------------------
# One measure per kind of group
# ---------------------------------------------------------------------------


def check(
    page, registry: str, name: str, width: int, *, allow_absent: bool = False, **override
) -> list[str]:
    """The faults of one group at the page's current state; a group that is not there is a fault,
    except where ``allow_absent`` says the state may hold none (an empty state)."""
    entry = getattr(al, registry)[name]
    problems: list[str] = []

    def take(found: dict, what: str, *, need: int = 1) -> None:
        violations = found["violations"]
        if allow_absent:
            # An empty state holds what it holds: nothing, or one message row that has no header
            # above it (``HEADER_ONLY_JS`` is what rejects a header row over nothing).
            if found["found"] == 0:
                return
            need = 1
            violations = [v for v in violations if "no header row above" not in v]
        if found["found"] < need:
            problems.append(f"{name} {what}: measured {found['found']}, the premise is {need}")
        problems.extend(f"{name} {what}: {v}" for v in violations)

    if registry == TABLE and entry["kind"] == "table":
        spec = al.spec_of(entry, name=name, **override)
        take(al.measure(page, al.COLUMN_EDGES_JS, spec), "column edges")
        if name in GAP_RULE:
            take(al.measure(page, al.COLUMN_GAPS_JS, spec), "column gaps")
    elif registry == TABLE:
        spec = al.spec_of(entry, name=name, **override)
        take(
            al.measure(page, al.FIELD_OFFSETS_JS, spec),
            "field offsets",
            need=MIN_ROWS.get(name, DEFAULT_MIN_ROWS),
        )
        if entry.get("figure_columns"):
            figures = {"name": name, **entry["figure_columns"]}
            take(al.measure(page, al.FIGURE_COLUMNS_JS, figures), "figure columns")
        if name in GAP_RULE:
            take(al.measure(page, al.COLUMN_GAPS_JS, spec), "field gaps")
    elif registry == TOOLBAR:
        spec = {"name": name, "row": entry["row"], "max_gap": al.MAX_NON_CONTROL_GAP_PX, **override}
        take(al.measure(page, al.ROW_START_JS, spec), "row start")
    else:
        spec = {
            "name": name,
            "line": entry["line"],
            "items": entry["items"],
            "rows_may_wrap": entry.get("rows_may_wrap", False),
            "no_wrap": width >= NO_WRAP_FROM,
            **override,
        }
        found = al.measure(page, al.LINE_GAPS_JS, spec)
        take(found, "line gaps")
        if any(line["items"] < 2 for line in found["lines"]):
            problems.append(f"{name} line gaps: a line holds one item")
    return problems


def sweep(
    page, groups: list[tuple[str, str, dict]], width: int, *, allow_absent: bool = False
) -> list[str]:
    """Every group of a state that applies at ``width``."""
    problems: list[str] = []
    for registry, name, override in groups:
        if registry == LINE and width < LINE_FROM_WIDTH.get(name, 0):
            continue
        problems += check(page, registry, name, width, allow_absent=allow_absent, **override)
    return problems


def assert_swept(probe, groups, width: int, where: str, *, allow_absent: bool = False) -> None:
    problems = sweep(probe.page, groups, width, allow_absent=allow_absent)
    assert not problems, f"{where} at {width}:\n" + "\n".join(problems)
    assert probe.page_errors == [], probe.page_errors


def _world(tmp_path, monkeypatch, name: str) -> None:
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world(name, tmp_path, monkeypatch.setattr)


# ---------------------------------------------------------------------------
# Hosts and the states of each: the dashboard, its detail panel and its settings modal
# ---------------------------------------------------------------------------

DASHBOARD_STATES = {
    "dashboard": {
        "world": "dashboard_full",
        "groups": [
            _group(TABLE, "comparison_rows"),
            _group(TABLE, "symphony_cards"),
            _group(TOOLBAR, "hero_top"),
            _group(LINE, "tracked_armed_triggered"),
            _group(LINE, "dollar_saved_line"),
        ],
    },
    "sleeves": {
        "world": "dashboard_sleeves",
        "rules": True,
        "groups": [_group(TABLE, "sleeve_rule_rows"), _group(LINE, "sleeve_card_line")],
    },
    "detail-panel": {
        "world": "dashboard_full",
        "events": True,
        "open": "detail",
        "groups": [
            _group(TABLE, "risk_math_rows"),
            _group(TABLE, "event_rows"),
            _group(TABLE, "symphony_variable_rows"),
        ],
    },
    "settings-modal": {
        "world": "dashboard_full",
        "open": "modal",
        "groups": [
            _group(TABLE, "modal_parameter_rows"),
            _group(TABLE, "modal_locked_variable_entries"),
        ],
    },
}


def _prepare_dashboard(tmp_path, monkeypatch, spec: dict) -> None:
    _world(tmp_path, monkeypatch, spec["world"])
    monkeypatch.setattr(database, "SYMPHONY_LOGS_FILE", str(tmp_path / "symphony_logs.json"))
    if spec.get("events"):
        dash_alignment._seed_events()
    if spec.get("rules"):
        dash_alignment._seed_sleeve_with_three_rules()


def _open_dashboard_state(probe, spec: dict) -> None:
    if spec.get("open") == "detail":
        dash_alignment._open_detail(probe)
    elif spec.get("open") == "modal":
        dash_alignment._open_modal(probe)


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("state", list(DASHBOARD_STATES))
def test_every_dashboard_group_meets_its_rule_at_every_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    state,
    width,
):
    spec = DASHBOARD_STATES[state]
    _prepare_dashboard(tmp_path, monkeypatch, spec)
    with dashboard_module._Dashboard(firefox, advisor_server, width, al.VIEWPORT_HEIGHT) as probe:
        _open_dashboard_state(probe, spec)
        assert_swept(probe, spec["groups"], width, f"dashboard {state}")


# ---------------------------------------------------------------------------
# Performance and History
# ---------------------------------------------------------------------------

PERFORMANCE_GROUPS = [
    _group(TABLE, "risk_metrics"),
    _group(TABLE, "preconditions"),
    _group(TABLE, "exit_turnover"),
    _group(TOOLBAR, "performance_toolbars"),
    _group(LINE, "performance_headline_strip"),
]
HISTORY_GROUPS = [
    _group(TABLE, "exit_reasons"),
    _group(TOOLBAR, "history_toolbar"),
    _group(LINE, "history_hero_figures"),
]
HISTORY_EXIT_GROUPS = [_group(TABLE, "exits")]


@pytest.mark.parametrize("width", al.WIDTHS)
def test_every_performance_group_meets_its_rule_at_every_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        perf_module._open(
            probe, "/performance", preconditions=(200, perf_module._usable_preconditions_body())
        )
        assert_swept(probe, PERFORMANCE_GROUPS, width, "performance")


@pytest.mark.parametrize("width", al.WIDTHS)
def test_every_history_group_meets_its_rule_at_every_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        perf_module._open(probe, "/history")
        assert_swept(probe, HISTORY_GROUPS, width, "history")
        perf_module._open_exit_day(probe)
        assert_swept(probe, HISTORY_EXIT_GROUPS, width, "history, one exit day opened")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.WIDTHS)
def test_every_settings_group_meets_its_rule_at_every_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,  # noqa: F811
    width,
):
    with settings_module.settings_open(
        firefox, advisor_server, size=(width, al.VIEWPORT_HEIGHT)
    ) as probe:
        assert_swept(probe, [_group(TOOLBAR, "settings_actions")], width, "settings")
        settings_module.go_to(probe, "algorithm")
        assert_swept(probe, [_group(TABLE, "algorithm_parameter_cards")], width, "algorithm")
        settings_module.go_to(probe, "symphonies")
        probe.page.locator("#sym-list .sym-list-btn").first.click()
        probe.page.wait_for_selector(".override-grid .override-row")
        assert_swept(probe, [_group(TABLE, "symphony_override_rows")], width, "symphony overrides")


# ---------------------------------------------------------------------------
# The AI Advisor, one state per tab
# ---------------------------------------------------------------------------


def _seed_overview() -> None:
    """Three guardrail producers with histories, the Market Prism read, the captured Retirement
    run, and the autotune runs of a weekly batch (eleven symphonies with older runs)."""
    obs_module._seed_lines()
    prod.seed_same_batch_autotune_runs(4)


def _seed_retirement() -> None:
    w.seed_roster()
    adv.seed_recommendation_cards(3)
    adv.seed_long_tenure_review(11)


def _seed_survivors() -> None:
    adv.seed_strategy_builder_cards(3)


def _open_every_disclosure(tab: str):
    """A function that opens every disclosure of one tab's panel."""

    def open_all(probe) -> None:
        probe.page.evaluate(
            "(tab) => document.querySelectorAll('#tab-panel-' + tab + ' details')"
            ".forEach((d) => { d.open = true; })",
            tab,
        )
        probe.page.wait_for_timeout(SETTLE_MS)

    return open_all


def _scoped(tab: str, name: str) -> tuple[str, str, dict]:
    """A group of the card tabs, scoped to its own tab panel (two tabs hold figure tables)."""
    entry = al.TABLE_GROUPS[name]
    return _group(TABLE, name, group=f'[data-testid="tab-panel-{tab}"] {entry["group"]}')


ADVISOR_STATES = {
    "overview": {
        "seed": _seed_overview,
        "tab": None,
        "open": _open_every_disclosure("overview"),
        # What the page shows closed is measured first, then everything with every disclosure open
        # (an open history or older-runs table must leave the other lines where they were).
        "closed_groups": [
            _group(TABLE, "autotune_runs"),
            _group(TABLE, "observation_lines"),
            _group(TOOLBAR, "overview_toolbar"),
            _group(TOOLBAR, "market_prism_header"),
        ],
        "groups": [
            _group(TABLE, "autotune_runs"),
            _group(TABLE, "autotune_older_runs"),
            _group(TABLE, "observation_lines"),
            _group(TABLE, "observation_histories"),
            _group(TOOLBAR, "overview_toolbar"),
            _group(TOOLBAR, "market_prism_header"),
        ],
    },
    "retirement": {
        "seed": _seed_retirement,
        "tab": "retirement",
        "open": _open_every_disclosure("retirement"),
        "groups": [
            _group(TABLE, "recommendation_cards"),
            _group(TABLE, "tenure_cards"),
            _group(TOOLBAR, "retirement_run_row"),
            _group(TOOLBAR, "tenure_header"),
            _group(LINE, "retirement_last_run_tiles"),
            _group(LINE, "tenure_run_line"),
            _group(LINE, "tenure_card_head"),
        ],
    },
    "strategy-builder": {
        "seed": _seed_survivors,
        "tab": "strategy-builder",
        "groups": [
            _group(TABLE, "survivor_cards"),
            _scoped("strategy-builder", "stats_tables"),
            _group(TOOLBAR, "strategy_builder_run_controls"),
        ],
    },
    "frontrunner-builder": {
        "seed": advisor_module._seed_five_proposals,
        "tab": "frontrunner-builder",
        "groups": [
            _group(TABLE, "pending_proposal_cards"),
            _scoped("frontrunner-builder", "stats_tables"),
            _group(TOOLBAR, "strategy_builder_run_controls"),
        ],
    },
    "asset-swaps": {
        "seed": None,
        "tab": "asset-swaps",
        "groups": [_group(TOOLBAR, "swap_form")],
    },
    "logic-changes": {
        "seed": None,
        "tab": "logic-changes",
        "groups": [_group(TOOLBAR, "tweak_form")],
    },
}


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("state", list(ADVISOR_STATES))
def test_every_advisor_group_meets_its_rule_at_every_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    state,
    width,
):
    spec = ADVISOR_STATES[state]
    advisor_module._prepare(tmp_path, monkeypatch, world="empty", seed=spec["seed"])
    with advisor_module._open(
        firefox, advisor_server, (width, al.VIEWPORT_HEIGHT), spec["tab"]
    ) as opened:
        closed = spec.get("closed_groups", spec["groups"])
        assert_swept(opened.probe, closed, width, f"advisor {state}")
        if spec.get("open"):
            spec["open"](opened.probe)
            assert_swept(opened.probe, spec["groups"], width, f"advisor {state}, disclosures open")


# A labelled construction: the keys ``/ai-advisor/suggest`` returns, with made-up values. The third
# suggestion is rejected by the out-of-sample gate, so its first button is the wider disabled one.
SUGGESTION_ANSWER = copy.deepcopy(polish_module._STUB_ANSWER)
SUGGESTION_ANSWER["suggestions"].append(
    {
        "config_key": "MAX_SQUEEZE_FLOOR",
        "current_value": 0.5,
        "suggested_value": 0.4,
        "risk_direction": "looser",
        "confidence": "medium",
        "data_sufficiency": "sufficient",
        "rationale": "Labelled construction.",
        "impact": {"metric": "sharpe", "before": 0.4, "after": 0.3, "delta": -0.1},
        "oos_status": "rejected",
        "oos_reason": "held-out tail slice worsened",
        "four_gates_verdict": {"allowlist": True, "risk_direction": "pass"},
    }
)
# A labelled construction: the keys the Strategy Builder run route returns, with made-up values.
LIVE_RUN_ANSWER = {
    "n_candidates": 5,
    "fdr_adjusted_threshold": 0.0123,
    "survivors": [
        {
            "candidate_id": "cand-short",
            "template_id": "built-new",
            "params": {"objective": "diversify"},
            "caveats": [],
        },
        {
            "candidate_id": "cand-an-identifier-a-good-deal-longer-than-the-first-0123456789",
            "template_id": "atlas-suggested",
            "params": {"objective": "cut_drawdown"},
            "caveats": ["Labelled construction: a caveat the server appends."],
        },
    ],
    "rejected": [
        {
            "candidate_id": "cand-rejected-one",
            "template_id": "built-new",
            "params": {"objective": "diversify"},
            "rejection_reason": "pbo_veto",
        },
        {
            "candidate_id": "cand-rejected-two-with-a-longer-identifier",
            "template_id": "built-new",
            "params": {"objective": "volatility_mitigation"},
            "rejection_reason": "below_spy_alpha",
        },
    ],
}


def _fulfil(probe, pattern: str, body: dict) -> None:
    probe.page.route(
        pattern,
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(body)
        ),
    )


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_suggestion_cards_the_script_writes_meet_their_rule_at_every_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    width,
):
    advisor_module._prepare(tmp_path, monkeypatch, world="empty")
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        _fulfil(probe, "**/ai-advisor/suggest", SUGGESTION_ANSWER)
        open_page(probe, "/ai-advisor")
        probe.page.evaluate(
            """() => {
              const sel = document.getElementById('symphony-id-input');
              const o = document.createElement('option'); o.value = 'stub'; o.textContent = 'stub';
              sel.appendChild(o); sel.value = 'stub';
              document.getElementById('get-suggestions-btn').disabled = false;
              window.getSuggestions();
            }"""
        )
        probe.page.wait_for_selector("#suggestions-container > [id^=card-]", state="visible")
        probe.page.wait_for_function(
            "(n) => document.querySelectorAll('#suggestions-container > [id^=card-]').length === n",
            arg=len(SUGGESTION_ANSWER["suggestions"]),
        )
        probe.page.wait_for_timeout(SETTLE_MS)
        assert_swept(probe, [_group(TABLE, "suggestion_cards")], width, "suggestion cards")


@pytest.mark.parametrize("width", al.WIDTHS)
def test_the_cards_of_a_live_strategy_builder_run_meet_their_rule_at_every_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    width,
):
    advisor_module._prepare(tmp_path, monkeypatch, world="empty")
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        _fulfil(probe, "**/ai-advisor/strategy-builder/run", LIVE_RUN_ANSWER)
        open_page(probe, "/ai-advisor", fragment="#strategy-builder")
        probe.page.wait_for_selector('[data-testid="tab-panel-strategy-builder"]', state="visible")
        probe.page.evaluate("() => window.sbRunAnalysis()")
        probe.page.wait_for_selector('[data-testid="sb-live-survivor-cards"]', state="visible")
        probe.page.evaluate(
            "() => document.querySelectorAll('#sb-run-results details')"
            ".forEach((d) => { d.open = true; })"
        )
        probe.page.wait_for_timeout(SETTLE_MS)
        assert_swept(probe, [_group(TABLE, "live_run_cards")], width, "live run cards")


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("kind", ["swap", "logic"])
def test_the_result_cards_of_a_swap_and_a_logic_change_meet_their_rule_at_every_width(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    kind,
    width,
):
    swap_logic_module._prepare(tmp_path, monkeypatch)
    name = {"swap": "swap_result_figures", "logic": "logic_change_result_figures"}[kind]
    with swap_logic_module._evaluated(firefox, advisor_server, monkeypatch, kind, width) as probe:
        assert_swept(probe, [_group(TABLE, name)], width, f"{kind} result cards")


# ---------------------------------------------------------------------------
# An open disclosure leaves every line where it was
# ---------------------------------------------------------------------------


def _gaps_of(page, name: str) -> list[tuple[list[str], float]]:
    """The gap between each pair of adjacent fields of a group of lines or cards."""
    entry = al.TABLE_GROUPS[name]
    found = al.measure(page, al.COLUMN_GAPS_JS, al.spec_of(entry, name=name))
    assert found["found"] > 0, f"{name}: the measure found nothing to measure"
    return [(g["between"], g["gap"]) for g in found["instances"][0]["gaps"]]


@pytest.mark.parametrize("width", al.WIDTHS)
def test_opening_every_history_and_older_run_table_moves_no_field_of_any_observation_line(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    width,
):
    """Rejects a list whose columns are sized by the widest content of any line, a history table
    included: opening one stretches the columns of every line, so the verdict, time and count of
    each line move away from its label."""
    spec = ADVISOR_STATES["overview"]
    advisor_module._prepare(tmp_path, monkeypatch, world="empty", seed=spec["seed"])
    with advisor_module._open(
        firefox, advisor_server, (width, al.VIEWPORT_HEIGHT), spec["tab"]
    ) as opened:
        before = _gaps_of(opened.page, "observation_lines")
        spec["open"](opened.probe)
        after = _gaps_of(opened.page, "observation_lines")
    assert [b for b, _ in before] == [b for b, _ in after], "the fields of a line changed"
    moved = [
        f"{'/'.join(pair)}: {was} px closed, {now} px open"
        for (pair, was), (_, now) in zip(before, after, strict=True)
        if abs(was - now) > al.TOLERANCE_PX
    ]
    assert not moved, f"at {width} opening the histories moved a field: " + "; ".join(moved)


# ---------------------------------------------------------------------------
# A symphony name long enough to wrap
# ---------------------------------------------------------------------------

# An invented name, longer than any column of any host holds in one line.
LONG_NAME = (
    "Labelled long symphony name that keeps on going well past the width of any column "
    "so that it has to wrap onto a second and a third line 0123456789"
)
NEEDLE = LONG_NAME[:30]
LONG_NAME_HOSTS = ["dashboard", "performance", "history", "settings", "retirement"]

NAME_FIT_JS = r"""(needle) => {
  const out = { found: 0, faults: [], pageScroll: document.documentElement.scrollWidth,
                viewport: innerWidth };
  const CELL = 'td, th, [role="cell"], [role="gridcell"], .card-id, .sym-card, '
      + '.tenure-review-entry, .sym-list-btn, .override-row, li';
  const seen = new Set();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.nodeValue.includes(needle)) continue;
    const el = n.parentElement;
    if (seen.has(el) || el.getClientRects().length === 0) continue;
    if (getComputedStyle(el).visibility === 'hidden') continue;
    seen.add(el);
    const range = document.createRange();
    range.selectNodeContents(n);
    const rects = [...range.getClientRects()].filter((g) => g.width > 1 && g.height > 1);
    if (!rects.length) continue;
    out.found += 1;
    const left = Math.min(...rects.map((g) => g.left));
    const right = Math.max(...rects.map((g) => g.right));
    const cell = el.closest(CELL);
    const where = el.className || el.tagName;
    if (cell) {
      const box = cell.getBoundingClientRect();
      if (left < box.left - 1 || right > box.right + 1) {
        out.faults.push(where + ' runs ' + Math.round(left) + '..' + Math.round(right)
                        + ' outside its cell ' + Math.round(box.left) + '..' + Math.round(box.right));
      }
    }
    if (right > innerWidth + 1) out.faults.push(where + ' leaves the viewport');
  }
  return out;
}"""


def _lengthen_the_first_symphony_name() -> None:
    """The one named edit of the seeded state: the first symphony's display name."""
    state = database.load_state()
    for entry in state.values():
        if isinstance(entry, dict) and "name" in entry:
            entry["name"] = LONG_NAME
            break
    database.save_state(state)


def _lengthen_an_exits_name() -> None:
    """The one named edit of the seeded post-mortem day: its first exit's symphony name."""
    import pathlib

    import analytics

    path = pathlib.Path(analytics._POST_MORTEMS_DIR) / f"post_mortem_{perf_module.EXIT_DAY}.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    record["triggers"][0]["symphony_name"] = LONG_NAME
    path.write_text(json.dumps(record), encoding="utf-8")


def _seed_retirement_with_a_long_name() -> None:
    w.seed_roster()
    adv.seed_recommendation_cards(3)
    record = adv.long_tenure_record(11)
    record["entries"][0]["display_name"] = LONG_NAME
    pb.store_run(record)


def _assert_the_name_fits(probe, where: str, *, at_least: int = 1) -> None:
    fit = probe.page.evaluate(NAME_FIT_JS, NEEDLE)
    assert fit["found"] >= at_least, f"{where}: the long name is on the page {fit['found']} times"
    assert not fit["faults"], f"{where}: " + "; ".join(fit["faults"])
    assert fit["pageScroll"] <= fit["viewport"], f"{where}: the page scrolls sideways {fit}"


@pytest.mark.parametrize("width", al.WIDTHS)
@pytest.mark.parametrize("host", LONG_NAME_HOSTS)
def test_a_long_symphony_name_wraps_inside_its_own_cell_and_the_other_columns_stay_aligned(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    request,
    host,
    width,
):
    """Rejects a name cell that does not wrap (it spills over the next column or out of its card)
    and a name that pushes the columns of the other rows out of line."""
    where = f"{host} with a long name at {width}"
    if host == "dashboard":
        _prepare_dashboard(tmp_path, monkeypatch, DASHBOARD_STATES["dashboard"])
        _lengthen_the_first_symphony_name()
        with dashboard_module._Dashboard(
            firefox, advisor_server, width, al.VIEWPORT_HEIGHT
        ) as probe:
            assert_swept(probe, DASHBOARD_STATES["dashboard"]["groups"], width, where)
            _assert_the_name_fits(probe, where)
    elif host in ("performance", "history"):
        request.getfixturevalue("performance_history_seeded")
        with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
            if host == "performance":
                _lengthen_the_first_symphony_name()
                perf_module._open(
                    probe,
                    "/performance",
                    preconditions=(200, perf_module._usable_preconditions_body()),
                )
                assert_swept(probe, PERFORMANCE_GROUPS, width, where)
            else:
                _lengthen_an_exits_name()
                perf_module._open(probe, "/history")
                perf_module._open_exit_day(probe)
                assert_swept(probe, [*HISTORY_GROUPS, *HISTORY_EXIT_GROUPS], width, where)
            _assert_the_name_fits(probe, where)
    elif host == "settings":
        request.getfixturevalue("world")
        _lengthen_the_first_symphony_name()
        with settings_module.settings_open(
            firefox, advisor_server, size=(width, al.VIEWPORT_HEIGHT)
        ) as probe:
            settings_module.go_to(probe, "symphonies")
            _assert_the_name_fits(probe, f"{where}, the list")
            probe.page.locator("#sym-list .sym-list-btn").first.click()
            probe.page.wait_for_selector(".override-grid .override-row")
            assert_swept(probe, [_group(TABLE, "symphony_override_rows")], width, where)
            _assert_the_name_fits(probe, where, at_least=2)
    else:
        advisor_module._prepare(
            tmp_path, monkeypatch, world="empty", seed=_seed_retirement_with_a_long_name
        )
        spec = ADVISOR_STATES["retirement"]
        with advisor_module._open(
            firefox, advisor_server, (width, al.VIEWPORT_HEIGHT), "retirement"
        ) as opened:
            assert_swept(opened.probe, spec["groups"], width, where)
            _assert_the_name_fits(opened.probe, where)


# ---------------------------------------------------------------------------
# Every empty state
# ---------------------------------------------------------------------------

# Every table shown with its header row and no body row: a name for each, by test id, id or class.
HEADER_ONLY_JS = r"""() => {
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const closed = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const holder = n.parentElement;
      if (holder && holder.tagName === 'DETAILS' && !holder.open && n.tagName !== 'SUMMARY') return true;
    }
    return false;
  };
  const out = [];
  for (const table of document.querySelectorAll('table, [role="table"]')) {
    if (!shown(table) || closed(table)) continue;
    const rows = [...table.querySelectorAll('tr, [role="row"]')]
      .filter((r) => shown(r) && r.closest('table, [role="table"]') === table);
    const isHead = (r) => [...r.children].filter(shown).length > 0
      && [...r.children].filter(shown).every((c) => c.matches('th, [role="columnheader"]'));
    const head = rows.filter(isHead);
    if (head.length > 0 && rows.length === head.length) {
      out.push(table.getAttribute('data-testid') || table.id || table.className || 'a table');
    }
  }
  return out;
}"""
EMPTY_HOSTS = ["/", "/performance", "/history", "/settings", "/ai-advisor"]
# Tables whose header row is written by the template and whose rows the page script fills, so with
# nothing to fill they show a header and a line saying why, as the reference's pages do.
HEADER_ONLY_AS_AT_THE_REFERENCE: frozenset[str] = frozenset()


def _groups_of_host(host: str, tab: str | None) -> list[tuple[str, str, dict]]:
    """Every registry group of ``host`` (of one Advisor tab when ``tab`` is given) the page itself
    holds, a group a user action produces left out."""
    found = []
    for registry in (TABLE, TOOLBAR, LINE):
        for name, entry in getattr(al, registry).items():
            wanted = entry.get("open", {})
            if entry["host"] != host or "action" in wanted or wanted.get("tab") != tab:
                continue
            if registry == TABLE and entry.get("group") is None and not entry.get("rows"):
                continue
            found.append(_group(registry, name))
    return found


@pytest.mark.parametrize("width", [1001, 1440, 1920])
@pytest.mark.parametrize("host", EMPTY_HOSTS)
def test_an_empty_host_shows_each_empty_state_and_no_table_with_a_header_and_no_rows(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    host,
    width,
):
    """No runs, no turnover, no review, no proposals: what is not there is not measured, what is
    there meets its rule, and no table keeps a header row over nothing."""
    advisor_module._prepare(tmp_path, monkeypatch, world="empty")
    tabs = list(al.ADVISOR_TABS) if host == "/ai-advisor" else [None]
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        open_page(probe, host)
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        for tab in tabs:
            if tab:
                probe.page.click(f'[data-tab="{tab}"]:not([data-testid^="tab-panel"])')
                probe.page.wait_for_selector(f'[data-testid="tab-panel-{tab}"]', state="visible")
            where = f"empty {host} {tab or ''}".strip()
            assert_swept(probe, _groups_of_host(host, tab), width, where, allow_absent=True)
            bare = set(probe.page.evaluate(HEADER_ONLY_JS)) - HEADER_ONLY_AS_AT_THE_REFERENCE
            assert not bare, f"{where}: a header row over no rows: {sorted(bare)}"


# ---------------------------------------------------------------------------
# After three polls and three refreshes
# ---------------------------------------------------------------------------


def test_the_dashboard_groups_keep_their_rules_and_their_rows_after_three_polls(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    """Rejects a poll that rewrites the cards or the comparison rows in a form the layout rule does
    not hold for, and one that adds a row or a header each time it runs."""
    spec = DASHBOARD_STATES["dashboard"]
    _prepare_dashboard(tmp_path, monkeypatch, spec)
    counts = (
        "() => [document.querySelectorAll('#portfolio-strip > .vs-row').length,"
        " document.querySelectorAll('[data-testid=\"sym-card\"]').length]"
    )
    with dashboard_module._Dashboard(
        firefox, advisor_server, al.WIDTHS[-2], al.VIEWPORT_HEIGHT
    ) as probe:
        first = probe.page.evaluate(counts)
        for refresh in range(3):
            probe.page.clock.run_for(dashboard_module.POLL_MS)
            probe.page.wait_for_timeout(500)
            assert_swept(
                probe, spec["groups"], al.WIDTHS[-2], f"dashboard after poll {refresh + 1}"
            )
            assert probe.page.evaluate(counts) == first, f"poll {refresh + 1} changed the row count"


def test_the_overview_groups_keep_their_rules_and_one_header_after_three_refreshes(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    """The Overview refreshes its runs every 15 s: no header row is added twice and no group moves."""
    spec = ADVISOR_STATES["overview"]
    advisor_module._prepare(tmp_path, monkeypatch, world="empty", seed=spec["seed"])
    width = al.WIDTHS[-2]
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, "/ai-advisor")
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        heads = "() => document.querySelectorAll('#autotune-runs-list .autotune-row--head').length"
        first = probe.page.evaluate(heads)
        assert first == 1, f"premise: one header row before any refresh, not {first}"
        for refresh in range(3):
            probe.context.clock.run_for(16_000)
            probe.page.wait_for_timeout(500)
            assert_swept(
                probe, spec["closed_groups"], width, f"overview after refresh {refresh + 1}"
            )
            assert probe.page.evaluate(heads) == 1, f"refresh {refresh + 1} added a header row"


# ---------------------------------------------------------------------------
# No description, note or stamp inside another section's box
# ---------------------------------------------------------------------------

FOREIGN_BOX_JS = r"""() => {
  const SECTION = '.hp-panel, .dash-panel, .adv-panel, .hero-section, .retirement-subsection, '
      + '.settings-section, .sym-card, .prism-block';
  const TEXT = '[data-explainer], .legend-as-of, .adv-note, [data-testid*="stamp"], '
      + '[data-testid$="as-of"]';
  const closed = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const holder = n.parentElement;
      if (holder && holder.tagName === 'DETAILS' && !holder.open && n.tagName !== 'SUMMARY') return true;
    }
    return false;
  };
  const shown = (el) => el.getClientRects().length > 0
      && getComputedStyle(el).visibility !== 'hidden' && !closed(el);
  const label = (el) => el.getAttribute('data-explainer') || el.getAttribute('data-testid')
      || el.id || String(el.className).slice(0, 40) || el.tagName;
  const sections = [...document.querySelectorAll(SECTION)].filter(shown);
  const out = [];
  for (const el of document.querySelectorAll(TEXT)) {
    if (!shown(el) || !(el.textContent || '').trim()) continue;
    if (el.closest('#detail-panel, #sym-settings-modal')) continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1) continue;
    const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    for (const s of sections) {
      if (s.contains(el) || el.contains(s)) continue;
      const b = s.getBoundingClientRect();
      if (cx > b.left && cx < b.right && cy > b.top && cy < b.bottom) {
        out.push(label(el) + ' sits inside the box of ' + label(s));
      }
    }
  }
  return out;
}"""


def _assert_no_foreign_boxes(probe, where: str) -> None:
    found = probe.page.evaluate(FOREIGN_BOX_JS)
    assert not found, f"{where}: " + "; ".join(sorted(set(found)))


@pytest.mark.parametrize("width", [1920, 1440])
@pytest.mark.parametrize("host", ["/", "/settings", "/ai-advisor"])
def test_no_description_note_or_stamp_sits_inside_another_sections_box_on_the_full_world_pages(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    host,
    width,
):
    advisor_module._prepare(
        tmp_path, monkeypatch, world="full", seed=advisor_module._seed_everything
    )
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        open_page(probe, host)
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        if host == "/ai-advisor":
            for tab in al.ADVISOR_TABS:
                probe.page.click(f'[data-tab="{tab}"]:not([data-testid^="tab-panel"])')
                probe.page.wait_for_selector(f'[data-testid="tab-panel-{tab}"]', state="visible")
                _assert_no_foreign_boxes(probe, f"{host} {tab} at {width}")
        elif host == "/settings":
            for section in ("master", "algorithm", "credentials", "symphonies", "maintenance"):
                settings_module.go_to(probe, section)
                _assert_no_foreign_boxes(probe, f"{host} {section} at {width}")
        else:
            _assert_no_foreign_boxes(probe, f"{host} at {width}")


@pytest.mark.parametrize("width", [1920, 1440])
@pytest.mark.parametrize("host", ["/performance", "/history"])
def test_no_description_note_or_stamp_sits_inside_another_sections_box_on_performance_and_history(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
    host,
    width,
):
    with open_probe(firefox, advisor_server, width=width, height=al.VIEWPORT_HEIGHT) as probe:
        if host == "/performance":
            perf_module._open(
                probe, host, preconditions=(200, perf_module._usable_preconditions_body())
            )
        else:
            perf_module._open(probe, host)
            perf_module._open_exit_day(probe)
        _assert_no_foreign_boxes(probe, f"{host} at {width}")


# ---------------------------------------------------------------------------
# What the sweep covers, read by ``test_aligned_tables_sweep_registry.py`` (no browser)
# ---------------------------------------------------------------------------

# A group the sweep does not measure, with the reason; every other entry of the registry is in a
# state above, and the registry test fails for an entry that is neither.
EXCUSED = {
    (
        TABLE,
        "correlation_matrix",
    ): "centred tiles under centred headers by ruling: no column edge applies to it",
}
SWEEP_STATES = [
    *DASHBOARD_STATES.values(),
    {"groups": PERFORMANCE_GROUPS},
    {"groups": HISTORY_GROUPS},
    {"groups": HISTORY_EXIT_GROUPS},
    {
        "groups": [
            _group(TOOLBAR, "settings_actions"),
            _group(TABLE, "algorithm_parameter_cards"),
            _group(TABLE, "symphony_override_rows"),
        ]
    },
    *ADVISOR_STATES.values(),
    {"groups": [_group(TABLE, "suggestion_cards")]},
    {"groups": [_group(TABLE, "live_run_cards")]},
    {
        "groups": [
            _group(TABLE, "swap_result_figures"),
            _group(TABLE, "logic_change_result_figures"),
        ]
    },
]
