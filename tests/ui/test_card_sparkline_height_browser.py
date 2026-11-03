"""A card's sparkline keeps its own height when a session caption shows, in a real browser.

Browser-only (marker ``browser``; run with ``python -m pytest -m browser -n 0
tests/ui/test_card_sparkline_height_browser.py``). Firefox headless through Playwright; the page and
its API answers are the harness world's (tests/ui/_dashboard_refresh_browser_support.py), the app in
this process serving the static scripts and the vendored Chart.js. Never skips.

The defect: Chart.js sizes a responsive canvas to its parent. When the parent also holds the session
caption and has only a minimum height, the parent grows by the caption's height, the canvas follows,
and the pair grows on every resize pass, so a card ends up many times its height. The page is
driven with every tape but one naming a session other than the ET day (the tapes of a section then
disagree, so each card names its own session; when they all agree the section's header line names it
once and no card repeats it, the last test).

Asserted on the live render: the canvas's parent is a box of the sparkline's own height, every canvas
renders at that height after several seconds and a later poll, the caption is outside the canvas's
parent and does not overlap the canvas, and a card with a caption is no taller than the same card
without one by more than the caption's own height. The 56 px figure is the card contract the template
has always stated for the sparkline; no colour or other size is asserted.
"""

from __future__ import annotations

import copy

import pytest

from tests.dashboard import _refresh_timing_support as sup
from tests.ui._browser_server import running_server
from tests.ui._dashboard_refresh_browser_support import (  # noqa: F401  (fixtures)
    firefox,
    new_probe,
    wait_refresh,
    world,
)

pytestmark = pytest.mark.browser

SPARK_HEIGHT_PX = 56
SETTLE_MS = 3_000
TOLERANCE_PX = 1.0
PREVIOUS_SESSION = "2000-01-03"

_CARD_FACTS = """
() => {
  const out = [];
  for (const canvas of document.querySelectorAll('[data-testid="card-spark"][data-sym-id]')) {
    let card = canvas;
    while (card && card.getAttribute('data-testid') !== 'sym-card') card = card.parentElement;
    const caption = card ? card.querySelector('.spark-session') : null;
    const wrapper = canvas.parentElement;
    const c = canvas.getBoundingClientRect();
    const w = wrapper.getBoundingClientRect();
    const k = caption ? caption.getBoundingClientRect() : null;
    out.push({
      id: canvas.getAttribute('data-sym-id'),
      canvasHeight: c.height,
      wrapperHeight: w.height,
      cardHeight: card ? card.getBoundingClientRect().height : null,
      captionShown: !!caption && !caption.hidden && getComputedStyle(caption).display !== 'none',
      captionInsideWrapper: !!caption && wrapper.contains(caption),
      captionHeight: k ? k.height : 0,
      captionBottom: k ? k.bottom : null,
      captionTop: k ? k.top : null,
      canvasTop: c.top,
      canvasBottom: c.bottom,
      captionOverlapsCanvas: !!k && k.height > 0 && k.width > 0 && k.top < c.bottom - 0.5 && k.bottom > c.top + 0.5
        && k.left < c.right - 0.5 && k.right > c.left + 0.5,
    });
  }
  return out;
}
"""


def _charts_naming(
    world_charts: dict, session_date: str, et_date: str, *, one_is_today: bool = False
) -> dict:
    """Every tape names `session_date`; with `one_is_today` the first tape names the ET day instead."""
    charts = copy.deepcopy(world_charts)
    for body in charts.values():
        if body.get("data"):
            named = et_date if one_is_today else session_date
            body["session_date"] = named
            body["et_date"] = et_date
            one_is_today = False
    return charts


def _mixed(world_charts: dict, today: str) -> dict:
    return _charts_naming(world_charts, PREVIOUS_SESSION, today, one_is_today=True)


def _drive(firefox, server, world, charts: dict, *, two_polls: bool = True) -> list[dict]:  # noqa: F811
    """Load the dashboard with these tape bodies, let it settle, poll again, settle, read the cards."""
    served = {**world, "charts": charts}
    probe = new_probe(firefox, server, served)
    try:
        probe.state = sup.with_stamp(world["state"], sup.render_stamp(world))
        probe.page.goto(server.base_url + "/")
        wait_refresh(probe, 1)
        probe.page.wait_for_timeout(SETTLE_MS)
        if two_polls:
            probe.state = sup.with_stamp(
                world["next"]["state"], sup.later_stamp(sup.render_stamp(world))
            )
            probe.page.evaluate("window.loadState()")
            wait_refresh(probe, 2)
            probe.page.wait_for_timeout(SETTLE_MS)
        return probe.page.evaluate(_CARD_FACTS)
    finally:
        probe.context.close()


def test_every_sparkline_keeps_its_height_with_a_session_caption_showing(firefox, world):  # noqa: F811
    today = world["seeded_today"]
    with running_server() as server:
        cards = _drive(firefox, server, world, _mixed(world["charts"], today))
    drawn = [c for c in cards if c["captionShown"]]
    assert drawn, "premise: a tape from another session shows its caption on at least one card"
    assert (
        len(drawn) == len([c for c in cards if world["charts"].get(c["id"], {}).get("data")]) - 1
    ), (
        f"premise: every card with a tape but the one naming today shows the caption, saw {[c['id'] for c in drawn]}"
    )
    for card in cards:
        assert abs(card["canvasHeight"] - SPARK_HEIGHT_PX) <= TOLERANCE_PX, (
            f"card {card['id']}: the sparkline renders {card['canvasHeight']:.0f} px tall, "
            f"not {SPARK_HEIGHT_PX} px, after the caption showed"
        )
        assert abs(card["wrapperHeight"] - SPARK_HEIGHT_PX) <= TOLERANCE_PX, (
            f"card {card['id']}: the canvas's parent is {card['wrapperHeight']:.0f} px tall"
        )


def test_the_caption_sits_outside_the_canvas_parent_and_clear_of_the_tape(firefox, world):  # noqa: F811
    today = world["seeded_today"]
    with running_server() as server:
        cards = _drive(
            firefox,
            server,
            world,
            _mixed(world["charts"], today),
            two_polls=False,
        )
    shown = [c for c in cards if c["captionShown"]]
    assert shown, "premise: a caption shows"
    for card in shown:
        assert not card["captionInsideWrapper"], (
            f"card {card['id']}: the caption is inside the box the chart sizes itself to"
        )
        assert not card["captionOverlapsCanvas"], (
            f"card {card['id']}: the caption overlaps the tape"
        )
        # The caption sits on the card's meta line (under the name), beside the tape rather than
        # above it, so the card takes no extra height for it.
        assert card["captionTop"] is not None, f"card {card['id']}: the caption has no position"


def test_a_card_with_a_caption_is_no_taller_than_one_without_by_more_than_the_caption(
    firefox,  # noqa: F811
    world,  # noqa: F811
):
    today = world["seeded_today"]
    with running_server() as server:
        with_caption = _drive(firefox, server, world, _mixed(world["charts"], today))
        without = _drive(firefox, server, world, _charts_naming(world["charts"], today, today))
    plain = {c["id"]: c for c in without}
    assert all(not c["captionShown"] for c in without), "premise: today's tape has no caption"
    for card in with_caption:
        if not card["captionShown"]:
            continue
        allowed = plain[card["id"]]["cardHeight"] + card["captionHeight"] + 2 * TOLERANCE_PX
        assert card["cardHeight"] <= allowed, (
            f"card {card['id']}: {card['cardHeight']:.0f} px tall with a caption against "
            f"{plain[card['id']]['cardHeight']:.0f} px without (caption {card['captionHeight']:.0f} px)"
        )


_HEADER_CAPTIONS = """
() => ({
  headers: Array.from(document.querySelectorAll('[data-field="section-session"]'))
    .filter((e) => !e.hidden && getComputedStyle(e).display !== 'none')
    .map((e) => ({
      text: e.textContent.trim(),
      inHeader: !!e.closest('.section-header'),
      box: e.getBoundingClientRect().toJSON(),
    })),
  cards: Array.from(document.querySelectorAll('.spark-session'))
    .filter((e) => !e.hidden && getComputedStyle(e).display !== 'none')
    .map((e) => e.textContent.trim()),
  width: document.documentElement.scrollWidth,
  viewport: document.documentElement.clientWidth,
})
"""


def test_tapes_that_all_name_one_session_have_it_stated_once_in_the_section_header(
    firefox,  # noqa: F811
    world,  # noqa: F811
):
    today = world["seeded_today"]
    served = {**world, "charts": _charts_naming(world["charts"], PREVIOUS_SESSION, today)}
    with running_server() as server:
        probe = new_probe(firefox, server, served)
        try:
            probe.state = sup.with_stamp(world["state"], sup.render_stamp(world))
            probe.page.goto(server.base_url + "/")
            wait_refresh(probe, 1)
            probe.page.wait_for_timeout(SETTLE_MS)
            seen = probe.page.evaluate(_HEADER_CAPTIONS)
        finally:
            probe.context.close()
    assert seen["headers"], "no section header line names the session"
    assert {h["text"] for h in seen["headers"]} == {f"{PREVIOUS_SESSION} session"}
    assert all(h["inHeader"] for h in seen["headers"]), (
        "the session is not in a section header line"
    )
    assert seen["cards"] == [], f"cards repeat the session their header names: {seen['cards']}"
    assert seen["width"] <= seen["viewport"], "the page scrolls sideways"
