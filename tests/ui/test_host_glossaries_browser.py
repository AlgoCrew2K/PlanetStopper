"""The labels of the dashboard, Performance, History and Settings carry their Glossary line as a title.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_host_glossaries_browser.py``). Firefox headless
through Playwright; the app in this process on the per-test database, seeded through the real
writers, the clocks fixed as in the sibling modules. ``static/glossary.js`` stamps a ``title`` on
every label whose text is a Glossary line's lead term and on every element carrying
``data-glossary="<key>"``; this module reads the titles on the live page, the rows the page scripts
write included (Table G3 names them: the risk metric rows, the precondition headers and verdict
chips, the Settings badge words), and again after the page's own refresh has rewritten them.

* every label Table G3 names for the host has ``title`` equal to the whole text of its Glossary
  line (whitespace collapsed);
* a label that carried a title at the reference keeps it, and the script marks no element it did not
  title itself.

Provenance of the preconditions body: the real ``/api/guard-alpha-preconditions`` answer over the
captured shadow rows with each symphony's shadow verdict rotated through the five verdicts (named
field edit), so each verdict chip word is on the page.
"""

from __future__ import annotations

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_copy as copy
from tests.ui import _performance_page as perf
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_probe,
)
from tests.ui.test_page_explainers_dashboard_browser import (  # noqa: F401  (fixture and helpers)
    POLL_MS,
    _Dashboard,
)
from tests.ui.test_page_explainers_dashboard_browser import (  # noqa: F401  (the dashboard's world)
    seeded as dashboard_seeded,
)
from tests.ui.test_page_explainers_performance_history_browser import (  # noqa: F401
    _open,
    _open_exit_day,
    seeded,
)
from tests.ui.test_page_explainers_settings_browser import (  # noqa: F401
    go_to,
    settings_open,
    world,
)

pytestmark = pytest.mark.browser

VERDICTS = (
    "SUFFICIENT_MET",
    "SUFFICIENT_LIKELY",
    "NOT_MET",
    "NEGATIVE_EDGE",
    "INSUFFICIENT_DATA",
)
VERDICT_LINE = {
    "SUFFICIENT_MET": "F19a",
    "SUFFICIENT_LIKELY": "F19b",
    "NOT_MET": "F19c",
    "NEGATIVE_EDGE": "F19d",
    "INSUFFICIENT_DATA": "F19e",
}
LABELS_JS = """
(selector) => [...document.querySelectorAll(selector)].map((e) => ({
  text: (e.textContent || '').replace(/\\s+/g, ' ').trim(),
  title: e.getAttribute('title'),
  key: e.getAttribute('data-glossary'),
  cls: String(e.className),
}))
"""
MARKED_JS = """
() => [...document.querySelectorAll('[data-glossary-titled]')].map((e) => ({
  tag: e.tagName, cls: String(e.className), text: (e.textContent || '').trim().slice(0, 40),
}))
"""


def _line(host: str, key: str) -> str:
    text = next(x["text"] for x in copy.glossary(host) if x["key"] == key)
    return " ".join(text.split())


def _titles(probe, selector: str) -> list[dict]:
    return probe.page.evaluate(LABELS_JS, selector)


def _expect(probe, host: str, selector: str, wanted: dict[str, str], where: str) -> None:
    """Each label text in ``wanted`` is on the page under ``selector`` and its title is the line."""
    found = _titles(probe, selector)
    problems = []
    for text, key in wanted.items():
        matches = [f for f in found if f["text"] == text or f["text"].startswith(text + " ")]
        if not matches:
            problems.append(f"{where}: no label {text!r} under {selector}")
            continue
        for match in matches:
            if match["title"] != _line(host, key):
                problems.append(
                    f"{where}: {text!r} has title {match['title']!r}, not the {key} line"
                )
    assert not problems, "\n".join(problems)


def _performance(probe) -> None:
    body = perf.get_real("/api/guard-alpha-preconditions")["json"]
    for index, entry in enumerate(body["symphonies"].values()):
        entry["shadow"]["verdict"] = VERDICTS[index % len(VERDICTS)]
    _open(probe, "/performance", preconditions=(200, body))


# ---------------------------------------------------------------------------
# The dashboard
# ---------------------------------------------------------------------------

DASHBOARD_LABELS = (
    (".guard-alpha-label", {"Guard effect on portfolio": "D02"}),
    ("#portfolio-strip .vs-row-label", {"Max DD": "D17", "Ann. vol": "D18"}),
    ('[data-testid="mini-stat"] .mini-stat-label', {"Tracked": "D20t", "Armed": "D20t"}),
    ('[data-testid="mini-stat"] .mini-stat-label', {"Triggered": "D20t"}),
    ('[data-testid="mdd-lifetime-scalar"]', {"Lifetime Max DD": "D19"}),
)


def _expect_dashboard(probe, where: str) -> None:
    for selector, wanted in DASHBOARD_LABELS:
        _expect(probe, "/", selector, wanted, where)


def test_the_dashboard_labels_carry_their_glossary_line_as_a_title(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    dashboard_seeded,
):
    dashboard_seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, 1440, al.VIEWPORT_HEIGHT) as probe:
        probe.page.wait_for_timeout(400)
        _expect_dashboard(probe, "after load")


def test_the_dashboard_labels_keep_their_titles_after_three_polls_rewrite_the_rows(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    dashboard_seeded,
):
    dashboard_seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, 1440, al.VIEWPORT_HEIGHT) as probe:
        for poll in range(3):
            probe.page.clock.run_for(POLL_MS)
            probe.page.wait_for_timeout(600)
            _expect_dashboard(probe, f"after poll {poll + 1}")


def test_a_label_that_carried_a_title_keeps_it_and_the_script_marks_only_what_it_titled(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    dashboard_seeded,
):
    dashboard_seeded("dashboard_full")
    with _Dashboard(firefox, advisor_server, 1440, al.VIEWPORT_HEIGHT) as probe:
        probe.page.wait_for_timeout(400)
        marks = _titles(probe, ".info-mark")
        marked = probe.page.evaluate(MARKED_JS)
    assert marks and all(m["title"] for m in marks), "premise: the info marks carry titles"
    glossary_texts = {" ".join(x["text"].split()) for x in copy.glossary("/")}
    assert not [m for m in marks if m["title"] in glossary_texts], "an info mark was retitled"
    assert marked, "premise: the script titled some labels"
    assert not [m for m in marked if "info-mark" in m["cls"]], marked


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------

METRIC_ROWS = {
    "Max DD": "F15a",
    "Calmar": "F15b",
    "Annualized volatility": "F15c",
    "Volatility reduction": "F15d",
    "Total return": "F15e",
    "Annualized return (CAGR)": "F15f",
    "Up days": "F15g",
}


def _expect_performance(probe, where: str) -> None:
    _expect(
        probe,
        "Performance",
        ".hp-fig-label",
        {"Sharpe improvement": "F07", "Max DD reduction": "F08", "Sortino improvement": "F09"},
        where,
    )
    _expect(probe, "Performance", "#metrics-tbody tr td:first-child", METRIC_ROWS, where)
    _expect(probe, "Performance", "th", {"Delta (DD and vol in %)": "F15h"}, where)
    _expect(
        probe,
        "Performance",
        '[data-testid="guard-alpha-preconditions-table"] th',
        {
            "Sample": "F17a",
            "ρ": "F17b",
            "95% CI": "F17c",
            "Daily Sharpe": "F17d",
            "N": "F17e",
        },
        where,
    )
    _expect(
        probe,
        "Performance",
        '[data-testid="exit-turnover-table"] th',
        {"Est. annual trading-cost drag": "F24"},
        where,
    )


def test_the_performance_labels_and_script_written_rows_carry_their_glossary_line_as_a_title(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=1440, height=al.VIEWPORT_HEIGHT) as probe:
        _performance(probe)
        probe.page.wait_for_timeout(500)
        _expect_performance(probe, "after load")
        chips = _titles(probe, ".precond-verdict-chip")
        assert len(chips) >= 5, f"premise: {len(chips)} verdict chips on the page"
        shown = set()
        for chip in chips:
            kind = next((v for v in VERDICTS if v.lower().replace("_", "-") in chip["cls"]), None)
            assert kind, f"a chip of an unknown verdict: {chip['cls']}"
            shown.add(kind)
            assert chip["title"] == _line("Performance", VERDICT_LINE[kind]), (
                f"the {kind} chip has title {chip['title']!r}"
            )
        assert shown == set(VERDICTS), f"premise: the page shows verdicts {sorted(shown)}"


def test_the_performance_titles_survive_a_window_pick_that_rewrites_the_metric_rows(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=1440, height=al.VIEWPORT_HEIGHT) as probe:
        _performance(probe)
        for token in ("1y", "60"):
            probe.page.click(f'[data-testid="window-btn-{token}"]')
            probe.page.wait_for_timeout(1200)
            _expect_performance(probe, f"after picking {token}")


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------


def _expect_history(probe, where: str) -> None:
    _expect(probe, "History", ".hp-fig-label", {"Dollar result": "Y05", "Win rate": "Y07"}, where)
    _expect(
        probe,
        "History",
        "th",
        {"Detail": "Y16", "Wins": "Y13", "Avg per exit": "Y13"},
        where,
    )


def test_the_history_labels_carry_their_glossary_line_as_a_title_before_and_after_a_pick(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    seeded,  # noqa: F811
):
    with open_probe(firefox, advisor_server, width=1440, height=al.VIEWPORT_HEIGHT) as probe:
        _open(probe, "/history")
        probe.page.wait_for_timeout(400)
        _expect_history(probe, "after load")
        _open_exit_day(probe)
        probe.page.wait_for_timeout(400)
        _expect_history(probe, "after opening a day")
        probe.page.click('#window-picker button[data-window="90"]')
        probe.page.wait_for_timeout(1200)
        _expect_history(probe, "after picking 90d")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_the_settings_badge_words_carry_their_glossary_lines_as_titles(
    firefox,  # noqa: F811
    advisor_server,  # noqa: F811
    world,  # noqa: F811
):
    with settings_open(firefox, advisor_server, size=(1440, al.VIEWPORT_HEIGHT)) as probe:
        go_to(probe, "symphonies")
        probe.page.locator("#sym-list .sym-list-btn").first.click()
        probe.page.wait_for_selector(".override-grid .override-row")
        probe.page.wait_for_timeout(400)
        locks = _titles(probe, ".lock-btn")
        overrides = _titles(probe, ".override-label-override")
        heading = probe.page.evaluate(
            "() => [...document.querySelectorAll('*')].filter((e) => e.children.length === 0"
            " && e.textContent.trim() === 'No longer held')"
            ".map((e) => e.getAttribute('title'))"
        )
    assert locks and overrides, "premise: the symphony's override rows carry both words"
    line = _line("Settings", "S15")
    assert {entry["title"] for entry in locks} == {line}, locks[:2]
    assert {entry["title"] for entry in overrides} == {line}, overrides[:2]
    assert heading and all(t == _line("Settings", "S16") for t in heading), heading
