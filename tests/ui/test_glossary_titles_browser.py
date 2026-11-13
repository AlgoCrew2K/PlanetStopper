"""Every label that names a Glossary term carries the whole line as its title, on every host.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_glossary_titles_browser.py``, a ``-k`` of a state picks a chunk). Firefox headless
through Playwright; the app in this process on the per-test database, seeded through the real
writers, the clocks fixed as in the sibling modules. ``static/glossary.js`` stamps a ``title`` on
every label whose text is a Glossary line's lead term and on every element carrying
``data-glossary="<key>"``. The host modules each name the labels of their own host; this sweep reads
the rule itself, over the live page of every host state, the rows the page scripts write included:

* every shown label whose text, trimmed and without a trailing colon, equals the lead term of a line
  of the Glossary that owns it has the whole line as its title, and so has every element that
  carries ``data-glossary``, drawn or not; a title the label carried before is kept, never replaced;
* every Glossary line the fixture's title rows name is the title of at least one element in the
  state that holds it;
* the same holds again after the page's own refresh has rewritten its rows: three dashboard polls,
  the window picks of Performance and History, the Overview's autotune refresh.

The line a title is compared with is read from ``tests/fixtures/page_explainers/copy.json`` through
``tests/ui/_page_copy.py``, never from the page; a slot in a line stands for any one word.
"""

from __future__ import annotations

import re

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers as harness
from tests.ui import _page_explainers_advisor as adv
from tests.ui import _performance_page as perf
from tests.ui import test_aligned_tables_browser as aligned
from tests.ui import test_dashboard_alignment_browser as dash_alignment
from tests.ui import test_overview_glossary_browser as overview_module
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
from tests.ui.test_page_explainers_dashboard_browser import (  # noqa: F401  (the dashboard's world)
    seeded as dashboard_seeded,
)
from tests.ui.test_page_explainers_performance_history_browser import (  # noqa: F401
    seeded as performance_history_seeded,
)
from tests.ui.test_page_explainers_settings_browser import world  # noqa: F401  (dashboard world)

pytestmark = pytest.mark.browser

VIEW = (1440, al.VIEWPORT_HEIGHT)
SETTLE_MS = 500

# The verdict words of the preconditions table, each line of the five chips.
VERDICTS = (
    "SUFFICIENT_MET",
    "SUFFICIENT_LIKELY",
    "NOT_MET",
    "NEGATIVE_EDGE",
    "INSUFFICIENT_DATA",
)

# What each state must title: the lines the layout table names for the labels it holds. The first
# set must each title at least one element; the second only when the state's world shows the label.
EXPECTED_KEYS = {
    "dashboard": {"D02", "D17", "D18", "D19", "D20t"},
    "performance": {
        "F07",
        "F08",
        "F09",
        "F15a",
        "F15b",
        "F15c",
        "F15d",
        "F15e",
        "F15f",
        "F15g",
        "F15h",
        "F17a",
        "F17b",
        "F17c",
        "F17d",
        "F17e",
        "F19a",
        "F19b",
        "F19c",
        "F19d",
        "F19e",
        "F24",
    },
    "history": {"Y05", "Y07", "Y13", "Y16"},
    "settings": {"S15", "S16"},
    "overview": {"O09a", "O09c", "O10", "O11", "O16", "O24"},
    "retirement": {"R06", "R07", "R08", "R09", "R29a", "R29b", "R29c", "R29d", "R29e", "R29f"},
    "strategy-builder": {"B07t"},
    "frontrunner-builder": {"X11", "X12"},
    "swap": {"W06"},
    "logic": {"L05"},
}
OPTIONAL_KEYS = {"D05"}

TITLES_JS = r"""(leads) => {
  const norm = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const closed = (el) => {
    for (let n = el; n; n = n.parentElement) {
      const holder = n.parentElement;
      if (holder && holder.tagName === 'DETAILS' && !holder.open && n.tagName !== 'SUMMARY') return true;
    }
    return false;
  };
  const shown = (el) => el.getClientRects().length > 0
      && getComputedStyle(el).visibility !== 'hidden' && !closed(el);
  const glossaries = [...document.querySelectorAll('details[data-terms="glossary"]')];
  const owners = glossaries.map((g) => ({ host: g.getAttribute('data-glossary-host'), el: g.parentElement }));
  // The Glossary that owns an element is the innermost host container holding it.
  const ownerOf = (el) => {
    let best = null;
    for (const o of owners) {
      if (o.el.contains(el) && (!best || best.el.contains(o.el))) best = o;
    }
    return best;
  };
  const out = { hosts: owners.map((o) => o.host), labels: [] };
  // A title is an attribute, so an element named by key is read whether or not it is drawn now (a row
  // a script keeps hidden until it has figures still carries its line).
  for (const el of document.querySelectorAll('[data-glossary]')) {
    if (el.closest('details[data-terms]')) continue;
    const owner = ownerOf(el);
    out.labels.push({ via: 'key', host: owner && owner.host, key: el.getAttribute('data-glossary'),
      text: norm(el.textContent).slice(0, 60), title: el.getAttribute('title'),
      marked: el.hasAttribute('data-glossary-titled') });
  }
  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('details[data-terms]') || !shown(el)) continue;
    // An element that names its line by key is read by that key, whatever its words match.
    if (el.hasAttribute('data-glossary')) continue;
    if (!(el.children.length === 0 || el.matches('th, [role="columnheader"]'))) continue;
    const text = norm(el.textContent).replace(/:$/, '').trim();
    const owner = ownerOf(el);
    if (!owner || !(leads[owner.host] || []).includes(text)) continue;
    out.labels.push({ via: 'lead', host: owner.host, text, title: el.getAttribute('title'),
      marked: el.hasAttribute('data-glossary-titled'), where: el.tagName + '.' + String(el.className).slice(0, 40) });
  }
  return out;
}"""


def _collapsed(text: str) -> str:
    return " ".join((text or "").split())


def _line_matches(text: str, title: str | None) -> bool:
    """Whether ``title`` is the line ``text`` (a slot in the line stands for any one word)."""
    pieces = [re.escape(p) for p in re.split(r"\{[a-z_]+\}", _collapsed(text))]
    return re.fullmatch(r"\S+".join(pieces), _collapsed(title or "")) is not None


def _lines_by_key() -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for host in copy.glossary_hosts():
        for line in copy.glossary(host):
            found.setdefault(line["key"], []).append({**line, "host": host})
    return found


def _titles(probe, where: str) -> set[str]:
    """Every fault of the page's titles, raised at once; the keys that carry their line as title."""
    leads = {
        host: [line["lead_term"] for line in copy.glossary(host) if line["lead_term"]]
        for host in copy.glossary_hosts()
    }
    facts = probe.page.evaluate(TITLES_JS, leads)
    by_key = _lines_by_key()
    titled: set[str] = set()
    problems: list[str] = []
    for label in facts["labels"]:
        if label["via"] == "key":
            lines = by_key.get(label["key"])
            if not lines:
                problems.append(f"{label['text']!r}: data-glossary {label['key']!r} is no line")
                continue
        else:
            lines = [
                line for line in copy.glossary(label["host"]) if line["lead_term"] == label["text"]
            ]
        keys = sorted({line["key"] for line in lines})
        if any(_line_matches(line["text"], label["title"]) for line in lines):
            titled.update(keys)
        elif label["title"] and not label["marked"]:
            continue  # a title the label carried before the Glossary is kept, never replaced
        else:
            problems.append(
                f"{label['text']!r} ({label.get('where') or label['key']}) has the title"
                f" {label['title']!r}, not the line {keys}"
            )
    assert not problems, f"{where}:\n" + "\n".join(problems)
    return titled


def _assert_titles(
    probe, state: str, where: str, *, hosts: tuple[str, ...], not_yet: frozenset[str] = frozenset()
) -> None:
    """The page's titles; ``not_yet`` names lines whose label the state has not drawn yet."""
    present = set(probe.page.evaluate(TITLES_JS, {})["hosts"])
    missing_hosts = [h for h in hosts if h not in present]
    assert not missing_hosts, f"{where}: no Glossary of {missing_hosts} on the page ({present})"
    titled = _titles(probe, where)
    wanted = EXPECTED_KEYS[state] - not_yet
    assert wanted <= titled, f"{where}: no element titled with the lines {sorted(wanted - titled)}"
    assert probe.page_errors == [], probe.page_errors


# ---------------------------------------------------------------------------
# The expectations name every line the layout table names
# ---------------------------------------------------------------------------


def test_the_states_together_expect_every_line_the_layout_table_names():
    named = {key for row in copy.title_rows() for key in row["line_keys"]}
    expected = set().union(*EXPECTED_KEYS.values()) | OPTIONAL_KEYS
    assert named <= expected, f"a labelled line no state expects: {sorted(named - expected)}"
    in_a_glossary = set(_lines_by_key())
    assert expected <= in_a_glossary, f"expected, not a line: {sorted(expected - in_a_glossary)}"


# ---------------------------------------------------------------------------
# The dashboard, its detail panel and its settings modal
# ---------------------------------------------------------------------------


def test_the_dashboard_titles_hold_after_load_after_three_polls_and_in_the_detail_panel(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    dashboard_seeded,  # noqa: F811
):
    dashboard_seeded("dashboard_full")
    with dashboard_module._Dashboard(firefox, advisor_server, *VIEW) as probe:
        probe.page.wait_for_timeout(SETTLE_MS)
        _assert_titles(probe, "dashboard", "dashboard after load", hosts=("/",))
        for poll in range(3):
            probe.page.clock.run_for(dashboard_module.POLL_MS)
            probe.page.wait_for_timeout(SETTLE_MS)
            _assert_titles(probe, "dashboard", f"dashboard after poll {poll + 1}", hosts=("/",))
        dashboard_module._open_first_card(probe)
        _titles(probe, "detail panel open")


def test_the_settings_modals_titles_hold_once_it_is_open(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    dashboard_seeded,  # noqa: F811
):
    dashboard_seeded("dashboard_full")
    with dashboard_module._Dashboard(firefox, advisor_server, *VIEW) as probe:
        dash_alignment._open_modal(probe)
        facts = probe.page.evaluate(TITLES_JS, {})
        assert "settings modal" in facts["hosts"], facts["hosts"]
        _titles(probe, "settings modal open")


# ---------------------------------------------------------------------------
# Performance and History
# ---------------------------------------------------------------------------


def _open_performance(probe) -> None:
    body = perf.get_real("/api/guard-alpha-preconditions")["json"]
    for index, entry in enumerate(body["symphonies"].values()):
        entry["shadow"]["verdict"] = VERDICTS[index % len(VERDICTS)]
    perf_module._open(probe, "/performance", preconditions=(200, body))
    probe.page.wait_for_timeout(SETTLE_MS)


def test_the_performance_titles_hold_after_load_and_after_each_window_pick(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=VIEW[0], height=VIEW[1]) as probe:
        _open_performance(probe)
        _assert_titles(probe, "performance", "performance after load", hosts=("Performance",))
        for token in ("1y", "60"):
            probe.page.click(f'[data-testid="window-btn-{token}"]')
            probe.page.wait_for_timeout(1200)
            _assert_titles(
                probe, "performance", f"performance after picking {token}", hosts=("Performance",)
            )


def test_the_history_titles_hold_after_load_an_opened_day_and_a_window_pick(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    performance_history_seeded,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=VIEW[0], height=VIEW[1]) as probe:
        perf_module._open(probe, "/history")
        # The exits table, and its Detail header, is drawn once a day is opened.
        _assert_titles(
            probe, "history", "history after load", hosts=("History",), not_yet=frozenset({"Y16"})
        )
        perf_module._open_exit_day(probe)
        probe.page.wait_for_timeout(SETTLE_MS)
        _assert_titles(probe, "history", "history after opening a day", hosts=("History",))
        probe.page.click('#window-picker button[data-window="90"]')
        probe.page.wait_for_timeout(1200)
        _assert_titles(probe, "history", "history after picking 90d", hosts=("History",))


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_the_settings_titles_hold_in_the_rows_the_script_writes(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,  # noqa: F811
):
    with settings_module.settings_open(firefox, advisor_server, size=VIEW) as probe:
        settings_module.go_to(probe, "symphonies")
        probe.page.locator("#sym-list .sym-list-btn").first.click()
        probe.page.wait_for_selector(".override-grid .override-row")
        probe.page.wait_for_timeout(SETTLE_MS)
        _assert_titles(probe, "settings", "settings overrides", hosts=("Settings",))


# ---------------------------------------------------------------------------
# The AI Advisor
# ---------------------------------------------------------------------------


def test_the_overview_titles_hold_after_load_and_after_three_refreshes_of_its_runs(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    overview_module._prepare(tmp_path, monkeypatch, seed=overview_module._seed_titled_world)
    with open_probe(firefox, advisor_server, width=VIEW[0], height=VIEW[1]) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, "/ai-advisor")
        probe.page.wait_for_selector(
            '#autotune-runs-list [data-testid="autotune-run-row"]', state="attached"
        )
        probe.page.wait_for_timeout(SETTLE_MS)
        _assert_titles(probe, "overview", "overview after load", hosts=("Overview",))
        for refresh in range(3):
            probe.context.clock.run_for(16_000)
            probe.page.wait_for_timeout(SETTLE_MS)
            _assert_titles(
                probe, "overview", f"overview after refresh {refresh + 1}", hosts=("Overview",)
            )


def test_the_retirement_titles_hold_on_the_tile_labels_and_the_evidence_headings(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
):
    advisor_module._prepare(
        tmp_path,
        monkeypatch,
        world="empty",
        seed=advisor_module._retirement_with_cards_and_long_review,
    )
    with advisor_module._open(firefox, advisor_server, VIEW, "retirement") as opened:
        opened.page.evaluate(
            "() => document.querySelectorAll('#tab-panel-retirement details')"
            ".forEach((d) => { d.open = true; })"
        )
        opened.page.wait_for_timeout(SETTLE_MS)
        _assert_titles(opened.probe, "retirement", "retirement", hosts=("Retirement",))


@pytest.mark.parametrize("tab", ["strategy-builder", "frontrunner-builder"])
def test_the_card_tabs_titles_hold_on_the_stats_table_header_and_the_change_labels(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    tab,
):
    advisor_module._prepare_cards(tmp_path, monkeypatch, tab)
    host = {"strategy-builder": "Strategy Builder", "frontrunner-builder": "Frontrunner Builder"}[
        tab
    ]
    with advisor_module._open(firefox, advisor_server, VIEW, tab) as opened:
        _assert_titles(opened.probe, tab, tab, hosts=(host,))


@pytest.mark.parametrize("kind", ["swap", "logic"])
def test_the_result_labels_of_a_swap_and_a_logic_change_carry_their_lines(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    tmp_path,
    monkeypatch,
    kind,
):
    swap_logic_module._prepare(tmp_path, monkeypatch)
    host = {"swap": "Asset Swaps", "logic": "Logic Changes"}[kind]
    with swap_logic_module._evaluated(firefox, advisor_server, monkeypatch, kind, VIEW[0]) as probe:
        _assert_titles(probe, kind, f"{kind} result", hosts=(host,))
