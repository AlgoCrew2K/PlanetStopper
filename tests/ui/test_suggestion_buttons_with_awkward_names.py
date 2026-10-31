"""Apply suggestion and Dismiss work for a symphony whose name holds an apostrophe, a quote or code.

The advisor card's two buttons used to carry the symphony name inside an inline ``onclick`` string,
escaped for HTML: the browser decodes the escape before the handler is compiled, so a name with an
apostrophe is a syntax error (the buttons do nothing) and a name built to close the string runs as
script. Real names carry apostrophes: two of the six symphony names in the captured Composer
statistics do (tests/fixtures/composer/tenure-review/case1_statistics_roth.json).

Real call path: ``static/ai_advisor.js`` runs unmodified over the page ``GET /ai-advisor`` renders
(tests/ui/_page_js_harness.py, which evaluates an inline ``onclick`` the way a browser does, after
decoding the attribute). The operator picks the symphony, runs the advisor (the suggest route's answer is
one suggestion in the shape the structured-output schema asks for, tests/_suggest_and_chat_routes.py), and
presses the card's button. What is sent is read off the recorded request.

Contract pinned
* Pressing Apply sends exactly one POST to ``/ai-advisor/accept`` whose ``symphony_id`` is the picked
  name, character for character, and whose ``suggestion`` is the suggestion on the card.
* Pressing Dismiss does the same to ``/ai-advisor/reject``.
* No name runs as script: a name that tries to close the string sets nothing on the page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests._suggest_and_chat_routes import SUGGESTION_ITEM
from tests.ui._page_js_harness import render_page, route, run_page

CAPTURED_NAMES = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "composer"
        / "tenure-review"
        / "case1_statistics_roth.json"
    ).read_text(encoding="utf-8")
)["response_body"]["symphonies"]
NAMES_WITH_AN_APOSTROPHE = [s["name"] for s in CAPTURED_NAMES if "'" in s["name"]]
NAMES_WITHOUT = [s["name"] for s in CAPTURED_NAMES if "'" not in s["name"]]

CONSTRUCTED_NAMES = {
    "closes the string and runs code": "x');window.__ran=1;//",
    "quote and ampersand": 'A & B "quoted" <i>name</i>',
    "backslash at the end": "ends with a backslash\\",
}

SUGGEST = r"/ai-advisor/suggest$"
ACCEPT = r"/ai-advisor/accept$"
REJECT = r"/ai-advisor/reject$"

PRESS = """
  const name = __NAME__;
  const select = H.$('#symphony-id-input');
  select.innerHTML = '';
  const option = document.createElement('option');
  option.setAttribute('value', name);
  option.textContent = name;
  select.appendChild(option);
  select.value = name;
  H.$('#get-suggestions-btn').removeAttribute('disabled');
  await H.click('#get-suggestions-btn');
  const button = H.$$('#suggestions-container button').find((b) => /__LABEL__/i.test(b.textContent));
  if (!button) return { error: 'no __LABEL__ button on the card', html: H.$('#suggestions-container').innerHTML.slice(0, 300) };
  button.setAttribute('id', 'probe-button');
  let thrown = null;
  try { await H.click('#probe-button'); } catch (e) { thrown = String(e && e.message || e); }
  return { thrown: thrown, ran: window.__ran === undefined ? null : window.__ran };
"""


def _press(name: str, label: str):
    scenario = "await H.boot();\n" + PRESS.replace("__NAME__", json.dumps(name)).replace(
        "__LABEL__", label
    )
    out = run_page(
        render_page("/ai-advisor"),
        scenario,
        routes=[
            route(
                SUGGEST,
                {"status": 200, "json": {"suggestions": [SUGGESTION_ITEM]}},
                method="POST",
            ),
            route(ACCEPT, {"status": 200, "json": {"status": "accepted"}}, method="POST"),
            route(REJECT, {"status": 200, "json": {"status": "rejected"}}, method="POST"),
        ],
        pathname="/ai-advisor",
    )
    assert isinstance(out.result, dict) and "error" not in out.result, out.result
    return out


ALL_NAMES = {
    **{f"captured: {n}": n for n in NAMES_WITH_AN_APOSTROPHE},
    **{f"captured, no apostrophe: {n}": n for n in NAMES_WITHOUT[:1]},
    **CONSTRUCTED_NAMES,
}


def test_the_captured_names_include_apostrophes():
    assert len(NAMES_WITH_AN_APOSTROPHE) >= 2, (
        "the capture no longer holds a name with an apostrophe"
    )


@pytest.mark.parametrize("name", list(ALL_NAMES.values()), ids=list(ALL_NAMES))
class TestTheCardsButtons:
    def test_apply_sends_the_picked_name_and_the_card_suggestion(self, name):
        out = _press(name, "Apply suggestion")
        posts = out.posts(ACCEPT.removesuffix("$"))
        assert len(posts) == 1, (
            f"pressing Apply sent {len(posts)} accept requests (script error: {out.result['thrown']}; "
            f"page errors: {out.errors})"
        )
        body = json.loads(posts[0]["body"])
        assert body["symphony_id"] == name
        assert body["suggestion"] == SUGGESTION_ITEM
        assert out.result["ran"] is None, "a name ran as script"

    def test_dismiss_sends_the_picked_name_and_the_card_suggestion(self, name):
        out = _press(name, "Dismiss")
        posts = out.posts(REJECT.removesuffix("$"))
        assert len(posts) == 1, (
            f"pressing Dismiss sent {len(posts)} reject requests (script error: {out.result['thrown']}; "
            f"page errors: {out.errors})"
        )
        body = json.loads(posts[0]["body"])
        assert body["symphony_id"] == name
        assert body["suggestion"] == SUGGESTION_ITEM
        assert out.result["ran"] is None, "a name ran as script"
