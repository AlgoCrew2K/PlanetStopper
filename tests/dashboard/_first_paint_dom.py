"""Test harness: run the real dashboard client code against the real server markup.

Why this exists: the dashboard's first-paint text (written by the server) and its
refreshed text (written by ``static/index.js`` and the inline script of
``templates/index.html``) must agree.  The harness parses the HTML the Flask test
client actually returned into a small DOM, runs the inline script and the whole of
``static/index.js`` over it under node, answers ``fetch`` from a route table, and
observes the DOM before and after each step.  No client function is re-implemented
here: every text the tests compare was written by the code under test.

Test-double limits (said once, so no reader trusts them for more): the DOM supports
the selector subset the dashboard code uses, ``textContent`` replaces children as a
browser does, and ``hidden`` and ``style.display`` are honoured by the visible-text
reader.  Charts are not drawn (``Chart`` is undefined, so chart renderers fail inside
their own guarded paths).  A visible render is judged on a live page, never here.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from html.parser import HTMLParser

_REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX_JS = _REPO / "static" / "index.js"

_VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict = {"tag": "#root", "attrs": {}, "children": []}
        self._stack = [self.root]

    @staticmethod
    def _node(tag: str, attrs) -> dict:
        return {
            "tag": tag,
            "attrs": {k: ("" if v is None else v) for k, v in attrs},
            "children": [],
        }

    def handle_starttag(self, tag, attrs):
        node = self._node(tag, attrs)
        self._stack[-1]["children"].append(node)
        if tag not in _VOID:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._stack[-1]["children"].append(self._node(tag, attrs))

    def handle_endtag(self, tag):
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i]["tag"] == tag:
                del self._stack[i:]
                return

    def handle_data(self, data):
        self._stack[-1]["children"].append({"text": data})


def parse_markup(html: str) -> dict:
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root


def require_node() -> str:
    node = shutil.which("node")
    assert node is not None, (
        "node is required by the dashboard render tests and was not found on PATH"
    )
    return node


def js_to_fixed(values: list[float], places: int) -> list[str]:
    """``Number.prototype.toFixed`` for each value, evaluated by node (the rounding
    rule the server text must equal)."""
    node = require_node()
    program = (
        "const v = JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify(v.map(x => x.toFixed(" + str(int(places)) + "))));"
    )
    done = subprocess.run(
        [node, "-e", program, json.dumps(values)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def js_eval(expression: str, payload) -> object:
    """Evaluate a JavaScript expression over ``payload`` (bound to ``p``) in node."""
    node = require_node()
    program = (
        "const p = JSON.parse(process.argv[1]);"
        "console.log(JSON.stringify((function(){ return " + expression + "; })()));"
    )
    done = subprocess.run(
        [node, "-e", program, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


_HARNESS_JS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const errors = [];
process.on('unhandledRejection', (e) => { errors.push('unhandled: ' + String(e)); });

class TextNode {
  constructor(d) { this.nodeType = 3; this.data = d; this.parentNode = null; }
  get textContent() { return this.data; }
}
const kebab = (s) => s.replace(/[A-Z]/g, (c) => '-' + c.toLowerCase());
function parseStyle(str) {
  const m = new Map();
  String(str || '').split(';').forEach((part) => {
    const i = part.indexOf(':');
    if (i > 0) m.set(part.slice(0, i).trim(), part.slice(i + 1).trim());
  });
  return m;
}

class El {
  constructor(tag, attrs) {
    this.nodeType = 1;
    this.tagName = String(tag || 'div').toUpperCase();
    this._attrs = new Map(Object.entries(attrs || {}));
    this.childNodes = [];
    this.parentNode = null;
    this._listeners = {};
    this._innerHTML = '';
    this._styleMap = parseStyle(this._attrs.get('style'));
    const self = this;
    this.style = new Proxy({}, {
      get(t, k) {
        if (typeof k === 'symbol') return undefined;
        if (k === 'setProperty') return (n, v) => { self._styleMap.set(n, String(v)); };
        if (k === 'getPropertyValue') return (n) => self._styleMap.get(n) || '';
        if (k === 'removeProperty') return (n) => { self._styleMap.delete(n); };
        if (k === 'cssText') return Array.from(self._styleMap).map(([a, b]) => a + ':' + b).join(';');
        const v = self._styleMap.get(kebab(k));
        return v === undefined ? '' : v;
      },
      set(t, k, v) {
        if (k === 'cssText') { self._styleMap = parseStyle(v); return true; }
        if (v === '' || v == null) self._styleMap.delete(kebab(k));
        else self._styleMap.set(kebab(k), String(v));
        return true;
      },
    });
    this.dataset = new Proxy({}, {
      get(t, k) {
        if (typeof k === 'symbol') return undefined;
        const v = self._attrs.get('data-' + kebab(k));
        return v === undefined ? undefined : v;
      },
      set(t, k, v) { self._attrs.set('data-' + kebab(k), String(v)); return true; },
    });
  }
  get children() { return this.childNodes.filter((c) => c.nodeType === 1); }
  get firstChild() { return this.childNodes[0] || null; }
  get parentElement() { return this.parentNode; }
  get id() { return this._attrs.get('id') || ''; }
  set id(v) { this._attrs.set('id', String(v)); }
  get className() { return this._attrs.get('class') || ''; }
  set className(v) { this._attrs.set('class', String(v)); }
  _cls() { return (this._attrs.get('class') || '').split(/\s+/).filter(Boolean); }
  get classList() {
    const self = this;
    const write = (arr) => self._attrs.set('class', arr.join(' '));
    return {
      add(...c) { const s = self._cls(); c.forEach((x) => { if (!s.includes(x)) s.push(x); }); write(s); },
      remove(...c) { write(self._cls().filter((x) => !c.includes(x))); },
      contains(c) { return self._cls().includes(c); },
      toggle(c, force) {
        const has = self._cls().includes(c);
        const want = force === undefined ? !has : !!force;
        if (want && !has) this.add(c);
        if (!want && has) this.remove(c);
        return want;
      },
    };
  }
  get hidden() { return this._attrs.has('hidden'); }
  set hidden(v) { if (v) this._attrs.set('hidden', ''); else this._attrs.delete('hidden'); }
  get disabled() { return this._attrs.has('disabled'); }
  set disabled(v) { if (v) this._attrs.set('disabled', ''); else this._attrs.delete('disabled'); }
  getAttribute(n) { return this._attrs.has(n) ? this._attrs.get(n) : null; }
  setAttribute(n, v) {
    this._attrs.set(n, String(v));
    if (n === 'style') this._styleMap = parseStyle(v);
  }
  removeAttribute(n) { this._attrs.delete(n); if (n === 'style') this._styleMap = new Map(); }
  hasAttribute(n) { return this._attrs.has(n); }
  get textContent() { return this.childNodes.map((c) => c.textContent).join(''); }
  set textContent(v) {
    this.childNodes.forEach((c) => { c.parentNode = null; });
    this.childNodes = [];
    const s = v == null ? '' : String(v);
    if (s !== '') this.appendChild(new TextNode(s));
  }
  get innerHTML() { return this._innerHTML; }
  set innerHTML(v) {
    this.childNodes.forEach((c) => { c.parentNode = null; });
    this.childNodes = [];
    this._innerHTML = String(v);
  }
  get outerHTML() { return ''; }
  insertAdjacentHTML() {}
  appendChild(c) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    this.childNodes.push(c);
    return c;
  }
  removeChild(c) {
    const i = this.childNodes.indexOf(c);
    if (i >= 0) this.childNodes.splice(i, 1);
    c.parentNode = null;
    return c;
  }
  insertBefore(c, ref) {
    if (c.parentNode) c.parentNode.removeChild(c);
    c.parentNode = this;
    const i = ref ? this.childNodes.indexOf(ref) : -1;
    if (i < 0) this.childNodes.push(c); else this.childNodes.splice(i, 0, c);
    return c;
  }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  contains(o) { for (let n = o; n; n = n.parentNode) { if (n === this) return true; } return false; }
  addEventListener(t, f) { (this._listeners[t] = this._listeners[t] || []).push(f); }
  removeEventListener(t, f) { this._listeners[t] = (this._listeners[t] || []).filter((x) => x !== f); }
  dispatchEvent(ev) { (this._listeners[ev.type] || []).forEach((f) => f.call(this, ev)); return true; }
  focus() {}
  blur() {}
  click() { this.dispatchEvent({ type: 'click', target: this, currentTarget: this, stopPropagation() {} }); }
  scrollIntoView() {}
  getContext() { return null; }
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0 }; }
  closest(sel) { for (let n = this; n && n.nodeType === 1; n = n.parentNode) { if (matches(n, sel)) return n; } return null; }
  matches(sel) { return matches(this, sel); }
  querySelector(sel) { return queryAll(this, sel, true)[0] || null; }
  querySelectorAll(sel) { return queryAll(this, sel, false); }
}

function splitTop(str, isSep) {
  const out = []; let cur = ''; let depth = 0; let quote = null;
  for (const ch of str) {
    if (quote) { cur += ch; if (ch === quote) quote = null; continue; }
    if (ch === '"' || ch === "'") { quote = ch; cur += ch; continue; }
    if (ch === '[') depth++;
    if (ch === ']') depth--;
    if (depth === 0 && isSep(ch)) { if (cur) out.push(cur); cur = ''; continue; }
    cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}
function parseCompound(s) {
  const p = { tag: null, id: null, classes: [], attrs: [] };
  const re = /#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]|([a-zA-Z][\w-]*)/g;
  let m;
  while ((m = re.exec(s))) {
    if (m[1]) p.id = m[1];
    else if (m[2]) p.classes.push(m[2]);
    else if (m[3]) p.attrs.push([m[3], m[4] !== undefined ? m[4] : (m[5] !== undefined ? m[5] : m[6])]);
    else if (m[7]) p.tag = m[7].toUpperCase();
  }
  return p;
}
function matchCompound(el, p) {
  if (!el || el.nodeType !== 1) return false;
  if (p.tag && el.tagName !== p.tag) return false;
  if (p.id && el.getAttribute('id') !== p.id) return false;
  for (const c of p.classes) { if (!el._cls().includes(c)) return false; }
  for (const [n, v] of p.attrs) {
    const a = el.getAttribute(n);
    if (a === null) return false;
    if (v !== undefined && a !== v) return false;
  }
  return true;
}
function parseSelector(sel) {
  return splitTop(sel, (c) => c === ',').map((one) => {
    const toks = splitTop(one.trim(), (c) => /\s/.test(c));
    const parts = []; let child = false;
    toks.forEach((t) => {
      if (t === '>') { child = true; return; }
      parts.push({ c: parseCompound(t), child });
      child = false;
    });
    return parts;
  });
}
function matchComplex(el, parts, i) {
  if (!matchCompound(el, parts[i].c)) return false;
  if (i === 0) return true;
  if (parts[i].child) return el.parentNode ? matchComplex(el.parentNode, parts, i - 1) : false;
  for (let n = el.parentNode; n && n.nodeType === 1; n = n.parentNode) {
    if (matchComplex(n, parts, i - 1)) return true;
  }
  return false;
}
function matches(el, sel) {
  return parseSelector(sel).some((parts) => parts.length && matchComplex(el, parts, parts.length - 1));
}
function queryAll(root, sel, first) {
  const parsed = parseSelector(sel);
  const out = [];
  (function walk(n) {
    for (const c of n.childNodes) {
      if (c.nodeType !== 1) continue;
      if (parsed.some((parts) => parts.length && matchComplex(c, parts, parts.length - 1))) {
        out.push(c);
        if (first) return true;
      }
      if (walk(c) && first) return true;
    }
    return false;
  })(root);
  return out;
}

function build(n) {
  if (n.text !== undefined) return new TextNode(n.text);
  const el = new El(n.tag, n.attrs);
  (n.children || []).forEach((c) => el.appendChild(build(c)));
  return el;
}
const rootEl = build(cfg.tree);
const htmlEl = rootEl.childNodes.find((c) => c.nodeType === 1 && c.tagName === 'HTML') || rootEl;
const bodyEl = queryAll(htmlEl, 'body', true)[0] || htmlEl;

const docListeners = {};
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

const requests = [];
const routes = Object.assign({}, cfg.routes || {});
const deferreds = [];
function mkResp(spec) {
  const status = spec.status === undefined ? 200 : spec.status;
  const body = spec.body === undefined ? {} : spec.body;
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(JSON.parse(JSON.stringify(body))),
    text: () => Promise.resolve(JSON.stringify(body)),
  };
}
function fetchStub(url, opts) {
  opts = opts || {};
  requests.push({
    url: String(url),
    method: String(opts.method || 'GET').toUpperCase(),
    headers: opts.headers || {},
    body: opts.body === undefined ? null : opts.body,
  });
  const key = String(url);
  const spec = routes[key] !== undefined ? routes[key] : routes[key.split('?')[0]];
  if (!spec || spec.reject) return Promise.reject(new TypeError('stub fetch has no answer for ' + key));
  if (spec.deferred) {
    return new Promise((res) => { deferreds.push({ url: key, res: () => res(mkResp(spec)) }); });
  }
  return Promise.resolve(mkResp(spec));
}

const intervals = [];
const winListeners = {};
const sandbox = {
  document,
  console: {
    log() {}, info() {}, warn() {}, debug() {},
    error(...a) { errors.push(a.map(String).join(' ')); },
  },
  fetch: fetchStub,
  setInterval(f, ms) { intervals.push({ f, ms }); return intervals.length; },
  clearInterval() {},
  setTimeout() { return 0; },
  clearTimeout() {},
  getComputedStyle() { return { getPropertyValue() { return ''; } }; },
  location: { reload() {}, href: 'http://localhost/', pathname: '/', search: '' },
  localStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
  navigator: { userAgent: 'node' },
  confirm() { return true; },
  alert() {},
  encodeURIComponent,
  decodeURIComponent,
  URLSearchParams,
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.addEventListener = (t, f) => { (winListeners[t] = winListeners[t] || []).push(f); };
sandbox.removeEventListener = () => {};
vm.createContext(sandbox);

function run(code, filename) {
  try { vm.runInContext(code, sandbox, { filename }); } catch (e) { errors.push(filename + ': ' + String(e && e.stack || e)); }
}

const norm = (s) => String(s).replace(/\s+/g, ' ').trim();
const isHidden = (el) => el.hasAttribute('hidden') || el.style.display === 'none';
function visibleText(el) {
  if (el.nodeType === 3) return el.data;
  if (isHidden(el)) return '';
  return el.childNodes.map(visibleText).join('');
}

function observe() {
  const cards = {};
  document.querySelectorAll('[data-testid="sym-card"]').forEach((card) => {
    const id = card.getAttribute('data-sym-id');
    const v = card.querySelector('[data-testid="triggered-verdict"]');
    const ae = card.querySelector('[data-testid="guard-alpha-at-exit"]');
    const dial = card.querySelector('[data-testid="mc-dial"]');
    const arc = dial && dial.querySelector('.mc-arc');
    const txt = dial && dial.querySelector('.mc-text');
    cards[id] = {
      verdict: v && {
        hidden: isHidden(v),
        text: norm(visibleText(v)),
        hasVerdictTextEl: !!v.querySelector('[data-field="verdict-text"]'),
      },
      atExit: ae && { hidden: isHidden(ae), text: norm(ae.textContent) },
      dial: dial && {
        offset: arc ? arc.getAttribute('stroke-dashoffset') : null,
        stroke: arc ? arc.style.stroke.replace(/\s+/g, '') : null,
        text: txt ? norm(txt.textContent) : null,
      },
    };
  });
  const b = document.getElementById('fleet-correlation-banner');
  const bt = document.getElementById('fleet-correlation-banner-text');
  const n = document.getElementById('fleet-dismiss-notice');
  const btn = document.getElementById('fleet-dismiss-btn');
  const m = document.getElementById('market-state-banner');
  const sp = document.getElementById('fleet-info-specifics');
  const modal = document.getElementById('fleet-info-modal');
  return {
    cards,
    fleet: {
      present: !!b,
      hidden: b ? isHidden(b) : null,
      text: bt ? norm(visibleText(bt)) : null,
      alertAttr: b ? b.getAttribute('data-fleet-alert') : null,
      noticePresent: !!n,
      noticeHidden: n ? isHidden(n) : null,
      noticeText: n ? norm(n.textContent) : null,
      btnPresent: !!btn,
      btnDisabled: btn ? !!btn.disabled : null,
      btnOnclick: btn ? btn.getAttribute('onclick') : null,
    },
    market: m ? { hidden: isHidden(m), text: norm(m.textContent), state: m.getAttribute('data-market-state') } : null,
    modal: { present: !!modal, hidden: modal ? isHidden(modal) : null, specifics: sp ? sp.innerHTML : null },
  };
}

async function flush() { for (let i = 0; i < 40; i++) await new Promise((r) => setImmediate(r)); }

(async () => {
  const out = { initial: null, steps: [], requests, errors };
  if (cfg.inline) {
    queryAll(htmlEl, 'script', false).forEach((s) => {
      if (s.hasAttribute('src')) return;
      const code = s.textContent;
      if (code.indexOf(cfg.inlineMarker || 'function updateBanners') < 0) return;
      run(code, 'inline.js');
    });
  }
  run(cfg.indexJs, 'index.js');
  out.initial = observe();

  (docListeners['DOMContentLoaded'] || []).forEach((f) => {
    try { f.call(document, { type: 'DOMContentLoaded' }); } catch (e) { errors.push('DOMContentLoaded: ' + String(e && e.stack || e)); }
  });
  await flush();
  out.loaded = observe();

  for (const step of cfg.steps || []) {
    const startReq = requests.length;
    if (step.route) Object.assign(routes, step.route);
    try {
      if (step.op === 'poll') {
        const iv = intervals.find((i) => i.ms === 30000) || intervals[0];
        iv.f();
      } else if (step.op === 'dismiss') {
        sandbox.dismissFleetAlert(document.getElementById('fleet-dismiss-btn'));
      } else if (step.op === 'resolve') {
        deferreds.splice(0).forEach((d) => d.res());
      } else if (step.op === 'open_modal') {
        sandbox.openFleetInfoModal();
      } else if (step.op === 'update_banners') {
        sandbox.updateBanners(step.data);
      }
    } catch (e) { errors.push('step ' + step.op + ': ' + String(e && e.stack || e)); }
    await flush();
    out.steps.push({ label: step.label || step.op, obs: observe(), requests: requests.slice(startReq) });
  }
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
})();
"""


def run_page(
    html: str,
    tmp_path: pathlib.Path,
    *,
    steps: list[dict] | None = None,
    routes: dict | None = None,
    inline: bool = True,
) -> dict:
    """Run the page's client code over the markup ``html``.

    ``routes`` maps a URL to ``{"body": ..., "status": 200, "reject": False,
    "deferred": False}``; a URL with no entry is refused.  Each step may carry
    ``route`` (entries merged before the step) and ``op`` (``poll``, ``dismiss``,
    ``resolve``, ``open_modal`` or ``update_banners`` with ``data``).  Returns
    ``{"initial", "loaded", "steps", "requests", "errors"}``; ``initial`` is the
    server markup as first painted, before any client request.
    """
    node = require_node()
    cfg = {
        "tree": parse_markup(html),
        "indexJs": INDEX_JS.read_text(encoding="utf-8"),
        "routes": routes or {},
        "steps": steps or [],
        "inline": inline,
    }
    cfg_path = tmp_path / "page_cfg.json"
    harness_path = tmp_path / "page_harness.js"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    harness_path.write_text(_HARNESS_JS, encoding="utf-8")
    done = subprocess.run(
        [node, str(harness_path), str(cfg_path)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert done.returncode == 0, f"page harness failed:\n{done.stderr[-3000:]}"
    return json.loads(done.stdout)
