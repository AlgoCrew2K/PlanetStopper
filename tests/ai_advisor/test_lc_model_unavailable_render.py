"""The Logic Changes panel's model line, rendered by the real script from real route output.

The route JSON is produced by the real route and engine against the Anthropic HTTP fake; the
script is the real ``static/ai_advisor_logic_changes.js`` run under ``node`` in a ``vm`` context
with minimal element, ``document`` and ``fetch`` stubs. The harness fires ``DOMContentLoaded``
and the evaluate click, then reads the results area's HTML.

``node`` is required by ``tests/js_syntax/test_js_syntax.py`` as well; a missing ``node`` fails
these tests on an assertion so a runner without it cannot pass them silently.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from tests.advisors import logic_change_http_support as support

_JS = pathlib.Path(__file__).resolve().parents[2] / "static" / "ai_advisor_logic_changes.js"
_PROVENANCE_TESTID = 'data-testid="lc-live-generation-provenance"'

_HARNESS = """
const fs = require("fs");
const vm = require("vm");
const data = JSON.parse(process.argv[1]);
const jsPath = process.argv[2];
function makeEl() {
  return {
    value: "", disabled: false, innerHTML: "", textContent: "", style: {}, handlers: {},
    addEventListener(type, fn) { this.handlers[type] = fn; },
  };
}
const ids = ["tweak-symphony-select", "tweak-objective-select", "tweak-description",
             "tweak-evaluate-btn", "try-tweak-error", "logic-results-area"];
const els = {};
ids.forEach((id) => { els[id] = makeEl(); });
const docHandlers = {};
const document = {
  getElementById: (id) => els[id] || null,
  addEventListener: (type, fn) => { docHandlers[type] = fn; },
};
function fetchStub(url) {
  if (url === "/api/csrf-token") {
    return Promise.resolve({ json: () => Promise.resolve({ csrf_token: "t" }) });
  }
  return Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(data), text: () => Promise.resolve(""),
  });
}
const ctx = vm.createContext({ document, fetch: fetchStub, console, Promise, JSON });
vm.runInContext(fs.readFileSync(jsPath, "utf8"), ctx);
if (docHandlers["DOMContentLoaded"]) { docHandlers["DOMContentLoaded"](); }
els["tweak-symphony-select"].value = "sym";
els["tweak-description"].value = "desc";
els["tweak-evaluate-btn"].disabled = false;
els["tweak-evaluate-btn"].handlers["click"]();
setTimeout(() => {
  process.stdout.write(JSON.stringify({
    html: els["logic-results-area"].innerHTML, error: els["try-tweak-error"].textContent,
  }));
}, 50);
"""


@pytest.fixture(scope="module")
def flask_client():
    import app as _app

    _app.app.config["TESTING"] = True
    with _app.app.test_client() as client:
        yield client


def _render(route_json: dict) -> str:
    assert shutil.which("node"), "node is required to run the real panel script"
    result = subprocess.run(
        ["node", "-e", _HARNESS, json.dumps(route_json), str(_JS)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, f"node harness failed: {result.stderr}"
    return json.loads(result.stdout)["html"]


def _provenance_line(html: str) -> str:
    start = html.find(_PROVENANCE_TESTID)
    assert start != -1, f"the panel rendered no model line: {html[:300]!r}"
    return html[start : html.find("</div>", start)]


def _route_json(flask_client, monkeypatch, *replies, page=support.CAPTURED_MODELS_PAGE) -> dict:
    fake = support.new_fake(page)
    support.install_route_world(monkeypatch, fake)
    for reply in replies:
        fake.queue_message(reply)
    return support.evaluate_via_route(flask_client)


def test_unresolved_family_model_line_reads_model_unavailable(
    flask_client, monkeypatch, unresolved_models
):
    fake = support.new_fake()
    support.install_route_world(monkeypatch, fake)
    support.queue_failing_listings(fake)
    data = support.evaluate_via_route(flask_client)
    assert data["provenance"]["generation_model"] == "", (
        "self-guard: the route must report no model"
    )

    line = _provenance_line(_render(data))

    assert "model unavailable" in line, (
        f"an empty generation_model must read as unavailable: {line!r}"
    )


def test_model_line_names_the_reply_model_when_a_reply_arrived(flask_client, monkeypatch):
    reply = support.reply_with_tool_edit(support.real_edit())
    data = _route_json(flask_client, monkeypatch, reply)

    line = _provenance_line(_render(data))

    assert reply["model"] in line
    assert "model unavailable" not in line


def test_model_line_is_rendered_on_an_error_response_too(
    flask_client, monkeypatch, unresolved_models
):
    monkeypatch.setattr("advisors.logic_change_engine._has_composer_key", lambda: False)
    resp = flask_client.post(
        "/ai-advisor/logic-changes/evaluate",
        json={"symphony_id": support.ROUTE_SYMPHONY_NAME, "change_description": "x"},
    )
    data = resp.get_json()
    assert "error" in data, "self-guard: the no-key path answers with an error"

    line = _provenance_line(_render(data))

    assert "model unavailable" in line


def test_security_model_text_is_escaped_in_the_model_line(flask_client, monkeypatch):
    data = _route_json(flask_client, monkeypatch, support.reply_with_tool_edit(support.real_edit()))
    data["provenance"]["generation_model"] = '<img src=x onerror=alert(1)>"&'

    line = _provenance_line(_render(data))

    assert "<img" not in line, f"model text must render as text, never as markup: {line!r}"
    assert "&lt;img" in line
