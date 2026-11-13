"""Every host in every seeded world, read in a real browser: copy, layout and phone width together.

Browser-only (marker ``browser``; run with
``python -m pytest -m browser -n 0 tests/ui/test_page_explainers_cross_host_browser.py``). Firefox
headless through Playwright; the app in this process on the per-test database, seeded through the
real writers (``tests/ui/_page_explainers.seed_world``); the app and browser clocks fixed. Never
skips. The expected text of a row comes from ``tests/fixtures/page_explainers/copy.json`` through
``tests/ui/_page_copy.py``; the reference figures from ``ref_capture.json``.

Scenarios swept over the (world, host) pairs of ``harness.WORLD_HOST_PAIRS``:

* each description shows, in full, with its words, at 1440 and at 390;
* S-2.12: every disclosure is closed on load, named "What the terms mean" and opens and closes by
  Enter and by Space; S-2.14 on Overview and Performance; S-2.18 with every script refused;
* S-3.11: every template-built reworded label shows its new words with every script refused;
* S-5.4 and S-5.10: honesty texts equal the reference's, a missing figure leaks no placeholder;
* the type contract (light and dark) at 1440 and the document no wider than the viewport, the
  empty states included;
* at 1000, 390 and 320 the page is no wider than the viewport, and at 390 no added text is under
  11 px and opening then closing each Glossary restores the page's height.

Recorded against the host cycles' own tests (same assertion, same world): S-2.12 and S-2.14 on `/`
(``test_page_explainers_dashboard_browser.py``).
"""

from __future__ import annotations

import json
import os
import re

import pytest

from tests.app.conftest import _reset_scheduler_run_state  # noqa: F401  (autouse, health state)
from tests.ui import _alignment as al
from tests.ui import _page_copy as copy
from tests.ui import _page_explainers as harness
from tests.ui import _page_explainers_advisor as adv
from tests.ui import _performance_page as perf
from tests.ui._shell_support import (  # noqa: F401  (fixtures: overrides, server, browser)
    WIDE,
    _empty_post_mortem_directory,
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_server,
    firefox,
    open_page,
    open_probe,
)
from tests.ui.test_page_copy_performance_history import warm_market_calendar
from tests.ui.test_page_explainers_cross_host import new_alternatives, page_of, slotted, tidy
from tests.ui.test_page_explainers_dashboard_browser import (
    seeded,  # noqa: F401  (the seeding fixture)
)
from tests.ui.test_page_explainers_unchanged_browser import (
    _relabel_matchers,
    figure_differences,
)

pytestmark = pytest.mark.browser

MIN_TEXT_PX = 11
# The phone the narrow rules are read at: 390 by 844, and below it 320 and the 1000 px boundary.
PHONE = (390, al.NARROW_VIEWPORT_HEIGHT[390])
SUMMARY_TEXT = "What the terms mean"
PAIRS = harness.WORLD_HOST_PAIRS
PAIR_IDS = [f"{w}-{h.strip('/') or 'dashboard'}" for w, h in PAIRS]
HONESTY = re.compile(r"basis|window|as-?of|caveat|exclu|stamp|note|label", re.I)
LEAK = re.compile(r"\b(NaN|undefined|Infinity|null|None)\b|-inf\b")
ABSENT_WITH_SECTION = {"B07", "B10"}

EXPLAINERS_JS = r"""
() => [...document.querySelectorAll('[data-explainer]')].map((el) => {
  const cs = getComputedStyle(el), r = el.getBoundingClientRect();
  return {
    key: el.getAttribute('data-explainer'),
    text: (el.innerText || '').replace(/\s+/g, ' ').trim(),
    shown: el.getClientRects().length > 0 && cs.visibility !== 'hidden',
    title: el.getAttribute('title') || '', aria: el.getAttribute('aria-label') || '',
    ellipsis: cs.textOverflow === 'ellipsis',
    clippedX: el.scrollWidth > el.clientWidth + 1,
    clippedY: cs.overflow !== 'visible' && el.scrollHeight > el.clientHeight + 1,
    left: r.left, right: r.right, vw: innerWidth,
    inCvar: !!el.closest('#cvar-diagnostic-panel'),
    // The detail panel is a drawer off the screen until a card is opened (its own host cycle opens it).
    inDrawer: !!el.closest('#detail-panel'),
    sectionHidden: (() => {
      const sec = el.closest('.hp-panel,.hero-section,.dash-panel,.adv-panel,.adv-card,.sym-card,'
          + '.settings-section,[data-testid^="tab-panel-"],#cvar-diagnostic-panel');
      return !!sec && sec.getClientRects().length === 0;
    })(),
  };
})
"""

ADDED = "[data-explainer], [data-explainer] *, [data-terms] > summary, [data-term]"
TYPE_JS = r"""
(added) => {
  const shown = (el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
  const own = [...document.querySelectorAll('.page-wrap *')].filter((el) =>
      shown(el) && [...el.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim())
      && !el.matches(added));
  const sizes = new Set(own.map((el) => getComputedStyle(el).fontSize));
  const colors = new Set(own.map((el) => getComputedStyle(el).color));
  const bad = [];
  for (const el of document.querySelectorAll(added)) {
    if (!shown(el) || !(el.textContent || '').trim()) continue;
    const cs = getComputedStyle(el);
    const name = (el.getAttribute('data-explainer') || el.getAttribute('data-term') || el.tagName);
    if (![400, 500].includes(Number(cs.fontWeight))) bad.push([name, 'weight', cs.fontWeight]);
    if (cs.textTransform === 'uppercase') bad.push([name, 'upper']);
    if (!sizes.has(cs.fontSize)) bad.push([name, 'size', cs.fontSize]);
    if (!colors.has(cs.color)) bad.push([name, 'color', cs.color]);
  }
  return { bad, added: document.querySelectorAll(added).length };
}
"""

SIZES_JS = r"""
(selector) => [...document.querySelectorAll(selector)]
  .filter((el) => el.getClientRects().length > 0 && (el.textContent || '').trim())
  .map((el) => [el.getAttribute('data-explainer') || el.getAttribute('data-term') || 'summary',
                parseFloat(getComputedStyle(el).fontSize)])
  .filter(([, px]) => px < 11)
"""

DISCLOSURES_JS = r"""
() => [...document.querySelectorAll('details[data-terms]')]
  .filter((d) => d.getClientRects().length > 0)
  .map((d) => ({
    key: d.getAttribute('data-terms'), open: d.open,
    name: (d.querySelector(':scope > summary') || {textContent: ''}).textContent.replace(/\s+/g, ' ').trim(),
  }))
"""

DOC_HEIGHT_JS = "() => Math.max(document.documentElement.scrollHeight, document.body.scrollHeight)"

TABS_JS = (
    "() => [...document.querySelectorAll('[role=\"tab\"][data-tab]')]"
    ".map((t) => t.getAttribute('data-tab'))"
)
SECTIONS_JS = (
    "() => [...document.querySelectorAll('.sn-btn[data-section]')]"
    ".map((t) => t.getAttribute('data-section'))"
)

_READ: dict = {}


@pytest.fixture(scope="module")
def ref() -> dict:
    path = harness.REF_CAPTURE_PATH
    if not path.is_file():
        pytest.fail(f"no reference capture at {path}: the producer has not been run")
    return json.loads(path.read_text(encoding="utf-8"))


def _tabs(probe, host: str) -> list[str]:
    """The states of a host that show different content: an Advisor tab, a Settings section."""
    if host == "/ai-advisor":
        return probe.page.evaluate(TABS_JS)
    if host == "/settings":
        return probe.page.evaluate(SECTIONS_JS)
    return [""]


def _show(probe, tab: str) -> None:
    if not tab:
        return
    if tab in ("master", "algorithm", "credentials", "symphonies", "maintenance"):
        probe.page.click(f'.sn-btn[data-section="{tab}"]')
    else:
        probe.page.click(f'[role="tab"][data-tab="{tab}"]')
    probe.page.wait_for_timeout(150)


def reading(firefox, server, seeded, world, host, width, *, theme=None):  # noqa: F811
    """Facts of ``host`` in ``world`` at ``width``; one browser visit per distinct request."""
    key = (world, host, width, theme)
    if key in _READ:
        return _READ[key]
    seeded(world)
    height = al.NARROW_VIEWPORT_HEIGHT.get(width, 900)
    with open_probe(firefox, server, width=width, height=height, theme=theme) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, host)
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        facts: dict = {"states": {}}
        facts["figures"] = [
            [k, harness.mask_clock_text(t)] for k, t in probe.page.evaluate(harness.FIGURES_JS)
        ]
        facts["type"] = probe.page.evaluate(TYPE_JS, ADDED)
        for tab in _tabs(probe, host):
            _show(probe, tab)
            state = {
                "explainers": probe.page.evaluate(EXPLAINERS_JS),
                "scroll": probe.page.evaluate(
                    "() => [document.documentElement.scrollWidth, innerWidth]"
                ),
            }
            facts["states"][tab] = state
        facts["page_errors"] = list(probe.page_errors)
    _READ[key] = facts
    return facts


# Hosts whose descriptions this plan has already changed in production: their rows are read from the
# plan's Copy table A (a removed row reads None), every other host from the reference rows.
PLAN_HOSTS = {
    "Advisor",
    "Overview",
    "Retirement",
    "Correlations",
    "Asset Swaps",
    "Logic Changes",
    "Chat",
    "Strategy Builder",
    "Frontrunner Builder",
    # The dashboard cycle: the dashboard, its detail panel, Performance, History, Settings and the
    # settings modal read this plan's Copy table A too.
    "/",
    "detail panel",
    "Performance",
    "History",
    "Settings",
    "settings modal",
}


def _plan_row(key: str) -> dict | None:
    try:
        row = copy.a_row(key)
    except KeyError:
        return None
    return row if copy.host_of(row) in PLAN_HOSTS else None


def _removed_by_plan(key: str) -> bool:
    return _plan_row(key) is not None and copy.a_text(key) is None


def _expected_pattern(key: str) -> re.Pattern:
    plan = _plan_row(key)
    record = plan if plan is not None else copy.row(key, "A")
    text = copy.a_text(key) if plan is not None else copy.text(key, "A")
    if slotted(record):
        pieces = [re.escape(p) for p in re.split(r"\{[a-z_]+\}", text)]
        return re.compile(r"\s*".join(p if i == 0 else r"\S+" + p for i, p in enumerate(pieces)))
    return re.compile(re.escape(tidy(text)))


def _is_hidden_by_design(world: str, host: str, key: str, item: dict) -> bool:
    if item["inCvar"] and world == "empty":
        return True
    return world == "empty" and key in ABSENT_WITH_SECTION


# ---------------------------------------------------------------------------
# S-1.15, S-1.19: every description shows, with its words, and none is carried by an attribute
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("world", "host"), PAIRS, ids=PAIR_IDS)
def test_every_description_of_the_host_is_visible_with_its_words_and_not_only_an_attribute(
    firefox,
    advisor_server,
    seeded,
    world,
    host,  # noqa: F811
):
    facts = reading(firefox, advisor_server, seeded, world, host, WIDE[0])
    seen: dict[str, dict] = {}
    for state in facts["states"].values():
        for item in state["explainers"]:
            if item["shown"] or item["key"] not in seen:
                seen[item["key"]] = item
    problems = []
    known = {r["key"] for r in copy.rows("A")}
    for key, item in seen.items():
        if key not in known:
            problems.append(f"{key}: not a Copy table A row")
            continue
        if _removed_by_plan(key):
            if item["shown"]:
                problems.append(f"{key}: shown, though the plan removes it")
            continue
        if not item["shown"]:
            if not (item["sectionHidden"] or _is_hidden_by_design(world, host, key, item)):
                problems.append(f"{key}: not shown though its section is")
            continue
        if not item["text"]:
            problems.append(f"{key}: empty (text only in an attribute?)")
        elif not _expected_pattern(key).search(tidy(item["text"])):
            problems.append(f"{key}: reads {item['text']!r}")
    own = [
        r["key"]
        for r in copy.rows("A")
        if page_of(r["host_place"]) == host
        and r["builder"] == "template"
        and r["kind"] != "kept"
        and not _removed_by_plan(r["key"])
    ]
    # Strategy Builder's two result sections need observations no harness world writes.
    optional = ABSENT_WITH_SECTION if world == "empty" else {"B07", "B10"}
    absent = [k for k in own if k not in seen and k not in optional]
    problems += [f"{k}: no element on the page" for k in absent]
    assert not problems, f"{world} {host}:\n" + "\n".join(problems)
    assert seen, f"premise: {host} carries descriptions"
    assert facts["page_errors"] == []


@pytest.mark.parametrize(("world", "host"), PAIRS, ids=PAIR_IDS)
def test_a_description_is_whole_on_a_phone_never_clipped_or_ellipsed(
    firefox,
    advisor_server,
    seeded,
    world,
    host,  # noqa: F811
):
    facts = reading(firefox, advisor_server, seeded, world, host, PHONE[0])
    problems = []
    for tab, state in facts["states"].items():
        for item in state["explainers"]:
            if not item["shown"] or item["inDrawer"]:
                continue
            where = f"{tab or host} {item['key']}"
            if item["ellipsis"] or item["clippedX"] or item["clippedY"]:
                problems.append(f"{where}: cut off")
            if item["left"] < -0.5 or item["right"] > item["vw"] + 0.5:
                problems.append(f"{where}: outside the viewport")
    assert not problems, "\n".join(problems)


# ---------------------------------------------------------------------------
# S-6.x: layout at 1440 and 1240
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("world", "host"), PAIRS, ids=PAIR_IDS)
def test_the_document_is_no_wider_than_the_viewport_at_1440(
    firefox,
    advisor_server,
    seeded,
    world,
    host,  # noqa: F811
):
    facts = reading(firefox, advisor_server, seeded, world, host, WIDE[0])
    problems = [
        f"{tab or host}: {scroll} wide in {vw}"
        for tab, state in facts["states"].items()
        for scroll, vw in [state["scroll"]]
        if scroll > vw
    ]
    assert not problems, "\n".join(problems)


@pytest.mark.parametrize("theme", [None, "dark"], ids=["light", "dark"])
@pytest.mark.parametrize(("world", "host"), [p for p in PAIRS if p[0] == "full"], ids=lambda p: p)
def test_added_text_uses_only_existing_weights_sizes_and_colors_in_both_themes(
    firefox,
    advisor_server,
    seeded,
    world,
    host,
    theme,  # noqa: F811
):
    facts = reading(firefox, advisor_server, seeded, world, host, WIDE[0], theme=theme)
    assert facts["type"]["added"] > 0, "premise: the page carries added text"
    assert facts["type"]["bad"] == [], f"{host} {theme}: {facts['type']['bad'][:6]}"


# ---------------------------------------------------------------------------
# S-7.x: the phone
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("width", al.NARROW)
@pytest.mark.parametrize(("world", "host"), PAIRS, ids=PAIR_IDS)
def test_the_page_is_no_wider_than_the_phone_on_every_tab(
    firefox,
    advisor_server,
    seeded,
    world,
    host,
    width,  # noqa: F811
):
    facts = reading(firefox, advisor_server, seeded, world, host, width)
    problems = [
        f"{tab or host}: scrollWidth {scroll} in a {vw} viewport"
        for tab, state in facts["states"].items()
        for scroll, vw in [state["scroll"]]
        if scroll != vw
    ]
    assert not problems, "\n".join(problems)


def _open_all_and_measure(probe, host: str) -> dict:
    out: dict = {"small": [], "disclosures": 0, "heights": []}
    for tab in _tabs(probe, host):
        _show(probe, tab)
        closed = probe.page.evaluate(DOC_HEIGHT_JS)
        out["small"] += probe.page.evaluate(SIZES_JS, "[data-explainer], [data-terms] > summary")
        probe.page.evaluate(
            "() => document.querySelectorAll('[data-terms]').forEach((d) => { d.open = true; })"
        )
        probe.page.wait_for_timeout(120)
        out["small"] += probe.page.evaluate(SIZES_JS, "[data-term]")
        out["disclosures"] += probe.page.evaluate(
            "() => document.querySelectorAll('[data-terms]').length"
        )
        probe.page.evaluate(
            "() => document.querySelectorAll('[data-terms]').forEach((d) => { d.open = false; })"
        )
        probe.page.wait_for_timeout(200)
        out["heights"].append((tab or host, closed, probe.page.evaluate(DOC_HEIGHT_JS)))
    return out


@pytest.mark.parametrize(("world", "host"), PAIRS, ids=PAIR_IDS)
def test_added_text_is_at_least_11_px_and_opening_then_closing_every_disclosure_restores_the_height(
    firefox,
    advisor_server,
    seeded,
    world,
    host,  # noqa: F811
):
    seeded(world)
    with open_probe(firefox, advisor_server, width=PHONE[0], height=PHONE[1]) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, host)
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        measured = _open_all_and_measure(probe, host)
    assert measured["disclosures"] > 0, "premise: the host carries disclosures"
    assert not measured["small"], f"added text under {MIN_TEXT_PX} px: {measured['small'][:6]}"
    # Each state is compared with itself: the page's other tabs lay out differently from this one.
    drifted = [h for h in measured["heights"] if abs(h[2] - h[1]) > 1]
    assert not drifted, f"(state, closed px, reclosed px): {drifted}"


# ---------------------------------------------------------------------------
# S-2.12: keyboard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("world", "host"), PAIRS, ids=PAIR_IDS)
def test_every_disclosure_is_closed_on_load_named_and_opens_and_closes_by_enter_and_space(
    firefox,
    advisor_server,
    seeded,
    world,
    host,  # noqa: F811
):
    seeded(world)
    problems, total = [], 0
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, host)
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        for tab in _tabs(probe, host):
            _show(probe, tab)
            for index, item in enumerate(probe.page.evaluate(DISCLOSURES_JS)):
                total += 1
                if item["open"]:
                    problems.append(f"{item['key']}: open on load")
                # A Glossary is named "Glossary"; every other disclosure "What the terms mean".
                want = "Glossary" if item["key"] == "glossary" else SUMMARY_TEXT
                if item["name"] != want:
                    problems.append(f"{item['key']}: named {item['name']!r}, not {want!r}")
                # The index-th visible disclosure, so a second Glossary on another tab is not
                # mistaken for this one.
                details = probe.page.locator("details[data-terms]:visible").nth(index)
                summary = details.locator(":scope > summary")
                for keystroke in ("Enter", "Space"):
                    summary.focus()
                    probe.page.keyboard.press(keystroke)
                    opened = details.evaluate("(d) => d.open")
                    probe.page.keyboard.press(keystroke)
                    closed = not details.evaluate("(d) => d.open")
                    if not (opened and closed):
                        problems.append(
                            f"{item['key']}: {keystroke} opened={opened} closed={closed}"
                        )
    assert total > 0, f"premise: {host} carries disclosures"
    assert not problems, f"{world} {host}:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# S-2.14: an opened disclosure survives the page's refresh (the dashboard is the host cycle's own)
# ---------------------------------------------------------------------------

DISCLOSURE_LINES_JS = (
    "(k) => { const d = document.querySelector('details[data-terms=\"' + k + '\"]');"
    " return { open: d.open, lines: [...d.querySelectorAll('[data-term]')]"
    ".map((e) => e.textContent.replace(/\\s+/g, ' ').trim()) }; }"
)


def test_an_opened_overview_disclosure_survives_the_runs_refresh_with_its_lines_unchanged(
    firefox,
    advisor_server,
    seeded,  # noqa: F811
):
    seeded("full")
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, "/ai-advisor")
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        probe.page.locator('#tab-panel-overview details[data-terms="glossary"] > summary').click()
        before = probe.page.evaluate(DISCLOSURE_LINES_JS, "glossary")
        for _ in range(3):
            probe.context.clock.run_for(16000)
            probe.page.wait_for_timeout(400)
        after = probe.page.evaluate(DISCLOSURE_LINES_JS, "glossary")
    assert before["open"] is True and before["lines"]
    assert after == before


def test_an_opened_performance_glossary_survives_a_window_pick_with_its_lines_unchanged(
    firefox,
    advisor_server,
    seeded,
    monkeypatch,  # noqa: F811
):
    import analytics

    seeded("full")
    # The window picker draws once the page holds shadow history: the Performance cycle's world.
    perf.seed_world()
    warm_market_calendar()
    monkeypatch.setattr(analytics, "DB_FILE", os.environ["DB_PATH"])
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, "/performance")
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        probe.page.locator('details[data-terms="glossary"] > summary').first.click()
        before = probe.page.evaluate(DISCLOSURE_LINES_JS, "glossary")
        probe.page.click('[data-testid="window-btn-1y"]')
        probe.page.wait_for_timeout(1200)
        probe.page.click('[data-testid="window-btn-60"]')
        probe.page.wait_for_timeout(1200)
        after = probe.page.evaluate(DISCLOSURE_LINES_JS, "glossary")
    assert before["open"] is True and before["lines"]
    assert len(before["lines"]) == len(copy.glossary("Performance"))
    assert after == before


# ---------------------------------------------------------------------------
# S-2.18, S-3.11: every script refused
# ---------------------------------------------------------------------------

BODY_JS = "() => document.body.textContent.replace(/\\s+/g, ' ')"


def _flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


@pytest.mark.parametrize(("world", "host"), PAIRS, ids=PAIR_IDS)
def test_with_every_script_refused_the_template_built_disclosures_and_labels_are_whole(
    firefox,
    advisor_server,
    seeded,
    world,
    host,  # noqa: F811
):
    seeded(world)
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        probe.page.route("**/static/*.js*", lambda request_route: request_route.abort())
        probe.page.goto(advisor_server.base_url + host)
        probe.page.wait_for_load_state("load")
        text = _flat(probe.page.evaluate(BODY_JS))
        disclosures = probe.page.evaluate(
            """() => [...document.querySelectorAll('details[data-terms]')].map((d) => ({
                 key: d.getAttribute('data-terms'), open: d.open,
                 lines: [...d.querySelectorAll('[data-term]')].map((e) => e.getAttribute('data-term'))}))"""
        )
    problems = [f"{d['key']}: open with no script" for d in disclosures if d["open"]]
    rows = [
        r
        for r in copy.rows("B")
        if r["builder"] == "template"
        and r.get("placement_kind") == "D"
        and page_of(r["host"]) == host
        and not slotted(r)
        # X11 is three Glossary lines now (X11a to X11c), whose words read on in the page's text
        and r["key"] != "X11"
        and _flat(copy.text(r["key"], "B")) in text
    ]
    shown_keys = {key for d in disclosures for key in d["lines"]}
    # A longer row of the same host may carry this row's sentence as its own first words.
    problems += [
        f"{r['key']}: its words are on the page but no disclosure line carries it"
        for r in rows
        if r["key"] not in shown_keys
        and not any(
            o is not r and _flat(copy.text(r["key"], "B")) in _flat(copy.text(o["key"], "B"))
            for o in rows
        )
    ]
    labels = [
        r["key"]
        for r in copy.rows("C")
        if r["builder"] == "template"
        and page_of(r.get("host_place") or "") == host
        and not slotted(r)
        and "new_resolved" not in r
        # Y02's reference wording is shortened by CX-8, which the History tests pin.
        and r["key"] != "Y02"
        and not all(_flat(part) in text for part in new_alternatives(r))
        and world == "full"
    ]
    problems += [f"{k}: reworded label missing with no script" for k in labels]
    assert disclosures or world == "empty", f"premise: {host} has template disclosures"
    assert not problems, f"{world} {host}:\n" + "\n".join(problems)


# ---------------------------------------------------------------------------
# S-5.4: honesty texts equal the reference's
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("world", "host"), PAIRS, ids=PAIR_IDS)
def test_every_basis_window_stamp_caveat_and_exclusion_text_equals_the_reference(
    firefox,
    advisor_server,
    seeded,
    ref,
    world,
    host,  # noqa: F811
):
    facts = reading(firefox, advisor_server, seeded, world, host, WIDE[0])
    want = ref["worlds"][world]["hosts"][host]["figures_1440"]
    honesty = [(k, t) for k, t in want if HONESTY.search(k) and t]
    assert honesty or world == "empty", f"premise: {host} has honesty figures at the reference"
    got = [(k, t) for k, t in facts["figures"] if HONESTY.search(k)]
    strict = figure_differences(got, honesty, [])
    loose = figure_differences(got, honesty, _relabel_matchers())
    reworded = [p for p in strict if p not in loose]
    for problem in loose:
        pytest.fail(f"{world} {host}: {problem}")
    for problem in reworded:
        key = problem.split(":", 1)[0]
        assert any(k == key and t for k, t in got), f"{key}: reworded to nothing"


# ---------------------------------------------------------------------------
# S-5.10: a figure that is absent, null or not finite shows a dash, never a placeholder
# ---------------------------------------------------------------------------

DASH_ONLY = re.compile(r"^[\s\-—–·]+$")
BAD_VALUES = {"absent": "__absent__", "none": None, "nan": float("nan"), "inf": float("inf")}
CARD_FIELDS = ("current_return", "current_value", "mc_prob", "symphony_vol", "high_water_mark")
EXIT_FIELDS = ("exit_return", "shadow_return", "saved_pct_guard_alpha", "saved_dollars")


def _degrade(record: dict, fields, value) -> None:
    for field in fields:
        if value == "__absent__":
            record.pop(field, None)
        else:
            record[field] = value


def _seed_degraded(host: str, variant: str, tmp_path, monkeypatch) -> None:
    import database
    from tests.ui._shell_support import (
        CAPTURED_POST_MORTEM_DAYS,
        IF_HELD_STAMP,
        load_captured_bot_state,
        pm_capture,
    )

    value = BAD_VALUES[variant]
    harness.fix_clock_and_token(monkeypatch.setattr)
    import analytics

    nothing = tmp_path / "no_post_mortems"
    nothing.mkdir(exist_ok=True)
    monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(nothing))
    state = load_captured_bot_state()
    for entry in state.values():
        if isinstance(entry, dict) and "name" in entry and host == "/":
            _degrade(entry, CARD_FIELDS, value)
    database.save_state(state)
    if host == "/history":
        directory = tmp_path / "post_mortems"
        directory.mkdir()
        for day in CAPTURED_POST_MORTEM_DAYS:
            record = pm_capture(day)
            for trigger in record.get("triggers") or []:
                trigger["if_held_source"] = IF_HELD_STAMP
                _degrade(trigger, EXIT_FIELDS, value)
            (directory / f"post_mortem_{day}.json").write_text(json.dumps(record), encoding="utf-8")
        monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(directory))


# History's answer carries NaN and Infinity as bare JSON words the browser cannot parse; the page
# never replaces its Loading row, at the reference as at this head (no route or script of
# History is part of this change). The criterion's "same dash as at the reference" has no such
# dash to match there, so those two History cases are not encoded (sent to main).
CASES = [
    (host, variant)
    for host in ("/", "/history")
    for variant in sorted(BAD_VALUES)
    if not (host == "/history" and variant in ("nan", "inf"))
]


@pytest.mark.parametrize(("host", "variant"), CASES)
def test_a_figure_that_is_absent_null_or_not_finite_shows_a_dash_and_no_placeholder(
    firefox,
    advisor_server,
    tmp_path,
    monkeypatch,
    host,
    variant,  # noqa: F811
):
    harness.credential_free_environment(
        tmp_path / "page.env", monkeypatch.setattr, lambda n: monkeypatch.delenv(n, raising=False)
    )
    _seed_degraded(host, variant, tmp_path, monkeypatch)
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, host)
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        figures = probe.page.evaluate(harness.FIGURES_JS)
        body = probe.page.evaluate("() => document.body.innerText")
        explainers = probe.page.evaluate(
            "() => document.querySelectorAll('[data-explainer]').length"
        )
        errors = list(probe.page_errors)
    leaks = [f"{k}: {t!r}" for k, t in figures if LEAK.search(t)] + [
        f"page text: {m.group(0)}" for m in LEAK.finditer(body)
    ]
    vocabulary = {"—", "--", "α —"}
    odd = [
        f"{k}: {t!r}"
        for k, t in figures
        if t and DASH_ONLY.match(t) and t.strip() not in vocabulary
    ]
    assert not leaks, f"{host} {variant} leaks a placeholder:\n" + "\n".join(leaks[:8])
    assert not odd, f"{host} {variant} shows a dash the reference never used:\n" + "\n".join(
        odd[:8]
    )
    assert explainers > 0, f"{host} {variant}: the page lost its descriptions"
    assert errors == [], errors


# ---------------------------------------------------------------------------
# S-6.9: History's reason rows stay slim
# ---------------------------------------------------------------------------

REASON_ROW_MAX_PX = 40
REASON_ROWS_JS = (
    "() => [...document.querySelectorAll('#reason-cards tr[data-testid=\"reason-card\"]')]"
    ".filter((r) => r.getClientRects().length > 0)"
    ".map((r) => Math.round(r.getBoundingClientRect().height))"
)


def test_history_reason_rows_are_at_most_40_px_tall_at_1440(
    firefox,
    advisor_server,  # noqa: F811
    seeded,
):
    seeded("full")
    with open_probe(firefox, advisor_server, width=WIDE[0], height=WIDE[1]) as probe:
        probe.context.clock.install(time=harness.CAPTURE_INSTANT)
        open_page(probe, "/history")
        probe.page.wait_for_timeout(harness.SETTLE_MS)
        heights = probe.page.evaluate(REASON_ROWS_JS)
    assert heights, "premise: History shows its reason rows"
    assert max(heights) <= REASON_ROW_MAX_PX, heights
