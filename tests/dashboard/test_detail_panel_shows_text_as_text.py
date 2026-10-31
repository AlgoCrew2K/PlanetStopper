"""The detail panel writes the text it is given as text, never as markup.

A symphony's name reaches the engine's event log (``"<name> ARMED (trail)"``), and the log comes back
through ``/api/logs/<id>``; a symphony's variables come back through ``/api/settings``. Both are
rendered by ``renderDetailLogs`` / ``renderDetailVars`` in ``static/index.js``. A name that carries
markup must read on screen as the characters it holds, and must not create an element.

Real call path: the whole of ``static/index.js`` runs under node (tests/dashboard/_index_js_harness.py)
over the page ``GET /`` renders; the panel is opened the way a user opens it, and the two routes it
asks are answered with the bodies below. The expected text is the payload itself.

Fixture provenance: the payloads are synthetic and labelled so. Their only job is to carry markup
through a field the real producers fill (an event message, an event type, a timestamp, a variable
name and value); the shapes around them (``{timestamp, event_type, message}`` and
``{symphonies: {<id>: {params, locked_vars}}}``) are the shapes the routes return
(tests/dashboard/test_detail_panel_follow.py builds the same bodies).
"""

from __future__ import annotations

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

WORLD = "default"
SYMPHONY = hx.UNTRIGGERED_ID

# Elements a payload would create if it were parsed as markup; the panel's own markup uses none.
MARKUP_TAGS = ("img", "b", "u", "i", "script", "svg")

NAME_WITH_IMAGE = "<img src=x onerror=alert(1)> ARMED (trail)"
NAME_WITH_QUOTES = 'Bob\'s "quoted" <b>Strategy</b> & Co'

PANEL = r"""
const D = H.sandbox.document;
const decode = function (s) {
  return s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#0*39;/g, "'")
          .replace(/&#x27;/gi, "'").replace(/&amp;/g, '&');
};
// What a reader sees of a container: the text of its elements plus its own markup with every real
// tag removed and every entity decoded. A payload written as markup loses its tags here and is
// missing from the result; one written as text, by either route, is in it.
const readable = function (el) {
  return el.textContent + ' ' + decode((el.innerHTML || '').replace(/<[^>]*>/g, ' '));
};
// Every tag name the container holds, as markup or as elements.
const tagsIn = function (el) {
  const names = [];
  (el.innerHTML || '').replace(/<\s*([a-zA-Z][a-zA-Z0-9-]*)/g, function (m, name) { names.push(name.toLowerCase()); return m; });
  (function walk(node) {
    node.childNodes.forEach(function (child) {
      if (child.nodeType === 1) { names.push(child.localName); walk(child); }
    });
  })(el);
  return names;
};
const open = async function (id) { return H.press('.sym-card[data-sym-id="' + id + '"]'); };
const read = function (id) {
  const el = D.getElementById(id);
  return { readable: readable(el), tags: tagsIn(el) };
};
"""


def _run(scenario: str, **bodies):
    world = hx.get_world(WORLD)
    return hx.run_scenario(
        world,
        PANEL + scenario,
        scripts=sup.CHROME_AND_INDEX,
        extra_bodies=bodies,
        now_iso=hx.seeded_now_iso(world),
    )["result"]


def _no_markup_was_created(read: dict) -> None:
    created = [tag for tag in read["tags"] if tag in MARKUP_TAGS]
    assert created == [], f"the text became elements: {created} in {read}"


class TestEventLog:
    def _open_with(self, events: list[dict]) -> dict:
        return _run(
            r"""
            await H.boot({ extras: true });
            H.route(/^\/api\/logs\//, { body: H.bodies.events });
            await open(__ID__);
            await H.completeAnimations();
            return read('events-timeline');
            """.replace("__ID__", repr(SYMPHONY)),
            events=events,
        )

    def test_a_symphony_name_with_markup_in_an_event_message_reads_as_the_characters(self):
        shown = self._open_with(
            [{"timestamp": "2026-10-21T10:00:00", "event_type": "info", "message": NAME_WITH_IMAGE}]
        )
        _no_markup_was_created(shown)
        assert NAME_WITH_IMAGE in " ".join(shown["readable"].split()), shown["readable"]

    def test_quotes_and_ampersands_and_a_bold_tag_in_a_name_read_as_the_characters(self):
        message = f"{NAME_WITH_QUOTES} ARMED (trail)"
        shown = self._open_with(
            [{"timestamp": "2026-10-21T10:00:00", "event_type": "info", "message": message}]
        )
        _no_markup_was_created(shown)
        assert message in " ".join(shown["readable"].split()), shown["readable"]

    def test_a_reason_after_a_prefix_keeps_its_markup_as_text(self):
        message = "ARMED: " + NAME_WITH_IMAGE
        shown = self._open_with(
            [{"timestamp": "2026-10-21T10:00:00", "event_type": "info", "message": message}]
        )
        _no_markup_was_created(shown)
        assert NAME_WITH_IMAGE in " ".join(shown["readable"].split()), shown["readable"]

    def test_markup_in_the_event_type_and_in_a_short_timestamp_creates_no_element(self):
        shown = self._open_with(
            [{"timestamp": "<u>t</u>", "event_type": "<b>kind</b>", "message": "plain message"}]
        )
        _no_markup_was_created(shown)
        assert "plain message" in shown["readable"]

    def test_a_plain_message_still_reads_in_the_timeline(self):
        shown = self._open_with(
            [
                {
                    "timestamp": "2026-10-21T10:00:00",
                    "event_type": "info",
                    "message": "ARMED: trailing stop (trail)",
                }
            ]
        )
        assert "trailing stop (trail)" in shown["readable"]
        assert "10:00" in shown["readable"]


class TestVariables:
    def _open_with(self, params: dict, locked: list[str]) -> dict:
        settings = {"symphonies": {SYMPHONY: {"params": params, "locked_vars": locked}}}
        return _run(
            r"""
            await H.boot({ extras: true });
            H.route('/api/settings', { body: H.bodies.settings });
            await open(__ID__);
            await H.completeAnimations();
            return read('vars-section');
            """.replace("__ID__", repr(SYMPHONY)),
            settings=settings,
        )

    def test_a_variable_name_and_a_string_value_with_markup_read_as_the_characters(self):
        shown = self._open_with({"<img src=x onerror=alert(1)>": "<b>v</b>", "PLAIN_KEY": 2.5}, [])
        _no_markup_was_created(shown)
        readable = " ".join(shown["readable"].split())
        assert "<img src=x onerror=alert(1)>" in readable, shown["readable"]
        assert "<b>v</b>" in readable, shown["readable"]
        assert "PLAIN_KEY" in readable

    def test_a_locked_variable_with_markup_in_its_name_still_shows_its_badge(self):
        key = "<u>locked</u>"
        shown = self._open_with({key: 1.0}, [key])
        _no_markup_was_created(shown)
        assert key in " ".join(shown["readable"].split()), shown["readable"]
        assert "◆" in shown["readable"] or "&#9670;" in shown["readable"], "the lock badge is gone"
