"""The sleeve rule row's fire-count label follows the poll: the session's date while closed, "today"
while open, the same text a fresh server render of that data prints.

The ruling (see tests/app/test_sleeve_rule_fires_names_the_closed_session.py): while the market is
closed the row names the closed session's date ("Oct 30 3", month abbreviation then the day without
a zero pad); while open it reads "today 3". The page is the one `GET /` renders and the bodies are
the ones the real routes answer, for the same Flask world at two instants (a Saturday and the
following Monday's open), so no producer value and no payload field name is typed in here: whichever
fields the label is built from are in the bodies the routes return. `static/chrome.js` and
`static/index.js` run whole under node (tests/dashboard/_index_js_harness.py).

Four shapes of the poll are covered: a closed page polled by an open body and an open page polled by
a closed body (the row follows the body, equal to a fresh render of it); a poll that repeats the
page's own body (nothing moves); a rule the poll adds (its row is built from the template and
carries the label too). Two properties of the date text: it is text, never markup (every string in
the body that holds the session's date is replaced by markup and the page gains no element), and
it takes no part from the browser's zone (the node process runs at the extremes of the zone range,
where a date built from the ISO day in local time moves a day).

Fixture provenance: `_closed_session_world` (captured droplet rows, retimed) for the symphonies;
the sleeve, rule and fires are synthetic, written through the production writers.
"""

from __future__ import annotations

import copy
from datetime import date

import pytest

from tests.app._closed_session_world import ClosedWorld, build_history_world, utc_of
from tests.app._refresh_timing_frozen_world import SYMPHONY_IDS
from tests.app.test_closed_market_numbers_never_move import local, write_engine_cycle
from tests.app.test_history_exits_hold_while_closed import (
    FRIDAY,
    MONDAY,
    anchor_of,
    et_day_of,
    fires_by,
    seed_sleeve_with_fires,
)
from tests.app.test_sleeve_rule_fires_names_the_closed_session import session_label
from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

SATURDAY = ("2026-10-31", "00:01:00")
MONDAY_OPEN = (MONDAY, "10:01:00")
HOSTILE = "<img src=x onerror=alert(1)>"
# The two ends of the zone range: a date built from "2026-10-31" at local midnight and read in UTC,
# or built at UTC midnight and read in local time, moves a day in one of them.
# (zone, a UTC hour on Nov 3, the day that instant reads as there: the sanity check that the zone applies)
ZONES = {
    "UTC-10": ("Pacific/Honolulu", 0, 2),
    "UTC+14": ("Pacific/Kiritimati", 12, 4),
}

PRELUDE = r"""
const D = H.sandbox.document;
const rows = function () {
  return Array.prototype.map.call(
    D.querySelectorAll('[data-testid="sleeve-card"] [data-testid="sleeve-rule-row"]'),
    function (r) {
      const text = r.textContent.replace(/\s+/g, ' ').trim();
      const head = text.slice(0, text.indexOf('lifetime'))
        .replace(/^.*?\((?:SHADOW|PAPER|LIVE)\)\s*/, '').replace(/\s*·\s*$/, '').trim();
      return { id: r.getAttribute('data-rule-id'), head: head };
    }).filter(function (r) { return r.id !== ''; });
};
const poll = async function (body) {
  H.route('/api/state', { body: body });
  await H.tick(); await H.completeAnimations();
};
const count = function (selector) { return D.querySelectorAll(selector).length; };
"""


def capture(cw: ClosedWorld) -> dict:
    """The page and the bodies the real routes answer now, in the shape the harness loads."""
    client = cw.world.client

    def body(path: str) -> dict:
        response = client.get(path)
        assert response.status_code == 200, f"{path} answered {response.status_code}"
        return response.get_json()

    page = client.get("/")
    assert page.status_code == 200
    return {
        "html": page.get_data(as_text=True),
        "state": body("/api/state"),
        "hero": {token: body(f"/api/hero-chart/{token}") for token in hx.HERO_TOKENS},
        "strips": {token: body(f"/api/strip/{token}") for token in hx.HERO_TOKENS},
        "summaries": {
            token: body(f"/api/guard-alpha-summary?window={token}") for token in hx.HERO_TOKENS
        },
        "charts": {sym_id: body(f"/api/chart/{sym_id}") for sym_id in SYMPHONY_IDS},
    }


class Worlds:
    """The Friday-session world captured twice: closed (Saturday) and open (Monday 10:01)."""

    def __init__(self, monkeypatch, tmp_path):
        cw = build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
        self.fired = fires_by(*MONDAY_OPEN)
        seed_sleeve_with_fires(self.fired)
        cw.at(*SATURDAY)
        self.anchor = anchor_of(cw)
        self.closed = capture(cw)
        self.closed_now = utc_of(*SATURDAY).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        write_engine_cycle(cw.world, local(MONDAY, "10:00:20"), 1)
        cw.at(*MONDAY_OPEN)
        self.opened = capture(cw)
        self.open_now = utc_of(*MONDAY_OPEN).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        assert self.anchor == FRIDAY
        assert self.closed["state"]["market_state"] != "open"
        assert self.opened["state"]["market_state"] == "open"
        self.closed_count = sum(1 for f in self.fired if et_day_of(f) == self.anchor)
        self.open_count = sum(1 for f in self.fired if et_day_of(f) == MONDAY)
        assert self.closed_count >= 2 and self.open_count == 1, "fixture sanity"

    def run(self, page: dict, scenario: str, other_state: dict | None = None) -> dict:
        """Run `scenario` on `page`; `H.bodies.other.state` carries `other_state`."""
        now = self.open_now if page is self.opened else self.closed_now
        return hx.run_scenario(
            page,
            PRELUDE + scenario,
            scripts=sup.CHROME_AND_INDEX,
            extra_bodies={"other": {"state": other_state}},
            now_iso=now,
        )["result"]


@pytest.fixture
def worlds(monkeypatch, tmp_path) -> Worlds:
    return Worlds(monkeypatch, tmp_path)


def test_the_server_render_names_the_session_while_closed_and_today_while_open(worlds):
    rendered_closed = worlds.run(worlds.closed, "return rows();")
    rendered_open = worlds.run(worlds.opened, "return rows();")
    assert [r["head"] for r in rendered_closed] == [
        f"{session_label(worlds.anchor)} {worlds.closed_count}"
    ]
    assert [r["head"] for r in rendered_open] == [f"today {worlds.open_count}"]


def test_a_closed_page_polled_with_an_open_body_reads_today(worlds):
    fresh = worlds.run(worlds.opened, "return rows();")
    after = worlds.run(
        worlds.closed,
        "const before = rows(); await poll(H.bodies.other.state); return { before: before, after: rows() };",
        worlds.opened["state"],
    )
    assert after["after"] == fresh, (
        "the poll of an open body must leave the row a fresh render of that body prints "
        f"({[r['head'] for r in fresh]}), it left {[r['head'] for r in after['after']]}"
    )
    assert after["before"] != after["after"], "fixture sanity: the two states print different rows"


def test_an_open_page_polled_with_a_closed_body_names_the_session(worlds):
    fresh = worlds.run(worlds.closed, "return rows();")
    after = worlds.run(
        worlds.opened,
        "await poll(H.bodies.other.state); return rows();",
        worlds.closed["state"],
    )
    assert after == fresh, (
        "the poll of a closed body must leave the row a fresh render of that body prints "
        f"({[r['head'] for r in fresh]}), it left {[r['head'] for r in after]}"
    )
    assert after[0]["head"] == f"{session_label(worlds.anchor)} {worlds.closed_count}"


@pytest.mark.parametrize("state", ["closed", "opened"])
def test_a_poll_of_the_pages_own_body_leaves_the_row_as_rendered(worlds, state):
    page = getattr(worlds, state)
    result = worlds.run(
        page,
        "const before = rows(); await poll(H.bodies.state); return { before: before, after: rows() };",
    )
    assert result["after"] == result["before"]


@pytest.mark.parametrize("state", ["closed", "opened"])
def test_a_rule_the_poll_adds_carries_the_same_label_as_the_rules_already_there(worlds, state):
    page = getattr(worlds, state)
    body = copy.deepcopy(page["state"])
    rules = body["dashboard_panels"]["sleeves"][0]["rules"]
    added = {**rules[0], "id": max(rule["id"] for rule in rules) + 1, "name": "A new rule"}
    rules.append(added)
    result = worlds.run(
        page,
        "await poll(H.bodies.other.state); return rows();",
        body,
    )
    assert len(result) == len(rules), "the poll must add the rule's row"
    heads = [row["head"] for row in result]
    assert heads[-1] == heads[0], (
        f"a row built from the template must print the label of its siblings: {heads}"
    )


def strings_equal_to(node, value, path=()):
    """Every path in a JSON body whose string equals `value`."""
    if isinstance(node, dict):
        for key, child in node.items():
            yield from strings_equal_to(child, value, (*path, key))
    elif isinstance(node, list):
        for index, child in enumerate(node):
            yield from strings_equal_to(child, value, (*path, index))
    elif node == value:
        yield path


def with_markup_for(body: dict, value: str) -> tuple[dict, int]:
    """`body` with every string of `meta` and `dashboard_panels` that equals `value` replaced by
    markup, and how many were replaced."""
    out = copy.deepcopy(body)
    replaced = 0
    for part in ("meta", "dashboard_panels"):
        for path in list(strings_equal_to(out[part], value)):
            holder = out[part]
            for step in path[:-1]:
                holder = holder[step]
            holder[path[-1]] = HOSTILE
            replaced += 1
    return out, replaced


@pytest.mark.parametrize("state", ["closed", "opened"])
def test_the_date_is_set_as_text_never_as_markup(worlds, state):
    """Wherever the poll body carries the session's date (in `meta` or the panels, whichever field
    the label is built from) it is replaced by markup: the page must gain no element."""
    page = getattr(worlds, state)
    hostile, replaced = with_markup_for(worlds.closed["state"], worlds.anchor)
    assert replaced, "fixture sanity: the closed body carries the session's date"
    result = worlds.run(
        page,
        "const shape = function () { return { img: count('img'), onerror: count('[onerror]'),"
        " all: count('*') }; };"
        "const before = shape(); await poll(H.bodies.other.state);"
        "return { before: before, after: shape() };",
        hostile,
    )
    assert result["after"] == result["before"], (
        f"the date reached the page as markup: elements {result['before']} -> {result['after']}"
    )


@pytest.mark.parametrize("zone", sorted(ZONES))
def test_the_label_takes_nothing_from_the_browsers_zone(monkeypatch, worlds, zone):
    name, utc_hour, shifted_day = ZONES[zone]
    monkeypatch.setenv("TZ", name)
    result = worlds.run(
        worlds.opened,
        f"const shifted = new Date(Date.UTC(2026, 10, 3, {utc_hour}, 0, 0)).getDate();"
        "await poll(H.bodies.other.state);"
        "return { shifted: shifted, rows: rows() };",
        worlds.closed["state"],
    )
    assert result["shifted"] == shifted_day, (
        f"fixture sanity: the node process must run in the zone {name} "
        f"(Nov 3 {utc_hour:02d}:00 UTC reads as day {result['shifted']} there)"
    )
    assert [row["head"] for row in result["rows"]] == [
        f"{session_label(worlds.anchor)} {worlds.closed_count}"
    ], f"in zone {zone} the label must still name {worlds.anchor} as the ISO digits say"
