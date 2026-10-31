"""Run the shipped ``static/chrome.js`` under ``node`` against a stub document, unedited.

TEST-ONLY. The whole real file is evaluated (no function is copied out of it), so the header's
Force run now button and the Emergency Liquidate modal run the code the browser runs. The stub
document only records: element state (text, attributes, ``disabled``), every ``fetch`` call (URL
and the init object, byte for byte), every ``alert`` message and every unhandled promise
rejection. Responses are scripted per URL by the caller, and in this repository's tests they are
the answers the real Flask app gave to the same request (``tests/app/test_chrome_header_honesty``).

A scripted response is ``{"status": int, "content_type": str, "body": str}`` (the body is text,
exactly as the server sent it) or ``{"reject": "<message>"}`` for a request that fails before any
answer. ``json()`` parses the body and rejects when it is not JSON, as a browser's does.

Steps (``run`` takes a list):

* ``dom_ready``                  fire every ``DOMContentLoaded`` listener the script registered
* ``click_force``                click the Force run now button (its registered listener)
* ``open_panic`` / ``cancel`` / ``escape``   the modal's open path, Cancel's handler, the Escape key
* ``type_phrase`` (value)        set the phrase input, then run the markup's own ``oninput`` gate
* ``select_account`` (value)     choose an option in the account select
* ``confirm``                    click Confirm Liquidate (a disabled button is not clickable)
* ``flush``                      let pending promise callbacks run
* ``timers``                     run every pending ``setTimeout`` callback, recording its delay

After every step a snapshot of the observable state is appended to ``snapshots``.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
CHROME_JS = REPO / "static" / "chrome.js"
REQUEST_GUARD_JS = REPO / "static" / "request_guard.js"
NODE_TIMEOUT_SECONDS = 60

PANIC_ACCOUNT_ID = "panic-account"
PANIC_PHRASE_ID = "panic-phrase"
PANIC_CONFIRM_ID = "panic-confirm-btn"
PANIC_MODAL_ID = "panic-modal"
FORCE_RUN_SELECTOR = '[data-testid="force-run-btn"]'

_RUNNER = r"""
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));

const alerts = [];
const fetchCalls = [];
const unhandled = [];
const timers = [];
const docListeners = {};
process.on('unhandledRejection', (reason) => {
  unhandled.push(String((reason && reason.message) || reason));
});

function mkEl(id, extra) {
  const attrs = {};
  const el = {
    id, textContent: '', value: '', disabled: false, style: {}, dataset: {}, listeners: {},
    _html: '',
    setAttribute(n, v) { attrs[n] = String(v); },
    removeAttribute(n) { delete attrs[n]; },
    hasAttribute(n) { return n in attrs; },
    getAttribute(n) { return n in attrs ? attrs[n] : null; },
    addEventListener(t, f) { (el.listeners[t] = el.listeners[t] || []).push(f); },
    appendChild() {}, querySelector() { return null; }, querySelectorAll() { return []; },
    contains() { return false; }, classList: { add() {}, remove() {} },
  };
  Object.defineProperty(el, 'innerHTML', {
    get() { return el._html; },
    set(v) {
      el._html = String(v);
      if (el.isSelect) {
        const m = /<option value="([^"]*)"/.exec(el._html);
        el.value = m ? m[1] : '';
      }
    },
  });
  return Object.assign(el, extra || {});
}

const els = {};
els[input.ids.modal] = mkEl(input.ids.modal);
els[input.ids.modal].setAttribute('hidden', '');
els[input.ids.account] = mkEl(input.ids.account, { isSelect: true });
els[input.ids.account].innerHTML = '<option value="">Loading accounts…</option>';
els[input.ids.phrase] = mkEl(input.ids.phrase);
els[input.ids.confirm] = mkEl(input.ids.confirm, { disabled: true });
const forceBtn = mkEl('force-run-btn', { textContent: 'Force run now' });

const document = {
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
  getElementById(id) { return Object.prototype.hasOwnProperty.call(els, id) ? els[id] : null; },
  querySelector(sel) { return sel === input.forceSelector ? forceBtn : null; },
  querySelectorAll() { return []; },
  createElement() { return mkEl('created'); },
  documentElement: { dataset: {}, getAttribute() { return null; } },
};

function respond(spec) {
  if (spec.reject !== undefined) return Promise.reject(new TypeError(spec.reject));
  const headers = { get(n) { return String(n).toLowerCase() === 'content-type' ? spec.content_type : null; } };
  return Promise.resolve({
    status: spec.status, ok: spec.status >= 200 && spec.status < 300, headers,
    json() { try { return Promise.resolve(JSON.parse(spec.body)); } catch (e) { return Promise.reject(e); } },
    text() { return Promise.resolve(spec.body); },
  });
}

const queues = {};
Object.keys(input.responses).forEach((url) => { queues[url] = input.responses[url].slice(); });
function fakeFetch(url, init) {
  fetchCalls.push({ url: String(url), init: init === undefined ? null : JSON.parse(JSON.stringify(init)) });
  const q = queues[String(url)];
  if (!q || q.length === 0) {
    return respond({ status: 200, content_type: 'application/json', body: '{}' });
  }
  return respond(q.length > 1 ? q.shift() : q[0]);
}

const ctx = {
  document, fetch: fakeFetch, console,
  alert(msg) { alerts.push(String(msg)); },
  setInterval() { return 0; }, clearInterval() {},
  setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; }, clearTimeout() {},
  loadState: undefined,
  performance: { now: () => 1000 },
  location: { assign() {} },
};
ctx.window = ctx;
vm.createContext(ctx);
// The page loads the token holder before chrome.js (templates/_chrome.html).
vm.runInContext(input.guardSource, ctx, { filename: 'request_guard.js' });
vm.runInContext(input.source, ctx, { filename: 'chrome.js' });

function snap() {
  return {
    force_text: forceBtn.textContent, force_disabled: forceBtn.disabled,
    modal_hidden: els[input.ids.modal].hasAttribute('hidden'),
    confirm_disabled: els[input.ids.confirm].disabled,
    account_html: els[input.ids.account].innerHTML, account_value: els[input.ids.account].value,
    phrase_value: els[input.ids.phrase].value,
    alerts: alerts.slice(), unhandled: unhandled.slice(), timer_count: timers.length,
  };
}
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise((r) => setImmediate(r)); };

(async () => {
  const snapshots = [];
  const ranTimers = [];
  for (const step of input.steps) {
    if (step.op === 'dom_ready') {
      (docListeners.DOMContentLoaded || []).forEach((fn) => fn({}));
    } else if (step.op === 'click_force') {
      (forceBtn.listeners.click || []).forEach((fn) => fn({}));
    } else if (step.op === 'open_panic') {
      ctx.openPanicModal();
    } else if (step.op === 'cancel') {
      ctx.closePanicModal();
    } else if (step.op === 'escape') {
      (docListeners.keydown || []).forEach((fn) => fn({ key: 'Escape' }));
    } else if (step.op === 'type_phrase') {
      els[input.ids.phrase].value = step.value;
      // The markup's own gate (templates/_chrome.html oninput), evaluated with `this` = the input.
      vm.runInContext('(function () { ' + input.oninput + ' })', ctx).call(els[input.ids.phrase]);
    } else if (step.op === 'select_account') {
      els[input.ids.account].value = step.value;
    } else if (step.op === 'confirm') {
      if (!els[input.ids.confirm].disabled) ctx.submitPanicLiquidation();
    } else if (step.op === 'flush') {
      await flush();
    } else if (step.op === 'timers') {
      const pending = timers.splice(0, timers.length);
      pending.forEach((t) => { ranTimers.push(t.ms); t.fn(); });
    } else {
      throw new Error('unknown step ' + step.op);
    }
    await flush();
    snapshots.push(Object.assign({ op: step.op }, snap()));
  }
  process.stdout.write(JSON.stringify({ fetch_calls: fetchCalls, snapshots, ran_timer_delays: ranTimers }));
})();
"""


def node_available() -> bool:
    return shutil.which("node") is not None


def run(
    steps: list[dict],
    *,
    responses: dict[str, list[dict]],
    oninput: str,
    source: str | None = None,
) -> dict:
    """Run ``static/chrome.js`` (or ``source``) through ``steps``; return the recorded state.

    ``responses`` maps a request URL to the scripted answers for it, in order (the last one
    repeats). A URL with no script answers ``200 {}``. ``oninput`` is the confirm-gate attribute
    value read from the rendered markup.
    """
    assert node_available(), "node is not installed: the chrome.js harness cannot run"
    payload = {
        "guardSource": REQUEST_GUARD_JS.read_text(encoding="utf-8"),
        "source": CHROME_JS.read_text(encoding="utf-8") if source is None else source,
        "steps": steps,
        "responses": responses,
        "oninput": oninput,
        "forceSelector": FORCE_RUN_SELECTOR,
        "ids": {
            "modal": PANIC_MODAL_ID,
            "account": PANIC_ACCOUNT_ID,
            "phrase": PANIC_PHRASE_ID,
            "confirm": PANIC_CONFIRM_ID,
        },
    }
    result = subprocess.run(
        ["node", "-e", _RUNNER],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=NODE_TIMEOUT_SECONDS,
    )
    assert result.returncode == 0, f"chrome.js harness crashed:\n{result.stderr}"
    return json.loads(result.stdout)


def calls_to(out: dict, url: str) -> list[dict]:
    return [c for c in out["fetch_calls"] if c["url"] == url]
