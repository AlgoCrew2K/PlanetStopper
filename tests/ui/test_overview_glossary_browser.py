"""The Overview's Glossary and the titles its labels take from it, in a real browser.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_overview_glossary_browser.py``). Firefox headless
through Playwright, imported inside a fixture; a missing Playwright or Firefox fails with a message
naming it, never a skip.

Each case is one seeded world (``tests/ui/_page_explainers.py`` and
``tests/ui/_page_explainers_advisor.py``, written through the real writers) served by the app in
this process. The expected text of a line is read from ``tests/ui/_page_copy.py``.

Contract pinned for the Overview (the plan's AC-4):

* The Glossary is closed on load, named "Glossary", opens and closes with Enter and Space, hides its
  lines until it is opened and shows each one, in order and word for word, afterwards.
* A label whose whole text is a term, and each element that names a Glossary key, carries the whole
  line as its ``title``: the autotune headers and outcome words (rows the page script writes, and
  writes again), the numeric verification header and the sentiment chip. A title the page already
  carried is neither changed nor marked.
* An opened Glossary stays open, with the same lines, when the page refreshes its autotune runs.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from tests._advisor_overview_prism import prism_raw_response, store_prism_row
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

GLOSSARY = '#tab-panel-overview details[data-terms="glossary"]'
LINES = f"{GLOSSARY} > p[data-term]"
SCRIPT_REWRITE_MS = 400


def _line(key: str) -> str:
    found = [x for x in copy_fixture.glossary("Overview") if x["key"] == key]
    assert len(found) == 1, key
    return " ".join(found[0]["text"].split())


def _seed_titled_world():
    """Runs with different outcomes (so the outcome words are in rows) and a t-stat in one,
    the Market Prism read, and its verification row (so the numeric verification header shows)."""
    import database

    keys = adv.seed_autotune_runs(11, adopted_at=3)
    adv.save_run_like("adopted", keys[0], "2026-10-30T04:00:00", selection_tstat=2.1)
    store_prism_row()
    database.insert_advisor_observation(
        advisor_role="MARKET_PRISM_VERIFICATION",
        subject_type="portfolio",
        subject_id="global",
        verdict=None,
        raw_response={
            "run_id": prism_raw_response()["run_id"],
            "checks": [],
            "summary": {"n_checks": 0},
            "verdict": "no-numeric-claims",
        },
    )


def _prepare(tmp_path, monkeypatch, *, seed=None):
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    harness.seed_world("full", tmp_path, monkeypatch.setattr)
    if seed is not None:
        seed()


@contextmanager
def _open(firefox, server, size=(1440, 900)):  # noqa: F811
    with open_probe(firefox, server, width=size[0], height=size[1]) as probe:
        open_page(probe, "/ai-advisor")
        probe.page.wait_for_selector(
            '#autotune-runs-list [data-testid="autotune-run-row"]', state="attached"
        )
        probe.page.evaluate("() => document.fonts.ready.then(() => true)")
        probe.page.wait_for_timeout(SCRIPT_REWRITE_MS)
        yield probe
        assert probe.page_errors == [], probe.page_errors


def _require_glossary(page) -> None:
    """A missing Glossary fails here, by name, instead of as a timeout on its first use."""
    assert page.locator(GLOSSARY).count() == 1, "the Overview carries no Glossary"


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


def test_the_glossary_is_closed_named_and_opens_and_closes_with_enter_and_space(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch)
    with _open(firefox, advisor_server) as probe:
        page = probe.page
        _require_glossary(page)
        summary = page.locator(f"{GLOSSARY} > summary")
        assert page.locator(GLOSSARY).evaluate("(el) => el.open") is False, "open on load"
        assert summary.inner_text().strip() == "Glossary"
        before = page.locator(GLOSSARY).evaluate("(el) => el.getBoundingClientRect().height")
        summary.focus()
        page.keyboard.press("Enter")
        assert page.locator(GLOSSARY).evaluate("(el) => el.open") is True, "Enter did not open it"
        page.keyboard.press("Enter")
        assert page.locator(GLOSSARY).evaluate("(el) => el.open") is False, "Enter did not close it"
        page.keyboard.press("Space")
        assert page.locator(GLOSSARY).evaluate("(el) => el.open") is True, "Space did not open it"
        page.keyboard.press("Space")
        assert page.locator(GLOSSARY).evaluate("(el) => el.open") is False, "Space did not close it"
        after = page.locator(GLOSSARY).evaluate("(el) => el.getBoundingClientRect().height")
        assert abs(after - before) <= 1, "the closed Glossary changed its height"


def test_the_glossary_hides_its_lines_until_opened_and_shows_each_in_order_after(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch)
    expected = copy_fixture.glossary("Overview")
    with _open(firefox, advisor_server) as probe:
        page = probe.page
        _require_glossary(page)
        closed = page.evaluate(_SHOWN_TEXT_JS, LINES)
        assert len(closed) == len(expected) and not any(c["shown"] for c in closed), (
            "a line shows before the Glossary is opened"
        )
        page.click(f"{GLOSSARY} > summary")
        page.wait_for_timeout(100)
        read = page.evaluate(_SHOWN_TEXT_JS, LINES)
        assert [r["key"] for r in read] == [x["key"] for x in expected]
        for item, line in zip(read, expected, strict=True):
            assert item["shown"], f"{line['key']}: not visible after opening"
            assert item["text"] == " ".join(line["text"].split()), line["key"]
        page.click(f"{GLOSSARY} > summary")
        page.wait_for_timeout(100)
        assert not any(c["shown"] for c in page.evaluate(_SHOWN_TEXT_JS, LINES))


_TITLES_JS = """() => {
  const norm = (t) => String(t || '').replace(/\\s+/g, ' ').trim();
  const overview = document.querySelector('#tab-panel-overview');
  const heads = [...overview.querySelectorAll('#autotune-runs-list [role="columnheader"]')]
    .map((h) => ({ text: norm(h.textContent), title: h.getAttribute('title'),
                   marked: h.hasAttribute('data-glossary-titled') }));
  const outcome = [...overview.querySelectorAll('#autotune-runs-list .autotune-run-outcome *')]
    .map((e) => ({ text: norm(e.textContent), title: e.getAttribute('title'),
                   marked: e.hasAttribute('data-glossary-titled') }))
    .filter((e) => e.title);
  const one = (sel) => { const e = overview.querySelector(sel);
    return e ? { text: norm(e.textContent), title: e.getAttribute('title'),
                 marked: e.hasAttribute('data-glossary-titled') } : null; };
  return { heads, outcome, chip: one('[data-testid="prism-sentiment-chip"]'),
           verification: one('.prism-verification-header') };
}"""


def _assert_overview_titles(read: dict, when: str) -> None:
    heads = {h["text"]: h for h in read["heads"]}
    for header, key in (("Runs without adopting a tuned config", "O09a"), ("Sel t-stat", "O09c")):
        assert header in heads, f"{when}: no {header!r} header on the page"
        assert heads[header]["title"] and " ".join(heads[header]["title"].split()) == _line(key), (
            f"{when}: the {header!r} header carries {heads[header]['title']!r}"
        )
        assert heads[header]["marked"], f"{when}: the header's title is not marked as the script's"
    words = {"Fallback": "O10", "Frozen-eval": "O11"}
    for word, key in words.items():
        carriers = [
            e for e in read["outcome"] if e["text"].startswith(word) and e["title"] is not None
        ]
        assert carriers, f"{when}: no outcome element reading {word!r} carries a title"
        assert any(" ".join(e["title"].split()) == _line(key) for e in carriers), (
            f"{when}: no {word!r} element carries the line {key}: {carriers}"
        )
    assert read["chip"] and " ".join(read["chip"]["title"].split()) == _line("O16"), (
        f"{when}: the sentiment chip carries {read['chip']}"
    )
    assert read["verification"] and (
        " ".join(read["verification"]["title"].split()) == _line("O24")
    ), f"{when}: the numeric verification header carries {read['verification']}"


def test_the_headers_outcome_words_chip_and_verification_header_carry_their_glossary_line(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_titled_world)
    with _open(firefox, advisor_server) as probe:
        _assert_overview_titles(probe.page.evaluate(_TITLES_JS), "on load")


def test_rows_the_page_script_writes_again_take_their_titles_again(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    """The 15 s refresh replaces the runs table. The same markup is written again with every
    title the stamping script set removed, as a fresh answer's rows arrive without them."""
    _prepare(tmp_path, monkeypatch, seed=_seed_titled_world)
    with _open(firefox, advisor_server) as probe:
        for round_ in range(3):
            probe.page.evaluate(
                """() => {
                  const list = document.getElementById('autotune-runs-list');
                  list.innerHTML = list.innerHTML
                    .replace(/ data-glossary-titled=""/g, '')
                    .replace(/ title="[^"]*"/g, '');
                }"""
            )
            probe.page.wait_for_timeout(SCRIPT_REWRITE_MS)
            _assert_overview_titles(probe.page.evaluate(_TITLES_JS), f"after rewrite {round_ + 1}")


def test_a_title_the_page_already_carried_is_unchanged_and_never_marked(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_titled_world)
    with _open(firefox, advisor_server) as probe:
        facts = probe.page.evaluate(
            """() => ({
              dots: Object.fromEntries([...document.querySelectorAll('[role="tab"][data-tab]')]
                .map((b) => [b.getAttribute('data-tab'), (b.querySelector('.cap-tab-badge') || {}).title])
                .filter(([, t]) => t)),
              names: [...document.querySelectorAll('#autotune-runs-list .autotune-run-name')]
                .map((n) => ({ title: n.getAttribute('title'), marked: n.hasAttribute('data-glossary-titled'),
                               text: n.textContent.trim() })),
              score: (() => { const h = document.querySelector('#autotune-runs-list .autotune-row--head .autotune-run-score');
                              return h ? { title: h.getAttribute('title'), marked: h.hasAttribute('data-glossary-titled') } : null; })(),
              badges: [...document.querySelectorAll('.cap-tab-badge[title]')]
                .filter((b) => b.hasAttribute('data-glossary-titled')).length,
            })"""
        )
        assert facts["dots"] == adv.TAB_DOT_TITLES, facts["dots"]
        assert facts["badges"] == 0, "a tab dot's title was marked as the Glossary script's"
        assert facts["names"], "premise: the runs table has rows"
        for name in facts["names"]:
            assert name["title"] and not name["marked"], f"a symphony-name title changed: {name}"
        if facts["score"] is not None and facts["score"]["title"]:
            assert not facts["score"]["marked"], "the score header's own title was overwritten"


def test_an_opened_glossary_stays_open_with_the_same_lines_through_the_runs_refresh(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    _prepare(tmp_path, monkeypatch, seed=_seed_titled_world)
    with _open(firefox, advisor_server) as probe:
        page = probe.page
        _require_glossary(page)
        page.click(f"{GLOSSARY} > summary")
        before = [r["text"] for r in page.evaluate(_SHOWN_TEXT_JS, LINES)]
        # The next answer of the page's own 15 s refresh of its autotune runs.
        with page.expect_response("**/api/autotune-runs", timeout=25_000):
            pass
        page.wait_for_timeout(SCRIPT_REWRITE_MS)
        assert page.locator(GLOSSARY).evaluate("(el) => el.open") is True, "the refresh closed it"
        after = [r["text"] for r in page.evaluate(_SHOWN_TEXT_JS, LINES)]
        assert after == before and len(after) == len(copy_fixture.glossary("Overview"))
