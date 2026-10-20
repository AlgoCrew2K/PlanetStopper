"""The "Run review now" button's client function, run under node against a stub DOM.

`retRunReview(btnEl)` in static/ai_advisor.js posts to the on-demand review route. The
code under test is the real function text, extracted from the script and executed; the
stub supplies only what a browser would: a button and a message slot found by their
`data-testid`, a `fetch` whose answers each test scripts, and the script-level helpers
the function may call.

What the client must do, from the operator's side:

* send one POST with no body, the `X-Requested-With: XMLHttpRequest` header (so the
  authentication gate answers 401 JSON instead of redirecting to an HTML page) and the
  CSRF token (the one the page already holds, else fetched from /api/csrf-token);
* read the HTTP status before it tries to parse a body, because a refusal from the
  global hooks is an HTML page and a JSON parse of it throws;
* keep the button disabled from the click until the response arrives and send nothing
  for a second click in that time;
* show a readable message for every outcome, built from fixed text and never from the
  response body, so a hostile body cannot inject markup.

The script's JS syntax is covered only by tests/js_syntax/test_js_syntax.py.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_JS_PATH = REPO_ROOT / "static" / "ai_advisor.js"

RUN_URL = "/ai-advisor/retirement/review/run"
CSRF_URL = "/api/csrf-token"
BUTTON_TESTID = "tenure-review-run-btn"
MESSAGE_TESTID = "tenure-review-run-message"

MESSAGES = {
    202: "Review started: reload this page when it finishes",
    409: "a review is already running",
    401: "Session expired: sign in again",
    403: "Request refused: reload the page",
}


def _source() -> str:
    assert _JS_PATH.exists(), f"expected file not found: {_JS_PATH}"
    return _JS_PATH.read_text(encoding="utf-8")


def _extract_function(source: str, name: str) -> str:
    match = re.search(rf"function\s+{name}\s*\(", source)
    assert match is not None, f"{name} is not defined in static/ai_advisor.js"
    brace = source.index("{", match.end())
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : i + 1]
    pytest.fail(f"{name}: unbalanced braces")


_HARNESS = r"""
var __log = { fetches: [], innerHtml: [], alerts: [] };
var __registry = {};

function __lookup(sel) {
  var m = /data-testid="([^"]+)"/.exec(sel) || /^#([\w-]+)$/.exec(sel);
  return m && Object.prototype.hasOwnProperty.call(__registry, m[1]) ? __registry[m[1]] : null;
}

function __makeElement() {
  var el = {
    textContent: '', hidden: false, dataset: {}, style: {}, attributes: {}, _disabled: false,
    classList: { add: function () {}, remove: function () {}, toggle: function () {}, contains: function () { return false; } },
    setAttribute: function (k, v) { this.attributes[k] = v; if (k === 'disabled') { this._disabled = true; } },
    removeAttribute: function (k) { delete this.attributes[k]; if (k === 'disabled') { this._disabled = false; } },
    getAttribute: function (k) { return Object.prototype.hasOwnProperty.call(this.attributes, k) ? this.attributes[k] : null; },
    addEventListener: function () {},
    closest: function () { return __panel; },
    querySelector: function (sel) { return __lookup(sel); },
    querySelectorAll: function (sel) { var e = __lookup(sel); return e ? [e] : []; }
  };
  Object.defineProperty(el, 'disabled', {
    get: function () { return this._disabled; },
    set: function (v) { this._disabled = !!v; }
  });
  Object.defineProperty(el, 'innerHTML', {
    get: function () { return ''; },
    set: function (v) { __log.innerHtml.push(String(v)); }
  });
  Object.defineProperty(el, 'innerText', {
    get: function () { return this.textContent; },
    set: function (v) { this.textContent = String(v); }
  });
  return el;
}

var __panel = __makeElement();
var __button = __makeElement();
var __message = __makeElement();
__button.parentElement = __panel;
__message.parentElement = __panel;
__registry['__BUTTON__'] = __button;
__registry['__MESSAGE__'] = __message;

var document = {
  getElementById: function (id) { return __lookup('#' + id); },
  querySelector: function (sel) { return __lookup(sel); },
  querySelectorAll: function (sel) { var e = __lookup(sel); return e ? [e] : []; },
  addEventListener: function () {},
  documentElement: { dataset: {} }
};
var window = {};
function cssVar(name) { return 'var(' + name + ')'; }
function escHtml(s) { return String(s); }
function alert(text) { __log.alerts.push(String(text)); }

var _csrfToken = __CSRF_PRESET__;
var __scenario = __SCENARIO__;
var __pendingRun = null;

function __response(spec) {
  return {
    ok: spec.status >= 200 && spec.status < 300,
    status: spec.status,
    headers: { get: function () { return spec.kind === 'html' ? 'text/html' : 'application/json'; } },
    json: function () {
      if (spec.kind === 'html') {
        return Promise.reject(new SyntaxError('Unexpected token < in JSON at position 0'));
      }
      return Promise.resolve(spec.body);
    },
    text: function () {
      return Promise.resolve(spec.kind === 'html' ? '<html><body>refused</body></html>' : JSON.stringify(spec.body));
    }
  };
}

function fetch(url, opts) {
  __log.fetches.push({ url: String(url), opts: opts || null });
  if (String(url) === '__CSRF_URL__') {
    if (__scenario.csrf === 'fail') { return Promise.resolve(__response({ status: 500, kind: 'html' })); }
    return Promise.resolve(__response({ status: 200, kind: 'json', body: { csrf_token: 'fetched-token' } }));
  }
  if (__scenario.run.mode === 'network-error') {
    return Promise.reject(new TypeError('Failed to fetch'));
  }
  if (__scenario.run.mode === 'pending') {
    return new Promise(function (resolve) { __pendingRun = function () { resolve(__response(__scenario.run)); }; });
  }
  return Promise.resolve(__response(__scenario.run));
}

__FUNCTION__

async function __flush() { for (var i = 0; i < 8; i++) { await new Promise(function (r) { setTimeout(r, 2); }); } }
function __runCalls() { return __log.fetches.filter(function (f) { return f.url === '__RUN_URL__'; }); }
function __state() {
  return { disabled: __button.disabled, message: __message.textContent, runCalls: __runCalls().length };
}

(async function () {
  var out = {};
  __DRIVER__
  out.log = __log;
  console.log(JSON.stringify(out));
})().catch(function (e) { console.log(JSON.stringify({ crashed: String(e && e.stack || e) })); process.exit(0); });
"""


def _run(driver: str, *, run: dict, csrf_preset: str | None = None, csrf: str = "ok") -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not installed: the run button client cannot be executed")
    function = _extract_function(_source(), "retRunReview")
    script = (
        _HARNESS.replace("__BUTTON__", BUTTON_TESTID)
        .replace("__MESSAGE__", MESSAGE_TESTID)
        .replace("__CSRF_PRESET__", json.dumps(csrf_preset))
        .replace("__SCENARIO__", json.dumps({"run": run, "csrf": csrf}))
        .replace("__CSRF_URL__", CSRF_URL)
        .replace("__RUN_URL__", RUN_URL)
        .replace("__FUNCTION__", function)
        .replace("__DRIVER__", driver)
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"node crashed:\n{result.stderr}"
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert "crashed" not in out, f"the harness or the function threw: {out['crashed']}"
    return out


def _headers(call: dict) -> dict:
    return {k.lower(): v for k, v in ((call["opts"] or {}).get("headers") or {}).items()}


_CLICK_AND_SETTLE = "retRunReview(__button); await __flush(); out.state = __state();"

HOSTILE = '<img src=x onerror="alert(1)">'


class TestWiring:
    def test_the_function_is_exposed_on_window_for_the_inline_onclick_handler(self):
        assert re.search(r"window\.retRunReview\s*=\s*retRunReview\s*;", _source())


class TestRequestShape:
    def test_it_sends_one_bodyless_post_with_the_xhr_marker_and_the_page_csrf_token(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 202, "kind": "json", "body": {"status": "started"}},
            csrf_preset="page-held-token",
        )
        calls = [f for f in out["log"]["fetches"] if f["url"] == RUN_URL]
        assert len(calls) == 1
        opts = calls[0]["opts"]
        assert opts["method"] == "POST"
        headers = _headers(calls[0])
        assert headers.get("x-requested-with") == "XMLHttpRequest"
        assert headers.get("x-csrf-token") == "page-held-token"
        assert opts.get("body") in (None, ""), "the run action reads no request body"
        assert [f for f in out["log"]["fetches"] if f["url"] == CSRF_URL] == [], (
            "a token the page already holds must not be fetched again"
        )

    def test_without_a_page_token_it_fetches_one_first_and_sends_that(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 202, "kind": "json", "body": {"status": "started"}},
            csrf_preset=None,
        )
        urls = [f["url"] for f in out["log"]["fetches"]]
        assert urls == [CSRF_URL, RUN_URL]
        assert _headers(out["log"]["fetches"][1]).get("x-csrf-token") == "fetched-token"
        assert _headers(out["log"]["fetches"][1]).get("x-requested-with") == "XMLHttpRequest"

    def test_when_no_token_can_be_obtained_nothing_is_posted_and_the_button_is_released(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 202, "kind": "json", "body": {}},
            csrf_preset=None,
            csrf="fail",
        )
        assert out["state"]["runCalls"] == 0
        assert out["state"]["disabled"] is False
        assert out["state"]["message"].startswith("Request failed")


class TestMessagesByStatus:
    @pytest.mark.parametrize(
        "status, kind",
        [(202, "json"), (409, "json"), (401, "json"), (403, "html")],
        ids=["started", "already running", "session expired", "request refused"],
    )
    def test_each_outcome_shows_its_fixed_message_and_releases_the_button(self, status, kind):
        out = _run(
            _CLICK_AND_SETTLE,
            run={
                "mode": "status",
                "status": status,
                "kind": kind,
                "body": {"status": "x", "error": HOSTILE, "message": HOSTILE},
            },
            csrf_preset="tok",
        )
        assert out["state"]["message"] == MESSAGES[status]
        assert out["state"]["disabled"] is False

    @pytest.mark.parametrize("status", [400, 404, 413, 429, 500, 502, 503])
    def test_any_other_status_is_reported_with_its_number_even_when_the_body_is_html(self, status):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": status, "kind": "html"},
            csrf_preset="tok",
        )
        assert out["state"]["message"] == f"Request failed: HTTP {status}"
        assert out["state"]["disabled"] is False

    def test_an_html_refusal_is_read_by_status_and_never_by_parsing_its_body(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 403, "kind": "html"},
            csrf_preset="tok",
        )
        assert out["state"]["message"] == MESSAGES[403]
        assert out["log"]["alerts"] == [], "a refusal is shown in the panel, not in a dialog"

    def test_a_network_failure_releases_the_button_and_says_the_request_failed(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "network-error"},
            csrf_preset="tok",
        )
        assert out["state"]["disabled"] is False
        assert out["state"]["message"].startswith("Request failed")

    @pytest.mark.parametrize("status", [202, 409])
    def test_no_response_text_is_ever_written_as_markup(self, status):
        out = _run(
            _CLICK_AND_SETTLE,
            run={
                "mode": "status",
                "status": status,
                "kind": "json",
                "body": {"status": HOSTILE, "message": HOSTILE, "error": HOSTILE},
            },
            csrf_preset="tok",
        )
        assert HOSTILE not in out["state"]["message"]
        assert not any("<img" in html for html in out["log"]["innerHtml"])


class TestOneRequestAtATime:
    def test_the_button_stays_disabled_until_the_response_arrives_and_a_second_click_sends_nothing(
        self,
    ):
        driver = """
        retRunReview(__button);
        await __flush();
        out.whilePending = __state();
        retRunReview(__button);
        await __flush();
        out.afterSecondClick = __state();
        __pendingRun();
        await __flush();
        out.afterResponse = __state();
        """
        out = _run(
            driver,
            run={"mode": "pending", "status": 202, "kind": "json", "body": {"status": "started"}},
            csrf_preset="tok",
        )
        assert out["whilePending"]["disabled"] is True
        assert out["whilePending"]["runCalls"] == 1
        assert out["afterSecondClick"]["disabled"] is True
        assert out["afterSecondClick"]["runCalls"] == 1, "a second click sent a second request"
        assert out["afterResponse"]["disabled"] is False
        assert out["afterResponse"]["message"] == MESSAGES[202]
        assert out["afterResponse"]["runCalls"] == 1

    def test_the_button_is_disabled_even_while_the_token_is_still_being_fetched(self):
        driver = """
        retRunReview(__button);
        out.immediately = __state();
        await __flush();
        """
        out = _run(
            driver,
            run={"mode": "pending", "status": 202, "kind": "json", "body": {}},
            csrf_preset=None,
        )
        assert out["immediately"]["disabled"] is True

    def test_after_a_response_a_new_click_sends_a_new_request(self):
        driver = """
        retRunReview(__button);
        await __flush();
        retRunReview(__button);
        await __flush();
        out.state = __state();
        """
        out = _run(
            driver,
            run={"mode": "status", "status": 409, "kind": "json", "body": {}},
            csrf_preset="tok",
        )
        assert out["state"]["runCalls"] == 2
        assert out["state"]["message"] == MESSAGES[409]
        assert out["state"]["disabled"] is False

    def test_a_click_on_an_already_disabled_button_sends_nothing(self):
        driver = """
        __button.disabled = true;
        retRunReview(__button);
        await __flush();
        out.state = __state();
        """
        out = _run(
            driver,
            run={"mode": "status", "status": 202, "kind": "json", "body": {}},
            csrf_preset="tok",
        )
        assert out["state"]["runCalls"] == 0
