"""What the dashboard and History say about their day, read from the page and from the client.

Shared by `test_open_market_stalled_session_wording` (the markup the server renders) and
`tests/ui/test_open_market_stalled_session_client` (the same words after the page script runs), so
both sides are held to one table of expected words.

The words a page can say about its day come in two forms:

* today's: `Today`, `If held · live` on a triggered card, `vs held now`, `Live Return`,
  `Live (if held)`, `HWM today`, `Today's events`, `No events yet today.`;
* a session's: its day (`Oct 30`) in place of `Today`, `If held` with no `live`, `at close`,
  `Return`, `If held`, `HWM Oct 30`, `Oct 30 events`, `No events on Oct 30.`

Today's form is the wording of the open market once a cycle has committed since the open. The
session form is the wording of the closed market, and of an open market on which no cycle has
committed since the open. The expected words are the product's wording as the templates and
`static/index.js` state it; the state they are read from is the real worlds of
`tests/app/_closed_session_world` (captured rows, the real writers, a virtual clock).
"""

from __future__ import annotations

import html as html_lib
import re
from datetime import date

from tests.app._closed_session_world import ClosedWorld, build_history_world

FRIDAY = "2026-10-30"
MONDAY = "2026-11-02"
TUESDAY = "2026-11-03"
FRIDAY_LABEL = "Oct 30"
MONDAY_LABEL = "Nov 2"

# The day word appears on the hero's Today row and on each of the two card layouts.
DAY_WORD_SLOTS = 3

TODAYS_WORDING = {
    "day": {"Today"},
    "if_held": {"If held", "If held · live"},
    "basis": {"vs held now"},
    "return": "Live Return",
    "legend": "Live (if held)",
    "hwm": "HWM today",
    "events": "Today’s events",
    "events_empty": "No events yet today.",
}


def session_wording(day_label: str) -> dict:
    """The words of a page whose figures belong to the session named by `day_label`."""
    return {
        "day": {day_label},
        "if_held": {"If held"},
        "basis": {"at close"},
        "return": "Return",
        "legend": "If held",
        "hwm": f"HWM {day_label}",
        "events": f"{day_label} events",
        "events_empty": f"No events on {day_label}.",
    }


def _text(fragment: str) -> str:
    return " ".join(html_lib.unescape(re.sub(r"<[^>]+>", "", fragment)).split())


def _texts(pattern: str, page: str) -> list[str]:
    return [_text(m) for m in re.findall(pattern, page, flags=re.S)]


def _one(pattern: str, page: str) -> str | None:
    found = _texts(pattern, page)
    assert len(found) == 1, f"{pattern!r} matched {len(found)} elements, expected one"
    return found[0]


def wording_of(page: str) -> dict:
    """The dashboard's day words as the server rendered them in `page`."""
    days = _texts(r'data-field="day-word"[^>]*>(.*?)</span>', page)
    assert len(days) == DAY_WORD_SLOTS, f"{len(days)} day-word elements: {days}"
    return {
        "day": set(days),
        "if_held": {
            t
            for t in _texts(r'<span class="dv-label">(.*?)</span>', page)
            if t.startswith("If held")
        },
        "basis": set(_texts(r'data-testid="guard-alpha-basis"[^>]*>(.*?)</span>', page)),
        "return": _one(r'data-field="live-return-label"[^>]*>(.*?)</div>', page),
        "legend": _one(r'data-field="if-held-legend"[^>]*>(.*?)</span>', page),
        "hwm": _one(r'data-field="hwm-label"[^>]*>(.*?)</div>', page),
        "events": _one(r'data-field="events-heading"[^>]*>(.*?)</div>', page),
        "events_empty": _one(r'data-field="events-empty"[^>]*>(.*?)</div>', page),
    }


# The same table read from the live page under node. Run before the boot (the server's first paint),
# after it (the load poll) and after the interval poll; the lists are reduced as `wording_of` does.
READ_WORDING_JS = """
const clean = function (e) { return e ? e.textContent.replace(/\\s+/g, ' ').trim() : null; };
const wording = function () {
  return {
    day: H.$$('[data-field="day-word"]').map(clean),
    dv: H.$$('.dv-label').map(clean),
    basis: H.$$('[data-testid="guard-alpha-basis"]').map(clean),
    ret: clean(H.$('[data-field="live-return-label"]')),
    legend: clean(H.$('[data-field="if-held-legend"]')),
    hwm: clean(H.$('[data-field="hwm-label"]')),
    events: clean(H.$('[data-field="events-heading"]')),
    eventsEmpty: clean(H.$('[data-field="events-empty"]')),
  };
};
"""


def reduce_client_wording(raw: dict) -> dict:
    """`wording_of`'s shape from what READ_WORDING_JS read (entities in first-paint markup decoded)."""
    decode = html_lib.unescape
    return {
        "day": {decode(t) for t in raw["day"]},
        "if_held": {decode(t) for t in raw["dv"] if decode(t).startswith("If held")},
        "basis": {decode(t) for t in raw["basis"]},
        "return": decode(raw["ret"]),
        "legend": decode(raw["legend"]),
        "hwm": decode(raw["hwm"]),
        "events": decode(raw["events"]),
        "events_empty": decode(raw["eventsEmpty"]),
    }


def stalled_world(monkeypatch, tmp_path) -> ClosedWorld:
    """Friday's session behind it (its last cycle committed at the close), the host on Friday."""
    return build_history_world(monkeypatch, tmp_path, first_history_day=date(2026, 9, 1))
