"""The Glossaries of the Advisor's other tabs and the titles their labels take, in a real browser.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_other_tabs_glossary_browser.py``). Firefox
headless through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with
a message naming it, never a skip.

Each case is one seeded world (``tests/ui/_page_explainers.py`` and ``tests/ui/
_page_explainers_advisor.py``, written through the real writers) served by the app in this
process. The expected text of a line is read from ``tests/ui/_page_copy.py``.

Contract pinned for Correlations, Asset Swaps, Logic Changes, Chat, Strategy Builder and
Frontrunner Builder (the plan's AC-4):

* Each tab's Glossary is closed on load, named "Glossary", opens and closes with Enter and Space,
  hides its lines until it is opened and shows each one, in order and word for word, afterwards.
* The Strategy Builder's "Live Baseline" header and the Frontrunner cards' Calmar, CAGR, Max DD and
  Node count change labels carry the whole line of their Glossary as their title.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _page_copy as copy_fixture
from tests.ui import _page_explainers as harness
from tests.ui import _page_explainers_advisor as adv
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, empty directory, browser)
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
)

pytestmark = pytest.mark.browser

HOSTS = adv.OTHER_TAB_GLOSSARY_HOSTS

_SHOWN_TEXT_JS = """(selector) => [...document.querySelectorAll(selector)].map((el) => ({
  key: el.getAttribute('data-term'),
  text: (el.textContent || '').replace(/\\s+/g, ' ').trim(),
  shown: el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden'
         && !(() => {
           for (let n = el; n; n = n.parentElement) {
             const holder = n.parentElement;
             if (holder && holder.tagName === 'DETAILS' && !holder.open && n.tagName !== 'SUMMARY') return true;
           }
           return false;
         })(),
}))"""


def _selector(host: str) -> str:
    return f'#tab-panel-{adv.TAB_OF_GLOSSARY_HOST[host]} details[data-terms="glossary"]'


def _seed():
    adv.seed_strategy_builder_cards(3)
    adv.seed_frontrunner_proposals_varied(2)


def _prepare(tmp_path, monkeypatch):
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world("empty", tmp_path, monkeypatch.setattr)
    _seed()
    import advisors.asset_swap_engine as ase

    monkeypatch.setenv("ANTHROPIC_API_KEY", harness.PLACEHOLDER_KEY)
    monkeypatch.setattr(ase, "_has_composer_key", lambda: True)


@contextmanager
def _open(firefox, server, host, size=(1440, 900)):  # noqa: F811
    with open_probe(firefox, server, width=size[0], height=size[1]) as probe:
        open_page(probe, "/ai-advisor", fragment=f"#{adv.TAB_OF_GLOSSARY_HOST[host]}")
        probe.page.evaluate("() => document.fonts.ready.then(() => true)")
        probe.page.wait_for_timeout(400)
        yield probe
        assert probe.page_errors == [], probe.page_errors


def _is_open(page, host: str) -> bool:
    return page.locator(_selector(host)).evaluate("(el) => el.open")


@pytest.mark.parametrize("host", HOSTS)
def test_a_tab_glossary_is_closed_named_and_opens_and_closes_with_enter_and_space(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    host,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch)
    with _open(firefox, advisor_server, host) as probe:
        page = probe.page
        assert page.locator(_selector(host)).count() == 1, f"the {host} tab carries no Glossary"
        summary = page.locator(f"{_selector(host)} > summary")
        assert _is_open(page, host) is False, "open on load"
        assert summary.inner_text().strip() == "Glossary"
        summary.focus()
        page.keyboard.press("Enter")
        assert _is_open(page, host) is True, "Enter did not open it"
        page.keyboard.press("Enter")
        assert _is_open(page, host) is False, "Enter did not close it"
        page.keyboard.press("Space")
        assert _is_open(page, host) is True, "Space did not open it"
        page.keyboard.press("Space")
        assert _is_open(page, host) is False, "Space did not close it"


@pytest.mark.parametrize("host", HOSTS)
def test_a_tab_glossary_hides_its_lines_until_opened_and_shows_each_in_order_after(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    host,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch)
    lines = copy_fixture.glossary(host)
    with _open(firefox, advisor_server, host) as probe:
        page = probe.page
        lines_selector = f"{_selector(host)} > p[data-term]"
        closed = page.evaluate(_SHOWN_TEXT_JS, lines_selector)
        assert len(closed) == len(lines), (
            f"the tab holds {len(closed)} Glossary lines, not {len(lines)}"
        )
        assert not any(c["shown"] for c in closed), "a line shows before the Glossary is opened"
        page.click(f"{_selector(host)} > summary")
        page.wait_for_timeout(100)
        read = page.evaluate(_SHOWN_TEXT_JS, lines_selector)
        assert [r["key"] for r in read] == [x["key"] for x in lines]
        for item, line in zip(read, lines, strict=True):
            assert item["shown"], f"{line['key']}: not visible after opening"
            assert item["text"] == " ".join(line["text"].split()), line["key"]
        page.click(f"{_selector(host)} > summary")
        page.wait_for_timeout(100)
        assert not any(c["shown"] for c in page.evaluate(_SHOWN_TEXT_JS, lines_selector))


def _titles(page, selector: str) -> list:
    return page.eval_on_selector_all(
        selector,
        "(els) => els.map((e) => [e.textContent.replace(/\\s+/g, ' ').trim(), e.getAttribute('title')])",
    )


def _line_text(host: str, key: str) -> str:
    line = next(x for x in copy_fixture.glossary(host) if x["key"] == key)
    return " ".join(line["text"].split())


def test_the_live_baseline_header_carries_its_glossary_line_as_its_title(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch)
    with _open(firefox, advisor_server, "Strategy Builder") as probe:
        found = _titles(probe.page, '[data-testid="baseline-col-header"]')
    assert found, "premise: a survivor with a live baseline prints the column"
    wanted = _line_text("Strategy Builder", "B07t")
    problems = [
        f"{t[0]!r}: title {t[1]!r}" for t in found if " ".join((t[1] or "").split()) != wanted
    ]
    assert not problems, "\n".join(problems)


def test_the_frontrunner_change_labels_carry_their_glossary_lines_as_their_titles(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch)
    wanted = {
        "Calmar": _line_text("Frontrunner Builder", "X11a"),
        "CAGR": _line_text("Frontrunner Builder", "X11b"),
        "Max DD": _line_text("Frontrunner Builder", "X11c"),
        "Node count change": _line_text("Frontrunner Builder", "X12"),
    }
    with _open(firefox, advisor_server, "Frontrunner Builder") as probe:
        found = _titles(probe.page, '[data-testid="frontrunner-proposal-card"] [data-glossary]')
    seen = {}
    for label, title in found:
        if label in wanted:
            seen.setdefault(label, []).append(" ".join((title or "").split()))
    assert set(seen) == set(wanted), f"labels with no Glossary key: {set(wanted) - set(seen)}"
    problems = [
        f"{label!r}: title {title!r}"
        for label, titles in seen.items()
        for title in titles
        if title != wanted[label]
    ]
    assert not problems, "\n".join(problems)
    assert all(len(titles) == 2 for titles in seen.values()), (
        "each label sits on both cards: " + str({k: len(v) for k, v in seen.items()})
    )
