"""Run the real ``static/ai_advisor.js`` over the real ``/ai-advisor`` markup under node.

Two runners, both executing the shipped file unmodified except for one appended export line:

* ``call_exports`` evaluates the script in a bare context and calls named functions from the
  script's own closure (``fmtEtStamp``, ``tabNameFromHash``, ...). The script is one IIFE, so
  the harness inserts a line that publishes the requested names just before the closing
  ``})();``; nothing else in the source is touched. A name the script does not define comes
  back as ``{"missing": name}`` so the failure names it, never a silent pass.
* ``run_advisor_page`` parses the HTML the Flask test client returned into the DOM of
  ``tests/dashboard/_first_paint_dom.py`` (one DOM implementation, reused), fires
  ``DOMContentLoaded``, then drives steps (call a ``window`` function on an element, click,
  set a property, change the URL fragment, advance the virtual clock, change the fetch table;
  a step with ``"hold": true`` lets microtasks run but fires no timer, so a test can act in the
  middle of a wait)
  and reads what the page shows after each.

Test-double limits (said once): fetch answers from the route table the test passes in (a list
of specs answers one per call, the last repeating; an unlisted URL is refused); timers run on a
virtual clock (``setTimeout`` callbacks fire in time order when the page is idle, intervals
only when a step advances the clock); ``Date.now`` and ``new Date()`` read the virtual clock;
charts are not drawn (``Chart`` is undefined); layout does not exist, so "visible" means no
``hidden`` attribute and no ``display: none`` on the element or an ancestor. A rendered page is
judged on a screenshot at the gate, never here.

Not imported by any production module.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

from tests.dashboard._first_paint_dom import _HARNESS_JS, parse_markup

AI_ADVISOR_JS = pathlib.Path(__file__).resolve().parents[1] / "static" / "ai_advisor.js"
REQUEST_GUARD_JS = AI_ADVISOR_JS.with_name("request_guard.js")

_PRELUDE = _HARNESS_JS.split("const rootEl = build(cfg.tree);")[0]

# Answers for the requests the page makes on load, so a step never trips over them.
DEFAULT_ROUTES: dict = {
    "/api/csrf-token": {"body": {"csrf_token": "csrf-token-for-the-harness"}},
    "/api/autotune-runs": {"body": []},
    "/api/performance/symphonies": {"body": []},
    "/api/incubation": {"body": []},
}

_DRIVER = r"""
const rootEl = build(cfg.tree);
const htmlEl = rootEl.childNodes.find((c) => c.nodeType === 1 && c.tagName === 'HTML') || rootEl;
const bodyEl = queryAll(htmlEl, 'body', true)[0] || htmlEl;

const RealDate = Date;
let now = cfg.startMs;
function FakeDate(...a) {
  if (!(this instanceof FakeDate)) return new RealDate(now).toString();
  if (a.length === 0) return new RealDate(now);
  return new RealDate(...a);
}
FakeDate.now = () => now;
FakeDate.UTC = RealDate.UTC;
FakeDate.parse = RealDate.parse;
FakeDate.prototype = RealDate.prototype;

const timers = [];
let timerSeq = 0;
const docListeners = {};
const winListeners = {};
const requests = [];
const reloads = [];
const alerts = [];
const replaced = [];
const routes = JSON.parse(JSON.stringify(cfg.routes));

const document = {
  documentElement: htmlEl,
  body: bodyEl,
  readyState: 'loading',
  cookie: '',
  title: '',
  getElementById(id) { return queryAll(htmlEl, '[id="' + id + '"]', true)[0] || null; },
  querySelector(sel) { return queryAll(htmlEl, sel, true)[0] || null; },
  querySelectorAll(sel) { return queryAll(htmlEl, sel, false); },
  createElement(tag) { return new El(tag, {}); },
  createTextNode(t) { return new TextNode(String(t)); },
  addEventListener(t, f) { (docListeners[t] = docListeners[t] || []).push(f); },
  removeEventListener() {},
};

function mkResp(spec) {
  const status = spec.status === undefined ? 200 : spec.status;
  const body = spec.body === undefined ? {} : spec.body;
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => (spec.badJson ? Promise.reject(new SyntaxError('bad json')) : Promise.resolve(JSON.parse(JSON.stringify(body)))),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}
function specFor(url) {
  let spec = routes[url] !== undefined ? routes[url] : routes[url.split('?')[0]];
  if (Array.isArray(spec)) spec = spec.length > 1 ? spec.shift() : spec[0];
  return spec;
}
function fetchStub(url, opts) {
  opts = opts || {};
  const key = String(url);
  requests.push({
    url: key,
    method: String(opts.method || 'GET').toUpperCase(),
    headers: opts.headers || {},
    body: opts.body === undefined ? null : opts.body,
    at: now,
  });
  const spec = specFor(key);
  if (!spec || spec.reject) return Promise.reject(new TypeError('stub fetch has no answer for ' + key));
  return Promise.resolve(mkResp(spec));
}

const location = {
  hash: cfg.initialHash || '',
  href: 'http://localhost/ai-advisor' + (cfg.initialHash || ''),
  pathname: '/ai-advisor',
  search: '',
  reload() { reloads.push({ hash: location.hash, at: now }); },
};
const history = {
  replaceState(state, title, url) {
    replaced.push({ url: url === undefined ? null : String(url) });
    if (typeof url === 'string' && url.indexOf('#') >= 0) location.hash = url.slice(url.indexOf('#'));
  },
  pushState(state, title, url) {
    replaced.push({ url: url === undefined ? null : String(url), push: true });
    if (typeof url === 'string' && url.indexOf('#') >= 0) location.hash = url.slice(url.indexOf('#'));
  },
};

const sandbox = {
  document,
  location,
  history,
  console: {
    log() {}, info() {}, warn() {}, debug() {},
    error(...a) { errors.push(a.map(String).join(' ')); },
  },
  fetch: fetchStub,
  Date: FakeDate,
  // The token holder times its reads; monotonic time moves with the virtual clock.
  performance: { now: () => now - cfg.startMs + 1000 },
  setTimeout(fn, ms) { const id = ++timerSeq; timers.push({ id, at: now + (ms || 0), fn, interval: null }); return id; },
  setInterval(fn, ms) { const id = ++timerSeq; timers.push({ id, at: now + ms, fn, interval: ms }); return id; },
  clearTimeout(id) { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
  clearInterval(id) { const i = timers.findIndex((t) => t.id === id); if (i >= 0) timers.splice(i, 1); },
  getComputedStyle() { return { getPropertyValue() { return ''; } }; },
  sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  navigator: { userAgent: 'node' },
  confirm() { return true; },
  alert(m) { alerts.push(String(m)); },
  encodeURIComponent,
  decodeURIComponent,
  URLSearchParams,
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.addEventListener = (t, f) => { (winListeners[t] = winListeners[t] || []).push(f); };
sandbox.removeEventListener = () => {};
vm.createContext(sandbox);

const norm = (s) => String(s).replace(/\s+/g, ' ').trim();
const isHiddenSelf = (el) => el.hasAttribute('hidden') || el.style.display === 'none';
function visible(el) {
  for (let n = el; n && n.nodeType === 1; n = n.parentNode) { if (isHiddenSelf(n)) return false; }
  return true;
}
function describe(el) {
  const attrs = {};
  el._attrs.forEach((v, k) => { attrs[k] = v; });
  return {
    tag: el.tagName.toLowerCase(),
    text: norm(el.textContent),
    html: el.innerHTML,
    visible: visible(el),
    classes: el._cls(),
    attrs,
    value: el.value === undefined ? null : el.value,
    disabled: el.disabled,
  };
}
function observe() {
  const out = {};
  for (const [name, sel] of Object.entries(cfg.watch || {})) {
    out[name] = queryAll(htmlEl, sel, false).map(describe);
  }
  return { watched: out, hash: location.hash, reloads: reloads.length, replaced: replaced.slice(), alerts: alerts.slice() };
}

async function drain() { for (let i = 0; i < 30; i++) await new Promise((r) => setImmediate(r)); }
function fire(t) { try { t.fn(); } catch (e) { errors.push('timer: ' + String(e && e.stack || e)); } }
async function settle() {
  await drain();
  for (let guard = 0; guard < 600; guard++) {
    const due = timers.filter((t) => t.interval === null).sort((a, b) => a.at - b.at)[0];
    if (!due) break;
    now = Math.max(now, due.at);
    timers.splice(timers.indexOf(due), 1);
    fire(due);
    await drain();
  }
}
async function advance(ms, andSettle) {
  const end = now + ms;
  for (let guard = 0; guard < 600; guard++) {
    const next = timers.filter((t) => t.at <= end).sort((a, b) => a.at - b.at)[0];
    if (!next) break;
    now = Math.max(now, next.at);
    if (next.interval !== null) next.at += next.interval; else timers.splice(timers.indexOf(next), 1);
    fire(next);
    await drain();
  }
  now = end;
  if (andSettle) await settle();
}

function run(code, filename) {
  try { vm.runInContext(code, sandbox, { filename }); } catch (e) { errors.push(filename + ': ' + String(e && e.stack || e)); }
}
function pick(sel) {
  const el = queryAll(htmlEl, sel, true)[0];
  if (!el) throw new Error('no element matches ' + sel);
  return el;
}

(async () => {
  const out = { initial: null, loaded: null, steps: [], requests, errors, ready: false };
  run(cfg.guardScript, 'request_guard.js');
  run(cfg.script, 'ai_advisor.js');
  out.initial = observe();
  (docListeners['DOMContentLoaded'] || []).forEach((f) => {
    try { f.call(document, { type: 'DOMContentLoaded' }); } catch (e) { errors.push('DOMContentLoaded: ' + String(e && e.stack || e)); }
  });
  await settle();
  out.loaded = observe();
  for (const step of cfg.steps || []) {
    const startReq = requests.length;
    if (step.route) Object.assign(routes, JSON.parse(JSON.stringify(step.route)));
    try {
      if (step.op === 'call') {
        const fn = sandbox[step.fn];
        if (typeof fn !== 'function') throw new Error('window.' + step.fn + ' is not a function');
        let args = (step.args || []).slice();
        if (step.target) {
          const el = pick(step.target);
          if (args.includes('$target')) args = args.map((a) => (a === '$target' ? el : a));
          else args.unshift(el);
        }
        fn.apply(sandbox, args);
      } else if (step.op === 'click') {
        pick(step.selector).click();
      } else if (step.op === 'set') {
        pick(step.selector)[step.prop] = step.value;
      } else if (step.op === 'hashchange') {
        location.hash = step.hash;
        (winListeners['hashchange'] || []).forEach((f) => f({ type: 'hashchange' }));
      } else if (step.op === 'event') {
        pick(step.selector).dispatchEvent({ type: step.type, target: pick(step.selector), preventDefault() {}, stopPropagation() {} });
      } else if (step.op === 'advance') {
        await advance(step.ms, step.hold !== true);
      }
    } catch (e) { errors.push('step ' + step.op + ': ' + String(e && e.stack || e)); }
    if (step.hold === true) await drain(); else await settle();
    out.steps.push({ label: step.label || step.op, obs: observe(), requests: requests.slice(startReq), now });
  }
  out.ready = true;
  process.stdout.write(JSON.stringify(out), function () { process.exit(0); });
})();
"""

_EXPORTS_RUNNER = r"""
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const noop = () => {};
const ctx = {
  document: {
    addEventListener: noop, removeEventListener: noop,
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  },
  fetch: () => Promise.reject(new Error('no network in the export harness')),
  alert: noop, console, setTimeout, clearTimeout, setInterval: () => 0, clearInterval: noop,
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(input.source, ctx, { filename: 'ai_advisor.js' });
const t = ctx.__t || {};
const out = input.calls.map((c) => {
  if (typeof t[c.fn] !== 'function') return { missing: c.fn };
  try { return { value: t[c.fn](...c.args) }; }
  catch (e) { return { error: String(e && e.message || e) }; }
});
process.stdout.write(JSON.stringify(out));
"""

_IIFE_CLOSE = re.compile(r"\}\)\(\);?\s*$|\}\(\)\);?\s*$")


def require_node() -> str:
    node = shutil.which("node")
    assert node is not None, "node is required by the advisor page tests and was not found on PATH"
    return node


def call_exports(calls: list[dict]) -> list[dict]:
    """Call named functions of ``static/ai_advisor.js``'s closure.

    ``calls`` is ``[{"fn": name, "args": [...]}, ...]``; each result is ``{"value": v}``,
    ``{"error": message}`` or ``{"missing": name}``.
    """
    node = require_node()
    source = AI_ADVISOR_JS.read_text(encoding="utf-8")
    match = _IIFE_CLOSE.search(source)
    assert match is not None, "static/ai_advisor.js no longer ends with an IIFE close"
    names = sorted({c["fn"] for c in calls})
    export = (
        "window.__t = {"
        + ", ".join(f"{n}: typeof {n} === 'function' ? {n} : undefined" for n in names)
        + "};\n"
    )
    patched = source[: match.start()] + export + source[match.start() :]
    done = subprocess.run(
        [node, "-e", _EXPORTS_RUNNER],
        input=json.dumps({"source": patched, "calls": calls}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 0, f"export harness failed: {done.stderr[-2000:]}"
    return json.loads(done.stdout)


def call_export(fn: str, *args):
    """One call; the function must exist and must not throw."""
    result = call_exports([{"fn": fn, "args": list(args)}])[0]
    assert "missing" not in result, f"static/ai_advisor.js defines no function named {fn}"
    assert "error" not in result, f"{fn}{args!r} threw: {result['error']}"
    return result["value"]


# 2026-10-30 15:00:00 UTC, a Friday during New York trading hours.
DEFAULT_START_MS = 1793372400000


def run_advisor_page(
    html: str,
    tmp_path: pathlib.Path,
    *,
    steps: list[dict] | None = None,
    routes: dict | None = None,
    watch: dict | None = None,
    initial_hash: str = "",
    start_ms: int = DEFAULT_START_MS,
) -> dict:
    """Run ``static/ai_advisor.js`` over ``html`` and return what the page shows.

    ``watch`` maps a name to a selector; every observation lists, per name, the matching
    elements as ``{text, html, visible, classes, attrs, value, disabled}``. ``routes`` maps a URL
    to ``{"body": ..., "status": 200, "reject": False}`` (or a list of such specs). Returns
    ``{"initial", "loaded", "steps", "requests", "errors"}``; each step carries ``obs`` and the
    ``requests`` it made.
    """
    node = require_node()
    merged = {**DEFAULT_ROUTES, **(routes or {})}
    cfg = {
        "tree": parse_markup(html),
        # The page loads the token holder before ai_advisor.js (templates/_chrome.html).
        "guardScript": REQUEST_GUARD_JS.read_text(encoding="utf-8"),
        "script": AI_ADVISOR_JS.read_text(encoding="utf-8"),
        "routes": merged,
        "steps": steps or [],
        "watch": watch or {},
        "initialHash": initial_hash,
        "startMs": start_ms,
    }
    cfg_path = tmp_path / "advisor_page_cfg.json"
    harness_path = tmp_path / "advisor_page_harness.js"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    harness_path.write_text(_PRELUDE + _DRIVER, encoding="utf-8")
    done = subprocess.run(
        [node, str(harness_path), str(cfg_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert done.returncode == 0, f"advisor page harness failed:\n{done.stderr[-3000:]}"
    return json.loads(done.stdout)
