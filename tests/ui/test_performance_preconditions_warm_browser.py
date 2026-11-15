"""The Performance page stays responsive while the preconditions route runs, in a real browser.

Browser-only (marker ``browser``, deselected by default; run with ``python -m pytest -m browser
-n 0 tests/ui/test_performance_preconditions_warm_browser.py``). Firefox headless through
Playwright, imported by the shared ``firefox`` fixture inside its body so collection works on a
host without Playwright; never skips.

The page is the real ``GET /performance`` served by the app in this process on a real threaded
server (tests/ui/_browser_server.py), over the excerpt world of
tests/app/_preconditions_replay_world.py: the preconditions route resolves a real cache file (the
71 MB production-sized one, written at test time and never committed, for the first test), and
every answer is the real route's. Requests the page sends anywhere but the local server (the
Chart.js CDN tag) are refused.

* First load, warm: the page's own ``/api/performance`` request, read from its resource timing,
  completes within the same route's alone time plus half a second, the four headline figures are
  drawn (a figure may read "--" only when the answer itself says the history is insufficient), and
  the preconditions panel shows exactly the rows and verdicts the route's body holds.
* Two 60 s polls with nothing written: no text of the headline or the preconditions panel changes.
  The browser clock is installed, so a poll fires only when the test runs it.
"""

from __future__ import annotations

import json
import time

import pytest

from tests.app._preconditions_replay_world import (
    FILE_DATE,
    MEGABYTE,
    ROUTE,
    chdir_to_tmp,  # noqa: F401  (fixture)
    fetch,
    generate_file,
    median_of,
    server,  # noqa: F401  (fixture)
    standard,
)
from tests.ui.test_dashboard_charts_browser import firefox  # noqa: F401  (module-scoped fixture)

pytestmark = [pytest.mark.browser, pytest.mark.usefixtures("chdir_to_tmp")]

PERFORMANCE = "/api/performance?scope=aggregate&days=60d"
PAGE_TIMEOUT_MS = 60_000
HELD_UP_MARGIN_SECONDS = 0.5
POLL_MS = 60_000
ROW = '[data-testid="guard-alpha-preconditions-row"]'

READ_PANEL = """
() => ({
  headline: ['guard-alpha-value', 'sharpe-delta-value', 'mdd-reduction-value', 'sortino-delta-value']
    .map((id) => (document.getElementById(id) || {}).textContent),
  rows: Array.from(document.querySelectorAll('[data-testid="guard-alpha-preconditions-row"]'))
    .map((tr) => Array.from(tr.children).map((td) => td.textContent.trim())),
  verdicts: Array.from(document.querySelectorAll('[data-testid="guard-alpha-preconditions-row"] .precond-verdict-chip'))
    .map((chip) => chip.textContent.trim()),
  stamp: (document.getElementById('performance-data-as-of') || {}).textContent || '',
})
"""

PERFORMANCE_TIMINGS = """
() => performance.getEntriesByType('resource')
  .filter((e) => e.name.includes('/api/performance?'))
  .map((e) => e.responseEnd - e.requestStart)
"""


@pytest.fixture(scope="module")
def production_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("browser_sized") / "history.json"
    generate_file(path, target_bytes=71 * MEGABYTE)
    yield path
    path.unlink(missing_ok=True)


@pytest.fixture
def page(firefox, server):
    context = firefox.new_context(viewport={"width": 1440, "height": 900})
    context.route(
        "**/*",
        lambda route: (
            route.continue_() if route.request.url.startswith(server.base_url) else route.abort()
        ),
    )
    page = context.new_page()
    page.set_default_timeout(PAGE_TIMEOUT_MS)
    try:
        yield page
    finally:
        context.close()


def expected_chip_texts(body: dict) -> list[str]:
    """The verdict chips the panel draws for ``body``: a symphony with no replay sample has no
    Replay row (a cold replay row reads INSUFFICIENT_DATA with no observation), every symphony
    has a Shadow row."""
    texts = []

    def chip(verdict: str) -> str:
        words = verdict.replace("_", " ").lower()
        return words[:1].upper() + words[1:]

    for entry in body["symphonies"].values():
        replay = entry["replay"]
        if not (replay["verdict"] == "INSUFFICIENT_DATA" and replay["n_obs"] == 0):
            texts.append(chip(replay["verdict"]))
        texts.append(chip(entry["shadow"]["verdict"]))
    return texts


def wait_for_panel(page, body: dict) -> None:
    rows = len(expected_chip_texts(body))
    page.wait_for_function(
        f"document.querySelectorAll('{ROW}').length === {rows}", timeout=PAGE_TIMEOUT_MS
    )
    page.wait_for_function(
        "(document.getElementById('performance-data-as-of') || {}).textContent.trim() !== ''",
        timeout=PAGE_TIMEOUT_MS,
    )


def test_the_headline_and_the_panel_fill_while_the_preconditions_request_is_warm(
    monkeypatch, page, server, production_file
):
    world = standard(monkeypatch, file=False)
    world.place_file(FILE_DATE, production_file)
    assert fetch(server).status == 200  # the cold request: after this the daemon is warm
    alone = median_of(3, lambda: fetch(server, PERFORMANCE).seconds)
    body = json.loads(fetch(server, ROUTE).data)
    performance_body = json.loads(fetch(server, PERFORMANCE).data)

    page.goto(server.base_url + "/performance", wait_until="domcontentloaded")
    wait_for_panel(page, body)

    timings = page.evaluate(PERFORMANCE_TIMINGS)
    assert timings, "the page never requested /api/performance"
    assert max(timings) / 1000 <= alone + HELD_UP_MARGIN_SECONDS, (
        f"the page's /api/performance took {max(timings) / 1000:.2f} s; alone it takes "
        f"{alone:.2f} s"
    )
    read = page.evaluate(READ_PANEL)
    if not performance_body.get("insufficient_history"):
        assert "--" not in read["headline"], f"a headline figure is not drawn: {read['headline']}"
    assert read["verdicts"] == expected_chip_texts(body), "the panel differs from the route's body"


def test_two_sixty_second_polls_with_nothing_written_change_no_text(monkeypatch, page, server):
    standard(monkeypatch)
    assert fetch(server).status == 200  # warm
    body = json.loads(fetch(server, ROUTE).data)
    page.context.clock.install()
    seen = {"preconditions": 0, "performance": 0}

    def count(response):
        if "/api/guard-alpha-preconditions" in response.url:
            seen["preconditions"] += 1
        elif "/api/performance?" in response.url:
            seen["performance"] += 1

    page.on("response", count)
    page.goto(server.base_url + "/performance", wait_until="domcontentloaded")
    wait_for_panel(page, body)
    first = page.evaluate(READ_PANEL)
    for poll in (1, 2):
        before = dict(seen)
        page.context.clock.run_for(POLL_MS)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (
            seen["preconditions"] > before["preconditions"]
            and seen["performance"] > before["performance"]
        ):
            page.wait_for_timeout(100)
        assert seen["preconditions"] > before["preconditions"], (
            f"poll {poll}: no preconditions request"
        )
        assert seen["performance"] > before["performance"], f"poll {poll}: no performance request"
        again = page.evaluate(READ_PANEL)
        assert again["headline"] == first["headline"], f"poll {poll} changed a headline figure"
        assert again["rows"] == first["rows"], f"poll {poll} changed the preconditions panel"
