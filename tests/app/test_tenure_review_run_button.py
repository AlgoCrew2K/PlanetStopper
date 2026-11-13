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
* read the HTTP status before it tries to parse a body, because the refusals differ in
  shape (an unauthenticated XHR gets 401 JSON from the authentication hook, a missing
  CSRF token gets a 403 HTML error page) and a JSON parse of the second throws;
* keep the button disabled from the click until the response arrives and send nothing
  for a second click in that time;
* show a readable message for every outcome, built from fixed text and never from the
  response body or from the text of an error thrown on the way, so neither a hostile body
  nor an error quoting a body can reach the panel;
* style a refusal (session expired, request refused, request failed) with the negative
  design token and an acknowledgement or an already-running notice without it.

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
_GUARD_JS_PATH = REPO_ROOT / "static" / "request_guard.js"

RUN_URL = "/ai-advisor/retirement/review/run"
CSRF_URL = "/api/csrf-token"
BUTTON_TESTID = "tenure-review-run-btn"
MESSAGE_TESTID = "tenure-review-run-message"

MESSAGES = {
    202: "Review started: reload this page when it finishes",
    409: "a review is already running",
    401: "Session expired: sign in again",
}


def _says_not_sent(message: str) -> bool:
    """A 403 is the session check refusing the click: the control says nothing was sent and which click
    sends it (the wording belongs to the shared helper, so the contract is what it must convey)."""
    return bool(re.search(r"not sent", message, re.I) and re.search(r"click|choose", message, re.I))


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
    textContent: '', hidden: false, dataset: {}, attributes: {}, _disabled: false, _classes: {},
    style: {
      setProperty: function (k, v) { this[k] = v; },
      removeProperty: function (k) { delete this[k]; }
    },
    setAttribute: function (k, v) {
      this.attributes[k] = v;
      if (k === 'disabled') { this._disabled = true; }
      if (k === 'class') { this.className = v; }
    },
    removeAttribute: function (k) {
      delete this.attributes[k];
      if (k === 'disabled') { this._disabled = false; }
      if (k === 'class') { this.className = ''; }
    },
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
  el.classList = {
    add: function () { for (var i = 0; i < arguments.length; i++) { el._classes[arguments[i]] = true; } },
    remove: function () { for (var i = 0; i < arguments.length; i++) { delete el._classes[arguments[i]]; } },
    toggle: function (c, force) {
      var on = force === undefined ? !el._classes[c] : !!force;
      if (on) { el._classes[c] = true; } else { delete el._classes[c]; }
      return on;
    },
    contains: function (c) { return !!el._classes[c]; }
  };
  Object.defineProperty(el, 'className', {
    get: function () { return Object.keys(el._classes).join(' '); },
    set: function (v) { el._classes = {}; String(v).split(/\s+/).forEach(function (c) { if (c) { el._classes[c] = true; } }); }
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
  addEventListener: function (type, fn) { if (type === 'DOMContentLoaded') { __domReady.push(fn); } },
  documentElement: { dataset: {} }
};
var __domReady = [];
var window = {};
var location = { assign: function () {} };
function cssVar(name) { return 'var(' + name + ')'; }
function escHtml(s) { return String(s); }
function alert(text) { __log.alerts.push(String(text)); }

var __scenario = __SCENARIO__;
var __pendingRun = null;

function __response(spec) {
  return {
    ok: spec.status >= 200 && spec.status < 300,
    status: spec.status,
    headers: { get: function () { return spec.kind === 'html' ? 'text/html' : 'application/json'; } },
    json: function () {
      if (spec.kind === 'html') {
        return Promise.reject(new SyntaxError(__scenario.errorText || 'Unexpected token < in JSON at position 0'));
      }
      return Promise.resolve(spec.body);
    },
    text: function () {
      return Promise.resolve(spec.kind === 'html' ? '<html><body>refused</body></html>' : JSON.stringify(spec.body));
    }
  };
}

var __runIndex = 0;
function __nextRun() {
  if (Array.isArray(__scenario.run)) {
    return __scenario.run[Math.min(__runIndex++, __scenario.run.length - 1)];
  }
  return __scenario.run;
}

function fetch(url, opts) {
  __log.fetches.push({ url: String(url), opts: opts || null });
  if (String(url) === '__CSRF_URL__') {
    if (__scenario.csrf === 'fail') { return Promise.resolve(__response({ status: 500, kind: 'html' })); }
    if (__scenario.csrf === 'unparsable') { return Promise.resolve(__response({ status: 200, kind: 'html' })); }
    return Promise.resolve(__response({ status: 200, kind: 'json', body: { csrf_token: __scenario.token } }));
  }
  var spec = __nextRun();
  if (spec.mode === 'network-error') {
    return Promise.reject(new TypeError(__scenario.errorText || 'Failed to fetch'));
  }
  if (spec.mode === 'pending') {
    return new Promise(function (resolve) { __pendingRun = function () { resolve(__response(spec)); }; });
  }
  return Promise.resolve(__response(spec));
}

// The page loads the token holder before ai_advisor.js (templates/_chrome.html) and the holder reads
// its token once when the page loads.
__GUARD__
var PSRequest = window.PSRequest;

__FUNCTION__

async function __flush() { for (var i = 0; i < 8; i++) { await new Promise(function (r) { setTimeout(r, 2); }); } }
function __runCalls() { return __log.fetches.filter(function (f) { return f.url === '__RUN_URL__'; }); }
function __state() {
  return {
    disabled: __button.disabled,
    message: __message.textContent,
    runCalls: __runCalls().length,
    classes: Object.keys(__message._classes),
    style: JSON.stringify(__message.style)
  };
}

(async function () {
  var out = {};
  __domReady.forEach(function (fn) { fn(); });
  await __flush();
  __DRIVER__
  out.log = __log;
  console.log(JSON.stringify(out));
})().catch(function (e) { console.log(JSON.stringify({ crashed: String(e && e.stack || e) })); process.exit(0); });
"""


def _run(
    driver: str,
    *,
    run: dict | list[dict],
    csrf_preset: str | None = None,
    csrf: str = "ok",
    error_text: str | None = None,
) -> dict:
    if shutil.which("node") is None:
        pytest.skip("node not installed: the run button client cannot be executed")
    function = _extract_function(_source(), "retRunReview")
    script = (
        _HARNESS.replace("__BUTTON__", BUTTON_TESTID)
        .replace("__MESSAGE__", MESSAGE_TESTID)
        .replace(
            "__SCENARIO__",
            json.dumps(
                {
                    "run": run,
                    # The token the page's holder reads at load; none means the read fails.
                    "token": csrf_preset,
                    "csrf": csrf if csrf_preset is not None or csrf != "ok" else "fail",
                    "errorText": error_text,
                }
            ),
        )
        .replace("__GUARD__", _GUARD_JS_PATH.read_text(encoding="utf-8"))
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
        assert [f["url"] for f in out["log"]["fetches"]] == [CSRF_URL, RUN_URL], (
            "a token the page already holds must not be fetched again by the click"
        )

    def test_a_page_with_no_token_still_sends_one_post_with_an_empty_token_and_reads_none_first(
        self,
    ):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 403, "kind": "html"},
            csrf_preset=None,
        )
        runs = [f for f in out["log"]["fetches"] if f["url"] == RUN_URL]
        assert len(runs) == 1, "the refused request must not be sent a second time"
        assert _headers(runs[0]).get("x-csrf-token") == ""
        assert _headers(runs[0]).get("x-requested-with") == "XMLHttpRequest"
        first_run = out["log"]["fetches"].index(runs[0])
        assert [f["url"] for f in out["log"]["fetches"][:first_run]] == [CSRF_URL], (
            "the only token read before the click is the one the page made when it loaded"
        )

    def test_when_the_session_cannot_be_refreshed_the_refusal_says_so_and_the_button_is_released(
        self,
    ):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 403, "kind": "html"},
            csrf_preset=None,
            csrf="fail",
        )
        assert out["state"]["runCalls"] == 1
        assert out["state"]["disabled"] is False
        assert _says_not_sent(out["state"]["message"])
        assert re.search(r"connection", out["state"]["message"], re.I)


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
        if status == 403:
            assert _says_not_sent(out["state"]["message"])
        else:
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
        assert _says_not_sent(out["state"]["message"])
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


class TestARefusedSubmissionAnsweredWithStatus200:
    """The route answers 200 with ``{"error": <exception type name>}`` when the review could not
    be handed to its worker. The client reads the status first and must still show that text:
    "Request failed: HTTP 200" would send the operator looking for a failure that is not there."""

    def test_the_error_text_the_server_sent_is_shown_and_the_status_is_not_reported_as_the_failure(
        self,
    ):
        out = _run(
            _CLICK_AND_SETTLE,
            run={
                "mode": "status",
                "status": 200,
                "kind": "json",
                "body": {"error": "RuntimeError"},
            },
            csrf_preset="tok",
        )
        assert "RuntimeError" in out["state"]["message"]
        assert "HTTP 200" not in out["state"]["message"]
        assert out["state"]["disabled"] is False

    def test_it_is_styled_as_a_failure_and_never_written_as_markup(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 200, "kind": "json", "body": {"error": HOSTILE}},
            csrf_preset="tok",
        )
        assert HOSTILE in out["state"]["message"], "the server's text is what the panel shows"
        assert _is_negative(out["state"]), "an error the server reported is not an acknowledgement"
        assert not any("<img" in html for html in out["log"]["innerHtml"]), "text, never markup"

    @pytest.mark.parametrize(
        "body",
        [{}, {"status": "started"}, {"error": ""}, {"error": None}, {"error": 7}],
        ids=["empty object", "no error key", "empty error", "null error", "number error"],
    )
    def test_a_200_without_a_usable_error_text_is_reported_by_its_status(self, body):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 200, "kind": "json", "body": body},
            csrf_preset="tok",
        )
        assert out["state"]["message"] == "Request failed: HTTP 200"
        assert out["state"]["disabled"] is False

    def test_a_200_whose_body_is_not_json_is_reported_by_its_status_without_the_parser_message(
        self,
    ):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 200, "kind": "html"},
            csrf_preset="tok",
            error_text="Unexpected token LEAKMARK in JSON",
        )
        assert out["state"]["message"] == "Request failed: HTTP 200"
        assert "LEAKMARK" not in out["state"]["message"]


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

    def test_the_button_is_disabled_the_moment_it_is_pressed(self):
        driver = """
        retRunReview(__button);
        out.immediately = __state();
        await __flush();
        """
        out = _run(
            driver,
            run={"mode": "pending", "status": 202, "kind": "json", "body": {}},
            csrf_preset="tok",
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


# ---------------------------------------------------------------------------
# The message never carries the text of an error thrown on the way.
# ---------------------------------------------------------------------------

LEAKY_ERROR = "Unexpected token < in JSON: <html>secret-path /srv/state key=abc123</html>"


class TestThrownErrorsAreNeverShown:
    def test_a_network_error_shows_fixed_text_and_not_the_error_message(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "network-error"},
            csrf_preset="tok",
            error_text=LEAKY_ERROR,
        )
        message = out["state"]["message"]
        assert message.startswith("Request failed")
        for fragment in ("secret-path", "abc123", "<html>", "Unexpected token"):
            assert fragment not in message, f"the thrown error leaked into the panel: {message!r}"
        assert out["state"]["disabled"] is False

    def test_a_token_reply_that_cannot_be_parsed_shows_fixed_text_and_not_the_parser_message(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 403, "kind": "html"},
            csrf_preset=None,
            csrf="unparsable",
            error_text=LEAKY_ERROR,
        )
        message = out["state"]["message"]
        assert out["state"]["runCalls"] == 1, "the refused request is not sent a second time"
        assert _says_not_sent(message)
        for fragment in ("secret-path", "abc123", "<html>", "Unexpected token"):
            assert fragment not in message, f"the parser error leaked into the panel: {message!r}"
        assert out["state"]["disabled"] is False

    def test_the_block_comment_states_the_refusal_shapes_the_global_hooks_really_answer(self):
        source = _source()
        start = re.search(r"function\s+retRunReview\s*\(", source).start()
        lines = source[:start].splitlines()
        comment = []
        for line in reversed(lines):
            if line.strip().startswith("//"):
                comment.append(line.strip())
            elif comment or line.strip():
                break
        text = " ".join(reversed(comment))
        assert text, "retRunReview has no comment above it"
        # Which body shape each refusal has is read by the shared request helper, and the status tests
        # above run both shapes (a 401 JSON, a 403 HTML page); the comment names the two statuses.
        assert "401" in text, "the comment must name the 401 refusal of an unauthenticated request"
        assert "403" in text, "the comment must name the 403 refusal of a missing CSRF token"
        assert "refusal from the global hooks is an HTML page" not in text, (
            "the comment still claims every refusal is an HTML page"
        )


# ---------------------------------------------------------------------------
# A refusal is styled with the negative design token; an acknowledgement is not.
# ---------------------------------------------------------------------------


def _template_css_rules():
    source = (REPO_ROOT / "templates" / "ai_advisor.html").read_text(encoding="utf-8")
    style = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", source, flags=re.DOTALL))
    return re.findall(r"([^{}]+)\{([^{}]*)\}", style)


def _is_negative(state: dict) -> bool:
    """True when the message element carries the negative token inline or through a class
    whose own rule (matched as a whole class) uses it."""
    if "--studio-neg" in state["style"]:
        return True
    rules = _template_css_rules()
    for cls in state["classes"]:
        pattern = re.compile(rf"\.{re.escape(cls)}(?![\w-])")
        if any(pattern.search(sel) and "--studio-neg" in body for sel, body in rules):
            return True
    return False


class TestRefusalsAreStyledNegative:
    @pytest.mark.parametrize(
        "run",
        [
            {"mode": "status", "status": 401, "kind": "json", "body": {"error": "unauthenticated"}},
            {"mode": "status", "status": 403, "kind": "html"},
            {"mode": "status", "status": 500, "kind": "html"},
            {"mode": "network-error"},
        ],
        ids=["session expired", "request refused", "request failed", "network error"],
    )
    def test_a_refusal_uses_the_negative_token(self, run):
        out = _run(_CLICK_AND_SETTLE, run=run, csrf_preset="tok")
        assert _is_negative(out["state"]), (
            f"the message {out['state']['message']!r} is not styled negative: {out['state']}"
        )

    @pytest.mark.parametrize(
        "run",
        [
            {"mode": "status", "status": 202, "kind": "json", "body": {"status": "started"}},
            {
                "mode": "status",
                "status": 409,
                "kind": "json",
                "body": {"status": "already_running"},
            },
        ],
        ids=["started", "already running"],
    )
    def test_an_acknowledgement_or_an_already_running_notice_is_not_styled_negative(self, run):
        out = _run(_CLICK_AND_SETTLE, run=run, csrf_preset="tok")
        assert out["state"]["message"], "the message must be set for the check to mean anything"
        assert not _is_negative(out["state"])

    def test_a_refusal_styling_does_not_stay_on_the_message_of_the_next_success(self):
        driver = """
        retRunReview(__button);
        await __flush();
        out.afterRefusal = __state();
        retRunReview(__button);
        await __flush();
        out.afterSuccess = __state();
        """
        out = _run(
            driver,
            run=[
                {"mode": "status", "status": 403, "kind": "html"},
                {"mode": "status", "status": 202, "kind": "json", "body": {"status": "started"}},
            ],
            csrf_preset="tok",
        )
        assert _is_negative(out["afterRefusal"])
        assert out["afterSuccess"]["message"] == MESSAGES[202]
        assert not _is_negative(out["afterSuccess"]), "the refusal's styling is still on"


# ---------------------------------------------------------------------------
# A request made while the market is open is refused with its own message.
# ---------------------------------------------------------------------------

_TEMPLATE_PATH = REPO_ROOT / "templates" / "ai_advisor.html"


def _rendered_button_label() -> str:
    found = re.search(
        r'onclick="retRunReview\(this\)"[^>]*>\s*([^<]+?)\s*</button>',
        _TEMPLATE_PATH.read_text(encoding="utf-8"),
    )
    assert found, "the tenure review button was not found in the Retirement template"
    return found.group(1)


class TestARefusedClickNamesTheRenderedButton:
    def test_the_not_sent_message_ends_by_naming_the_button_the_operator_can_see(self):
        label = _rendered_button_label()

        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 403, "kind": "html"},
            csrf_preset="tok",
        )

        message = out["state"]["message"]
        # Every other control of the Advisor says "Click <label> again."; the verb is the same here.
        assert message.endswith(f"Click {label} again."), message
        assert "Run review now" not in message

    def test_the_source_names_no_button_that_is_not_rendered(self):
        label = _rendered_button_label()
        source = _source()
        assert f"Click {label} again." in source
        assert f"Choose {label} again." not in source
        assert "Click Run review now again" not in source


MARKET_OPEN_MESSAGE = (
    "Run tenure review is available outside market hours: "
    "the Composer API is shared with the live engine."
)
REFUSED_CLASS = "tenure-review-run-message--refused"
MARKET_OPEN_RUN = {
    "mode": "status",
    "status": 409,
    "kind": "json",
    "body": {"status": "market_open"},
}


class TestAMarketHoursRefusalIsSaidAsOne:
    def test_the_message_names_the_market_hours_and_is_styled_as_a_refusal(self):
        out = _run(_CLICK_AND_SETTLE, run=MARKET_OPEN_RUN, csrf_preset="tok")

        state = out["state"]
        assert state["message"] == MARKET_OPEN_MESSAGE
        assert REFUSED_CLASS in state["classes"]
        assert _is_negative(state), (
            "a refusal uses the negative token; an already-running notice does not"
        )

    def test_the_button_is_released_and_exactly_one_request_was_sent(self):
        out = _run(_CLICK_AND_SETTLE, run=MARKET_OPEN_RUN, csrf_preset="tok")

        assert out["state"]["disabled"] is False
        assert out["state"]["runCalls"] == 1, "a refused request is not sent again"

    def test_text_in_the_body_never_replaces_the_fixed_message(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={
                "mode": "status",
                "status": 409,
                "kind": "json",
                "body": {"status": "market_open", "message": HOSTILE, "error": HOSTILE},
            },
            csrf_preset="tok",
        )

        assert out["state"]["message"] == MARKET_OPEN_MESSAGE
        assert not any("<img" in html for html in out["log"]["innerHtml"])

    @pytest.mark.parametrize(
        "body",
        [{"status": "already_running"}, {"status": HOSTILE}, {}, {"status": "market_closed"}],
        ids=["already running", "hostile status", "empty", "another status"],
    )
    def test_any_other_409_still_says_a_review_is_already_running_and_is_not_a_refusal(self, body):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 409, "kind": "json", "body": body},
            csrf_preset="tok",
        )

        assert out["state"]["message"] == MESSAGES[409]
        assert REFUSED_CLASS not in out["state"]["classes"]
        assert not _is_negative(out["state"])

    def test_a_409_whose_body_cannot_be_read_still_says_a_review_is_already_running(self):
        out = _run(
            _CLICK_AND_SETTLE,
            run={"mode": "status", "status": 409, "kind": "html"},
            csrf_preset="tok",
        )

        assert out["state"]["message"] == MESSAGES[409]
        assert out["state"]["disabled"] is False

    def test_the_refusal_styling_does_not_stay_on_the_message_of_the_next_success(self):
        driver = """
        retRunReview(__button);
        await __flush();
        out.afterRefusal = __state();
        retRunReview(__button);
        await __flush();
        out.afterSuccess = __state();
        """
        out = _run(
            driver,
            run=[
                MARKET_OPEN_RUN,
                {"mode": "status", "status": 202, "kind": "json", "body": {"status": "started"}},
            ],
            csrf_preset="tok",
        )

        assert out["afterRefusal"]["message"] == MARKET_OPEN_MESSAGE
        assert out["afterSuccess"]["message"] == MESSAGES[202]
        assert REFUSED_CLASS not in out["afterSuccess"]["classes"]
        assert out["afterSuccess"]["runCalls"] == 2
