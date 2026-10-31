"""Run the real ``static/`` scripts of a rendered page in one node process, unmodified.

TEST-ONLY. The page is the HTML the Flask test client returned; the scripts are the ``<script src>``
files that HTML names, in the order it names them, so a script a template forgets to load is
missing here exactly as it is in the browser. Nothing a script does is re-implemented: the DOM is
the small parser-backed DOM of ``tests/ui/test_static_js_page_harness.py`` (its prefix is reused,
not copied), and everything else is recorded.

What this adds to that DOM
--------------------------
* Virtual time. ``performance.now()`` and ``Date`` move together and only when a scenario says so
  (``H.advance(ms)`` runs every timer that falls due, in order; ``H.sleep(ms)`` jumps both clocks
  with no timer running). ``setTimeout``/``setInterval`` are virtual; no real timer ever fires.
* A request recorder. Every ``fetch`` is recorded with its URL, method, lower-cased headers, body
  and ``keepalive``. Answers come from ``routes``: ``{"pattern": regex, "method": "GET"|"POST",
  "responses": [spec, ...]}``. A spec is ``{status, json | text, headers, date, delay_ms, hold,
  reject}`` (``hold`` waits for ``H.release(name)``; ``delay_ms`` waits that much virtual time; either
  may be combined with ``reject`` to refuse the request late). A response carries a ``Date`` header stamped from the virtual wall clock when it is
  delivered (``date: None`` omits it, a number is milliseconds since the epoch). The last spec
  repeats unless the route is ``strict``, in which case each spec answers once and a request
  after the last is unrouted. A request no route matches is refused as a network error and listed
  in ``unrouted``.
* The money rule. A POST is legal only inside an operator action (``H.click``, ``H.act``): a POST
  with no action since the last ``H.advance``, or a second POST for one action, is a violation.
  ``run_page`` fails the test on any violation, so no control can pass by re-sending itself.
* ``H.surfaced()``: everything an operator can read (alerts plus the page's text), for a message
  whose place the contract does not fix.

A missing ``node`` fails the test; it never skips. The driver writes its result with
``process.stdout.write`` and lets the process end on its own, so a large result is never cut.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import patch

from tests.ui.test_static_js_page_harness import _DRIVER as _DOM_DRIVER

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "static"

# The DOM, the document and the console come from the shared driver, up to the point where it
# starts its own timers and fetch. The assertion makes a change to that file fail loudly here.
_SPLIT = "const intervals = [];"
assert _SPLIT in _DOM_DRIVER, "tests/ui/test_static_js_page_harness.py changed its driver layout"
_DOM_PREFIX = _DOM_DRIVER.split(_SPLIT, 1)[0]

DEFAULT_WALL = "2026-10-29T15:00:00Z"
DEFAULT_MONO_MS = 1000.0
DEFAULT_TOKEN = "token-A-0000000000000000000000000000000000000000"
NODE_TIMEOUT_SECONDS = 180
# Pages load these but a scenario never needs them: theme prepaint and external libraries.
_SKIPPED_SCRIPTS = ("tweaks.js",)

_TAIL = r"""
// ---- element behaviour the shared DOM leaves out ----
Object.defineProperty(El.prototype, 'disabled', {
  get() { return this.hasAttribute('disabled'); },
  set(v) { if (v) this.setAttribute('disabled', ''); else this.removeAttribute('disabled'); },
  configurable: true,
});
El.prototype.contains = function (n) { for (; n; n = n.parentNode) { if (n === this) return true; } return false; };
Object.defineProperty(El.prototype, 'firstElementChild', { get() { return this.children[0] || null; }, configurable: true });
El.prototype.scrollIntoView = function () {};
El.prototype.getBoundingClientRect = function () { return { top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0 }; };
El.prototype.append = function () { Array.from(arguments).forEach((n) => this.appendChild(typeof n === 'string' ? new TextNode(n) : n)); };
El.prototype.insertAdjacentHTML = function (where, html) {
  const holder = new El('div');
  parseHTML(String(html), holder);
  const kids = holder.childNodes.slice();
  if (where === 'afterbegin') kids.reverse().forEach((k) => this.insertBefore(k, this.firstChild));
  else kids.forEach((k) => this.appendChild(k));
};

// ---- clocks ----
const RealDate = Date;
let wallMs = RealDate.parse(input.wall);
let mono = input.mono_ms;
class FakeDate extends RealDate {
  constructor(...a) { if (a.length === 0) super(wallMs); else super(...a); }
  static now() { return wallMs; }
}
globalThis.Date = FakeDate;
Object.defineProperty(globalThis, 'performance', { value: { now: function () { return mono; } }, configurable: true, writable: true });

// ---- virtual timers ----
let timerSeq = 0;
let timers = [];
globalThis.setTimeout = function (fn, ms) {
  const id = ++timerSeq; const d = Number(ms) || 0;
  timers.push({ id: id, kind: 't', fn: fn, ms: d, due: mono + d, args: Array.prototype.slice.call(arguments, 2) });
  return id;
};
globalThis.setInterval = function (fn, ms) {
  const id = ++timerSeq; const d = Number(ms) || 0;
  timers.push({ id: id, kind: 'i', fn: fn, ms: d, due: mono + Math.max(d, 1), args: [] });
  return id;
};
globalThis.clearTimeout = globalThis.clearInterval = function (id) { timers = timers.filter(function (t) { return t.id !== id; }); };
globalThis.requestAnimationFrame = function (fn) { return globalThis.setTimeout(fn, 16); };
globalThis.cancelAnimationFrame = globalThis.clearTimeout;

// ---- browser surface the page scripts touch ----
const windowListeners = {};
globalThis.addEventListener = function (t, fn) { (windowListeners[t] = windowListeners[t] || []).push(fn); };
globalThis.removeEventListener = function (t, fn) { windowListeners[t] = (windowListeners[t] || []).filter(function (f) { return f !== fn; }); };
globalThis.dispatchEvent = function (ev) { (windowListeners[ev.type] || []).slice().forEach(function (f) { f(ev); }); return true; };
globalThis.CustomEvent = globalThis.Event = class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } };
const store = function () { const m = {}; return { getItem: function (k) { return k in m ? m[k] : null; }, setItem: function (k, v) { m[k] = String(v); }, removeItem: function (k) { delete m[k]; } }; };
globalThis.localStorage = store();
globalThis.sessionStorage = store();
globalThis.matchMedia = function () { return { matches: false, addEventListener: function () {}, addListener: function () {} }; };
Object.defineProperty(globalThis, 'navigator', { value: { userAgent: 'harness', onLine: true }, configurable: true, writable: true });
globalThis.IntersectionObserver = globalThis.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
globalThis.EventSource = class { constructor(url) { this.url = url; this.readyState = 0; } addEventListener() {} close() { this.readyState = 2; } };
doc.visibilityState = 'visible';
doc.hidden = false;
const navigations = [];
const alerts = [];
const confirms = [];
globalThis.alert = function (m) { alerts.push(String(m)); };
globalThis.confirm = function (m) { confirms.push(String(m)); return input.confirm !== false; };
globalThis.location = {
  pathname: input.pathname, search: '', hash: '', origin: 'http://127.0.0.1', _href: 'http://127.0.0.1' + input.pathname,
  get href() { return this._href; },
  set href(v) { navigations.push('href:' + v); },
  assign: function (u) { navigations.push('assign:' + u); },
  replace: function (u) { navigations.push('replace:' + u); },
  reload: function () { navigations.push('reload'); },
};
if (input.chart === 'stub') {
  const charts = [];
  globalThis.Chart = class {
    constructor(ctx, cfg) { this.config = cfg; this.data = cfg.data; this.options = cfg.options; charts.push(this); }
    update() {} destroy() {} static register() {}
  };
  globalThis.__charts = charts;
} else {
  delete globalThis.Chart;
}

// ---- fetch recorder ----
const requests = [];
const violations = [];
const unrouted = [];
const held = {};
let currentAction = null;
let actionSeq = 0;
let currentToken = input.token;
const routes = input.routes.map(function (r) { return Object.assign({ n: 0, queue: (r.responses || []).slice() }, r); });
function headersOf(init) {
  const h = {};
  Object.keys((init && init.headers) || {}).forEach(function (k) { h[k.toLowerCase()] = init.headers[k]; });
  return h;
}
function makeResponse(spec) {
  const status = spec.status === undefined ? 200 : spec.status;
  const isText = spec.text !== undefined;
  const headers = { 'content-type': isText ? 'text/html; charset=utf-8' : 'application/json' };
  if (spec.date === undefined) headers['date'] = new RealDate(wallMs).toUTCString();
  else if (spec.date !== null) headers['date'] = new RealDate(spec.date).toUTCString();
  Object.keys(spec.headers || {}).forEach(function (k) { headers[k.toLowerCase()] = spec.headers[k]; });
  const raw = isText ? spec.text : JSON.stringify(spec.json === undefined ? null : spec.json);
  return {
    ok: status >= 200 && status < 300, status: status, statusText: '',
    headers: { get: function (n) { const v = headers[String(n).toLowerCase()]; return v === undefined ? null : v; } },
    json: function () { try { return Promise.resolve(JSON.parse(raw)); } catch (e) { return Promise.reject(e); } },
    text: function () { return Promise.resolve(raw); },
    clone: function () { return makeResponse(spec); },
  };
}
globalThis.fetch = function (url, init) {
  url = String(url);
  const method = String((init && init.method) || 'GET').toUpperCase();
  const rec = { seq: requests.length, url: url, method: method, headers: headersOf(init), body: init && init.body !== undefined ? init.body : null,
                keepalive: !!(init && init.keepalive), action: currentAction ? currentAction.id : null, at: mono, initKeys: Object.keys(init || {}).sort() };
  requests.push(rec);
  if (method === 'POST') {
    if (!currentAction) violations.push('POST ' + url + ' was sent with no operator click before it');
    else if (currentAction.posts >= 1) violations.push('POST ' + url + ' was sent a second time for one click');
    if (currentAction) currentAction.posts += 1;
  }
  for (let i = 0; i < routes.length; i++) {
    const r = routes[i];
    if (r.method && r.method !== method) continue;
    if (!new RegExp(r.pattern).test(url)) continue;
    let spec;
    if (r.strict) { if (!r.queue.length) break; spec = r.queue.shift(); }
    else { spec = r.responses[Math.min(r.n, r.responses.length - 1)]; r.n += 1; }
    if (spec.token_dynamic) spec = { json: { csrf_token: currentToken } };
    const finish = function (resolve, reject) {
      if (spec.reject) reject(new TypeError(spec.reject === true ? 'Failed to fetch' : String(spec.reject)));
      else resolve(makeResponse(spec));
    };
    if (spec.hold) return new Promise(function (resolve, reject) { held[spec.hold] = function () { finish(resolve, reject); }; });
    if (spec.delay_ms) return new Promise(function (resolve, reject) { globalThis.setTimeout(function () { finish(resolve, reject); }, spec.delay_ms); });
    return new Promise(finish);
  }
  unrouted.push(method + ' ' + url);
  return Promise.reject(new TypeError('Failed to fetch'));
};

// ---- scripts, in page order ----
const loadErrors = [];
input.scripts.forEach(function (s) {
  try { vm.runInThisContext(s.src, { filename: s.name }); }
  catch (e) { loadErrors.push(s.name + ': ' + String(e && e.stack || e)); }
});

function visible(el) {
  for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
    if (n.hidden) return false;
    if (n.style && n.style.display === 'none') return false;
  }
  return true;
}
async function settle(n) { for (let i = 0; i < (n || 8); i++) await new Promise(function (r) { setImmediate(r); }); }
function step(d) { if (d > 0) { mono += d; wallMs += d; } }
function pageText() {
  const parts = [];
  (function walk(n) {
    if (n.nodeType === 3) { parts.push(n.data); return; }
    if (n.tagName === 'SCRIPT' || n.tagName === 'STYLE') return;
    n.childNodes.forEach(walk);
  })(doc.body);
  return parts.join(' ').replace(/\s+/g, ' ').trim();
}

const H = {
  document: doc, requests: requests, settle: settle, visible: visible, navigations: navigations, alerts: alerts, confirms: confirms,
  errors: errors, unrouted: unrouted, loadErrors: loadErrors,
  $: function (s) { return doc.querySelector(s); },
  $$: function (s) { return Array.from(doc.querySelectorAll(s)); },
  text: function (s) { const e = doc.querySelector(s); return e ? e.textContent : null; },
  attr: function (s, a) { const e = doc.querySelector(s); return e ? e.getAttribute(a) : null; },
  isVisible: function (s) { const e = doc.querySelector(s); return !!e && visible(e); },
  value: function (s) { const e = doc.querySelector(s); return e ? e.value : null; },
  pageText: pageText,
  surfaced: function () { return alerts.concat([pageText()]).join(' | '); },
  win: function (name) { return globalThis[name]; },
  boot: async function () {
    doc.readyState = 'complete';
    (doc._listeners.DOMContentLoaded || []).slice().forEach(function (fn) { fn.call(doc, mkEvent('DOMContentLoaded', doc)); });
    await settle();
  },
  // An operator action: the only thing that may be followed by a POST, one POST per action.
  act: async function (label, fn) {
    currentAction = { id: ++actionSeq, label: label, posts: 0 };
    await fn();
    await settle();
  },
  click: async function (s) {
    const e = doc.querySelector(s);
    if (!e) throw new Error('click: no element for ' + s);
    currentAction = { id: ++actionSeq, label: 'click ' + s, posts: 0 };
    if (e.hasAttribute('disabled') || e.disabled === true) { await settle(); return false; }
    e.dispatchEvent(mkEvent('click', e));
    await settle();
    return true;
  },
  set: async function (s, v) {
    const e = doc.querySelector(s);
    if (!e) throw new Error('set: no element for ' + s);
    e.value = v;
    e.dispatchEvent(mkEvent('input', e));
    e.dispatchEvent(mkEvent('change', e));
    await settle();
  },
  advance: async function (ms) {
    currentAction = null;
    const end = mono + ms;
    for (;;) {
      const due = timers.filter(function (t) { return t.due <= end; }).sort(function (a, b) { return a.due - b.due || a.id - b.id; });
      if (!due.length) break;
      const t = due[0];
      step(t.due - mono);
      if (t.kind === 't') timers = timers.filter(function (x) { return x !== t; }); else t.due += Math.max(t.ms, 1);
      try { t.fn.apply(null, t.args || []); } catch (e) { errors.push('timer threw: ' + String(e && e.stack || e)); }
      await settle(3);
    }
    step(end - mono);
    await settle();
  },
  // Both clocks jump and no timer runs (a closed laptop); the next advance runs what is overdue.
  sleep: function (ms) { currentAction = null; step(ms); },
  release: async function (name) { held[name](); await settle(); },
  // The daemon restarted: from now on GET /api/csrf-token answers `token` (a route given {token_dynamic: true}).
  setToken: function (token) { currentToken = token; },
  fireWindow: async function (type, props) { globalThis.dispatchEvent(Object.assign({ type: type }, props || {})); await settle(); },
  fireDocument: async function (type, props) {
    (doc._listeners[type] || []).slice().forEach(function (fn) { fn.call(doc, Object.assign(mkEvent(type, doc), props || {})); });
    await settle();
  },
  setVisibility: async function (state) { doc.visibilityState = state; doc.hidden = state === 'hidden'; await H.fireDocument('visibilitychange'); },
  to: function (re, method) { return requests.filter(function (r) { return new RegExp(re).test(r.url) && (!method || r.method === method); }); },
  posts: function (re) { return requests.filter(function (r) { return r.method === 'POST' && (!re || new RegExp(re).test(r.url)); }); },
  timerCount: function () { return timers.length; },
  charts: function () { return globalThis.__charts || []; },
};

(async function () {
  let out;
  try {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const result = await new AsyncFunction('H', input.scenario)(H);
    out = { ok: true, result: result === undefined ? null : result };
  } catch (e) {
    out = { ok: false, error: String(e && e.stack || e) };
  }
  out.requests = requests; out.violations = violations; out.navigations = navigations; out.alerts = alerts;
  out.confirms = confirms; out.errors = errors; out.unrouted = unrouted; out.loadErrors = loadErrors;
  out.unconsumed = routes.filter(function (r) { return r.strict; }).reduce(function (n, r) { return n + r.queue.length; }, 0);
  process.stdout.write('\n__RESULT__' + JSON.stringify(out) + '\n');
})();
"""


def node_binary() -> str:
    path = shutil.which("node")
    assert path, "node is not installed: the page-script harness cannot run"
    return path


def page_scripts(html: str) -> list[str]:
    """The ``static/`` files a page's ``<script src>`` tags name, in order (externals dropped)."""
    names = []
    for match in re.finditer(r"<script[^>]*\ssrc=\"([^\"]+)\"", html):
        src = match.group(1)
        if "/static/" not in src:
            continue
        name = src.split("/static/", 1)[1].split("?", 1)[0]
        if name not in _SKIPPED_SCRIPTS:
            names.append(name)
    return names


def render_page(path: str) -> str:
    """The HTML ``GET <path>`` returns, rendered by the real app with the developer .env unread."""
    import app as app_module

    with (
        patch.object(app_module, "ENV_FILE_PATH", str(Path(tempfile.gettempdir()) / "no-such.env")),
        app_module.app.test_client() as client,
    ):
        response = client.get(path)
        assert response.status_code == 200, f"GET {path} answered {response.status_code}"
        return response.get_data(as_text=True)


def answer(response, **extra) -> dict:
    """A route spec holding exactly what a Flask test-client response carried."""
    spec: dict = {"status": response.status_code}
    if "json" in (response.headers.get("Content-Type") or ""):
        spec["json"] = response.get_json()
    else:
        spec["text"] = response.get_data(as_text=True)
    spec.update(extra)
    return spec


def route(pattern: str, *responses: dict, method: str | None = None, strict: bool = False) -> dict:
    return {"pattern": pattern, "method": method, "responses": list(responses), "strict": strict}


def default_routes() -> list[dict]:
    """Answers for the requests every page makes on load that a scenario is not about.

    The token route answers whatever token the daemon holds now: the start token until a scenario
    calls ``H.setToken(...)`` (a restart), the new one after.
    """
    return [
        route(r"/api/csrf-token$", {"token_dynamic": True}, method="GET"),
        route(
            r"/api/candidate-alert$",
            {"json": {"new_valid_count": 0, "last_run": None}},
            method="GET",
        ),
        route(r"/api/accounts$", {"json": {"accounts": []}}, method="GET"),
    ]


@dataclass
class PageRun:
    result: object
    requests: list[dict]
    navigations: list[str]
    alerts: list[str]
    errors: list[str]
    unrouted: list[str]
    confirms: list[str] = field(default_factory=list)

    def to(self, pattern: str, method: str | None = None) -> list[dict]:
        return [
            r
            for r in self.requests
            if re.search(pattern, r["url"]) and (method is None or r["method"] == method)
        ]

    def posts(self, pattern: str | None = None) -> list[dict]:
        return [
            r
            for r in self.requests
            if r["method"] == "POST" and (pattern is None or re.search(pattern, r["url"]))
        ]


def run_page(
    html: str,
    scenario: str,
    *,
    routes: list[dict] | None = None,
    scripts: list[str] | None = None,
    pathname: str = "/history",
    chart: str = "stub",
    wall: str = DEFAULT_WALL,
    mono_ms: float = DEFAULT_MONO_MS,
    confirm: bool = True,
    token: str = DEFAULT_TOKEN,
) -> PageRun:
    """Run ``scenario`` (an async function body taking ``H``) over the page; return what happened.

    The test fails here, not in the scenario, when a script failed to load or threw at load, when a
    POST was sent without an operator click or twice for one click, or when a ``strict`` route was
    left with an unconsumed answer. ``scripts`` defaults to the page's own ``<script src>`` list.
    """
    names = page_scripts(html) if scripts is None else scripts
    payload = {
        "html": html,
        "scripts": [
            {"name": name, "src": (STATIC / name).read_text(encoding="utf-8")} for name in names
        ],
        "routes": (routes or []) + default_routes(),
        "scenario": scenario,
        "pathname": pathname,
        "chart": chart,
        "wall": wall,
        "mono_ms": mono_ms,
        "confirm": confirm,
        "token": token,
    }
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "driver.js").write_text(_DOM_PREFIX + _TAIL, encoding="utf-8")
        (base / "input.json").write_text(json.dumps(payload), encoding="utf-8")
        proc = subprocess.run(
            [node_binary(), str(base / "driver.js"), str(base / "input.json")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=NODE_TIMEOUT_SECONDS,
        )
    marker = proc.stdout.rfind("__RESULT__")
    assert marker >= 0, f"node produced no result (exit {proc.returncode}):\n{proc.stderr[-2000:]}"
    out = json.loads(proc.stdout[marker + len("__RESULT__") :].strip().splitlines()[0])
    assert not out["loadErrors"], f"a page script failed to load: {out['loadErrors']}"
    assert out["ok"], f"scenario threw: {out.get('error')}"
    assert not out["violations"], f"a POST broke the click rule: {out['violations']}"
    assert out["unconsumed"] == 0, f"{out['unconsumed']} queued answer(s) were never consumed"
    return PageRun(
        result=out["result"],
        requests=out["requests"],
        navigations=out["navigations"],
        alerts=out["alerts"],
        errors=out["errors"],
        unrouted=out["unrouted"],
        confirms=out["confirms"],
    )
