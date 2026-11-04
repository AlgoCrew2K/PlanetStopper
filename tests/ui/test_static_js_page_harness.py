"""Node page harness for the History and Performance pages, plus its own checks.

Why this exists: the History and Performance scripts (static/history.js,
static/performance.js) are tested elsewhere only by reading their source text, which
cannot tell a sign written twice or a wrongly ordered table from a right one. This
harness parses the HTML the Flask test client actually returned for the page into a
small DOM, runs the real script files over it under node, answers `fetch` from a route
table whose bodies are the real routes' output, and lets a scenario click, wait and read
the DOM back. No client function is re-implemented here: every text a test compares was
written by the code under test.

Test-double limits (said once): the DOM supports the selector subset these two scripts
use (tag, #id, .class, [attr], [attr=value], descendant and child combinators),
`textContent`/`innerHTML` as a browser does, `hidden` and `style.display`. Charts are a
stub that records its config. A visible render is judged on a live page, never here.

The harness itself is checked at the bottom of this file so a harness fault cannot pass
for a page fault.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "static"
FIXTURES = REPO / "tests" / "fixtures" / "prod_droplet"

_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const input = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));

const VOID = new Set(['area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr']);
const RAW = new Set(['script','style']);
const ENT = {amp:'&', lt:'<', gt:'>', quot:'"', apos:"'", nbsp:' '};
function decode(s) {
  return s.replace(/&(#x[0-9a-fA-F]+|#\d+|[a-zA-Z]+);/g, function (m, e) {
    if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
    return Object.prototype.hasOwnProperty.call(ENT, e) ? ENT[e] : m;
  });
}
function escText(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
function escAttr(s) { return escText(s).replace(/"/g, '&quot;'); }

class TextNode {
  constructor(data) { this.nodeType = 3; this.data = data; this.parentNode = null; }
  get textContent() { return this.data; }
}

function parseStyle(el, text) {
  el.style = {};
  String(text).split(';').forEach(function (decl) {
    const i = decl.indexOf(':');
    if (i < 0) return;
    const k = decl.slice(0, i).trim().replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
    if (k) el.style[k] = decl.slice(i + 1).trim();
  });
}

class El {
  constructor(tag) {
    this.nodeType = 1;
    this.tagName = tag.toUpperCase();
    this.childNodes = [];
    this.parentNode = null;
    this._attrs = new Map();
    this._listeners = {};
    this.style = {};
  }
  get nodeName() { return this.tagName; }
  get children() { return this.childNodes.filter(function (n) { return n.nodeType === 1; }); }
  get firstChild() { return this.childNodes[0] || null; }
  get lastChild() { return this.childNodes[this.childNodes.length - 1] || null; }
  get parentElement() { return this.parentNode && this.parentNode.nodeType === 1 ? this.parentNode : null; }
  get id() { return this.getAttribute('id') || ''; }
  set id(v) { this.setAttribute('id', v); }
  get className() { return this.getAttribute('class') || ''; }
  set className(v) { this.setAttribute('class', v); }
  get hidden() { return this._attrs.has('hidden'); }
  set hidden(v) { if (v) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }
  get title() { return this.getAttribute('title') || ''; }
  set title(v) { this.setAttribute('title', v); }
  get classList() {
    const self = this;
    const list = function () { return self.className.split(/\s+/).filter(Boolean); };
    return {
      add: function () { const l = list(); Array.from(arguments).forEach(function (c) { if (l.indexOf(c) < 0) l.push(c); }); self.className = l.join(' '); },
      remove: function () { const rm = Array.from(arguments); self.className = list().filter(function (c) { return rm.indexOf(c) < 0; }).join(' '); },
      contains: function (c) { return list().indexOf(c) >= 0; },
      toggle: function (c, force) {
        const has = list().indexOf(c) >= 0;
        const want = force === undefined ? !has : !!force;
        if (want && !has) this.add(c); else if (!want && has) this.remove(c);
        return want;
      },
    };
  }
  get dataset() {
    const self = this;
    return new Proxy({}, {
      get: function (_, k) { const v = self.getAttribute('data-' + String(k).replace(/[A-Z]/g, function (c) { return '-' + c.toLowerCase(); })); return v === null ? undefined : v; },
      set: function (_, k, v) { self.setAttribute('data-' + String(k).replace(/[A-Z]/g, function (c) { return '-' + c.toLowerCase(); }), v); return true; },
    });
  }
  setAttribute(k, v) { v = String(v); this._attrs.set(k, v); if (k === 'style') parseStyle(this, v); }
  getAttribute(k) { return this._attrs.has(k) ? this._attrs.get(k) : null; }
  hasAttribute(k) { return this._attrs.has(k); }
  removeAttribute(k) { this._attrs.delete(k); }
  get textContent() { return this.childNodes.map(function (n) { return n.textContent; }).join(''); }
  set textContent(v) {
    this.childNodes.forEach(function (n) { n.parentNode = null; });
    this.childNodes = [];
    if (v !== '' && v !== null && v !== undefined) this.appendChild(new TextNode(String(v)));
  }
  get innerHTML() { return this.childNodes.map(serialize).join(''); }
  set innerHTML(v) {
    this.childNodes.forEach(function (n) { n.parentNode = null; });
    this.childNodes = [];
    parseHTML(String(v), this);
  }
  appendChild(n) { if (n.parentNode) n.parentNode.removeChild(n); n.parentNode = this; this.childNodes.push(n); return n; }
  insertBefore(n, ref) {
    if (!ref) return this.appendChild(n);
    if (n.parentNode) n.parentNode.removeChild(n);
    const i = this.childNodes.indexOf(ref);
    n.parentNode = this;
    this.childNodes.splice(i < 0 ? this.childNodes.length : i, 0, n);
    return n;
  }
  removeChild(n) { const i = this.childNodes.indexOf(n); if (i >= 0) { this.childNodes.splice(i, 1); n.parentNode = null; } return n; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  get value() {
    if (this.tagName === 'SELECT') {
      const opts = this.querySelectorAll('option');
      const sel = opts.find(function (o) { return o.selected === true || (o.selected === undefined && o.hasAttribute('selected')); });
      const o = sel || opts[0];
      return o ? o.value : '';
    }
    if (this.tagName === 'OPTION') return this._attrs.has('value') ? this._attrs.get('value') : this.textContent;
    return this._value !== undefined ? this._value : (this.getAttribute('value') || '');
  }
  set value(v) { if (this.tagName === 'OPTION') this.setAttribute('value', v); else this._value = String(v); }
  addEventListener(t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); }
  removeEventListener(t, fn) { this._listeners[t] = (this._listeners[t] || []).filter(function (f) { return f !== fn; }); }
  dispatchEvent(ev) {
    ev.target = ev.target || this;
    let node = this;
    while (node) {
      ev.currentTarget = node;
      const inline = node.getAttribute && node.getAttribute('on' + ev.type);
      if (inline) new Function('event', inline).call(node, ev);
      (node._listeners && node._listeners[ev.type] || []).slice().forEach(function (fn) { fn.call(node, ev); });
      if (ev._stop || ev.bubbles === false) break;
      node = node.parentNode;
    }
    return !ev.defaultPrevented;
  }
  click() { return this.dispatchEvent(mkEvent('click', this)); }
  focus() {}
  getContext() { return {}; }
  matches(sel) { return parseSelector(sel).some(function (c) { return matchComplex(this, c); }, this); }
  closest(sel) { let n = this; while (n && n.nodeType === 1) { if (n.matches(sel)) return n; n = n.parentNode; } return null; }
  querySelectorAll(sel) {
    const out = [];
    const chains = parseSelector(sel);
    (function walk(n) {
      n.childNodes.forEach(function (c) {
        if (c.nodeType !== 1) return;
        if (chains.some(function (ch) { return matchComplex(c, ch); })) out.push(c);
        walk(c);
      });
    })(this);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function mkEvent(type, target) {
  return { type: type, target: target, bubbles: true, defaultPrevented: false, _stop: false,
           stopPropagation: function () { this._stop = true; }, preventDefault: function () { this.defaultPrevented = true; } };
}

function serialize(n) {
  if (n.nodeType === 3) return escText(n.data);
  const tag = n.tagName.toLowerCase();
  let attrs = '';
  n._attrs.forEach(function (v, k) { attrs += ' ' + k + '="' + escAttr(v) + '"'; });
  if (VOID.has(tag)) return '<' + tag + attrs + '>';
  return '<' + tag + attrs + '>' + n.childNodes.map(serialize).join('') + '</' + tag + '>';
}

function parseHTML(html, parent) {
  const tokenRe = /<!--[\s\S]*?-->|<!\w[^>]*>|<\/([a-zA-Z][\w:-]*)\s*>|<([a-zA-Z][\w:-]*)((?:\s+[^\s=\/>]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*(\/?)>/g;
  const attrRe = /([^\s=\/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  const stack = [parent];
  let last = 0, m;
  function text(s) { if (s) stack[stack.length - 1].appendChild(new TextNode(decode(s))); }
  while ((m = tokenRe.exec(html))) {
    text(html.slice(last, m.index));
    last = tokenRe.lastIndex;
    if (m[1]) {
      const tag = m[1].toLowerCase();
      for (let i = stack.length - 1; i > 0; i--) { if (stack[i].tagName.toLowerCase() === tag) { stack.length = i; break; } }
    } else if (m[2]) {
      const tag = m[2].toLowerCase();
      const el = new El(tag);
      let a;
      attrRe.lastIndex = 0;
      while ((a = attrRe.exec(m[3] || ''))) {
        el.setAttribute(a[1], decode(a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : ''));
      }
      stack[stack.length - 1].appendChild(el);
      if (RAW.has(tag) && !m[4]) {
        const close = html.toLowerCase().indexOf('</' + tag, last);
        const end = close < 0 ? html.length : close;
        el.appendChild(new TextNode(html.slice(last, end)));
        const gt = html.indexOf('>', end);
        last = tokenRe.lastIndex = gt < 0 ? html.length : gt + 1;
      } else if (!VOID.has(tag) && !m[4]) {
        stack.push(el);
      }
    }
  }
  text(html.slice(last));
}

function parseCompound(s) {
  const c = { tag: null, id: null, classes: [], attrs: [] };
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === '#') { const m = /^#([\w-]+)/.exec(s.slice(i)); c.id = m[1]; i += m[0].length; }
    else if (ch === '.') { const m = /^\.([\w-]+)/.exec(s.slice(i)); c.classes.push(m[1]); i += m[0].length; }
    else if (ch === '[') {
      const m = /^\[\s*([\w:-]+)\s*(?:=\s*(?:"([^"]*)"|'([^']*)'|([^\]\s]*)))?\s*\]/.exec(s.slice(i));
      if (!m) throw new Error('bad selector: ' + s);
      c.attrs.push({ name: m[1], value: m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[4] });
      i += m[0].length;
    }
    else if (ch === '*') { i += 1; }
    else { const m = /^[\w-]+/.exec(s.slice(i)); if (!m) throw new Error('bad selector: ' + s); c.tag = m[0].toUpperCase(); i += m[0].length; }
  }
  return c;
}
function splitComplex(s) {
  const parts = []; let cur = '', depth = 0, q = null;
  for (let i = 0; i < s.length; i++) {
    const ch = s[i];
    if (q) { cur += ch; if (ch === q) q = null; continue; }
    if (ch === '"' || ch === "'") { q = ch; cur += ch; continue; }
    if (ch === '[') depth++; if (ch === ']') depth--;
    if (depth === 0 && (ch === ' ' || ch === '>')) {
      if (cur) { parts.push({ c: parseCompound(cur) }); cur = ''; }
      if (ch === '>') parts.push({ comb: '>' });
      continue;
    }
    cur += ch;
  }
  if (cur) parts.push({ c: parseCompound(cur) });
  return parts;
}
function parseSelector(sel) {
  const list = []; let cur = '', depth = 0, q = null;
  for (let i = 0; i < sel.length; i++) {
    const ch = sel[i];
    if (q) { cur += ch; if (ch === q) q = null; continue; }
    if (ch === '"' || ch === "'") { q = ch; cur += ch; continue; }
    if (ch === '[') depth++; if (ch === ']') depth--;
    if (ch === ',' && depth === 0) { list.push(cur.trim()); cur = ''; continue; }
    cur += ch;
  }
  list.push(cur.trim());
  return list.filter(Boolean).map(splitComplex);
}
function matchCompound(el, c) {
  if (c.tag && el.tagName !== c.tag) return false;
  if (c.id && el.id !== c.id) return false;
  for (const k of c.classes) if (el.className.split(/\s+/).indexOf(k) < 0) return false;
  for (const a of c.attrs) {
    if (!el.hasAttribute(a.name)) return false;
    if (a.value !== undefined && el.getAttribute(a.name) !== a.value) return false;
  }
  return true;
}
function matchComplex(el, parts) {
  let i = parts.length - 1;
  while (i >= 0 && !parts[i].c) i--;
  if (i < 0 || !matchCompound(el, parts[i].c)) return false;
  let cur = el;
  i--;
  while (i >= 0) {
    let child = false;
    if (parts[i].comb === '>') { child = true; i--; }
    const need = parts[i].c;
    if (child) {
      cur = cur.parentNode;
      if (!cur || cur.nodeType !== 1 || !matchCompound(cur, need)) return false;
    } else {
      cur = cur.parentNode;
      while (cur && cur.nodeType === 1 && !matchCompound(cur, need)) cur = cur.parentNode;
      if (!cur || cur.nodeType !== 1) return false;
    }
    i--;
  }
  return true;
}

// ---- document ----
const root = new El('#root');
parseHTML(input.html, root);
const htmlEl = root.querySelector('html') || root;
const doc = {
  nodeType: 9,
  readyState: 'loading',
  _listeners: {},
  documentElement: htmlEl,
  body: root.querySelector('body') || htmlEl,
  head: root.querySelector('head') || htmlEl,
  getElementById: function (id) { return root.querySelector('#' + id); },
  querySelector: function (s) { return root.querySelector(s); },
  querySelectorAll: function (s) { return root.querySelectorAll(s); },
  createElement: function (t) { return new El(t); },
  createTextNode: function (t) { return new TextNode(String(t)); },
  addEventListener: function (t, fn) { (this._listeners[t] = this._listeners[t] || []).push(fn); },
  removeEventListener: function () {},
};
globalThis.document = doc;
globalThis.window = globalThis;
globalThis.getComputedStyle = function () { return { getPropertyValue: function () { return ''; } }; };
globalThis.location = { href: 'http://127.0.0.1/', pathname: '/', search: '', hash: '' };

const errors = [];
const logs = [];
globalThis.console = {
  log: function () { logs.push(Array.from(arguments).map(String).join(' ')); },
  info: function () {}, debug: function () {}, warn: function () {},
  error: function () { errors.push(Array.from(arguments).map(String).join(' ')); },
};

const intervals = [];
globalThis.setInterval = function (fn, ms) { intervals.push({ fn: fn, ms: ms }); return intervals.length; };
globalThis.clearInterval = function () {};

const charts = [];
globalThis.Chart = class {
  constructor(ctx, cfg) { this.config = cfg; this.data = cfg.data; this.options = cfg.options; charts.push(this); }
  update() {}
  static register() {}
};

// ---- fetch ----
const fetchLog = [];
const unrouted = [];
const held = {};
const routeCalls = new Map();
function makeResponse(spec) {
  const status = spec.status === undefined ? 200 : spec.status;
  return {
    ok: status >= 200 && status < 300,
    status: status,
    json: function () { return Promise.resolve(JSON.parse(JSON.stringify(spec.json === undefined ? null : spec.json))); },
    text: function () { return Promise.resolve(JSON.stringify(spec.json)); },
  };
}
globalThis.fetch = function (url) {
  url = String(url);
  fetchLog.push(url);
  for (let i = 0; i < input.routes.length; i++) {
    const r = input.routes[i];
    if (!new RegExp(r.pattern).test(url)) continue;
    const n = routeCalls.get(i) || 0;
    routeCalls.set(i, n + 1);
    const spec = r.responses ? r.responses[Math.min(n, r.responses.length - 1)] : r;
    if (spec.reject) return Promise.reject(new TypeError('Failed to fetch'));
    if (spec.hold) {
      return new Promise(function (resolve) { held[spec.hold] = function () { resolve(makeResponse(spec)); }; });
    }
    return Promise.resolve(makeResponse(spec));
  }
  unrouted.push(url);
  return Promise.reject(new Error('no route for ' + url));
};

// ---- scripts ----
input.scripts.forEach(function (s) { vm.runInThisContext(s.src, { filename: s.name }); });

function visible(el) {
  for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
    if (n.hidden) return false;
    if (n.style && n.style.display === 'none') return false;
  }
  return true;
}
async function settle(n) { for (let i = 0; i < (n || 6); i++) await new Promise(function (r) { setImmediate(r); }); }

const H = {
  document: doc, fetchLog: fetchLog, unrouted: unrouted, errors: errors, logs: logs, charts: charts,
  settle: settle, visible: visible,
  boot: async function () {
    doc.readyState = 'complete';
    (doc._listeners.DOMContentLoaded || []).slice().forEach(function (fn) { fn.call(doc, mkEvent('DOMContentLoaded', doc)); });
    await settle();
  },
  $: function (s) { return doc.querySelector(s); },
  $$: function (s) { return Array.from(doc.querySelectorAll(s)); },
  text: function (s) { const e = doc.querySelector(s); return e ? e.textContent : null; },
  attr: function (s, a) { const e = doc.querySelector(s); return e ? e.getAttribute(a) : null; },
  isVisible: function (s) { const e = doc.querySelector(s); return !!e && visible(e); },
  rows: function (s) {
    return Array.from(doc.querySelectorAll(s + ' tr')).map(function (tr) {
      return tr.children.map(function (td) { return td.textContent; });
    });
  },
  click: async function (s) {
    const e = doc.querySelector(s);
    if (!e) throw new Error('click: no element for ' + s);
    e.dispatchEvent(mkEvent('click', e));
    await settle();
  },
  release: async function (name) { held[name](); await settle(); },
  tick: async function () { intervals.forEach(function (i) { i.fn(); }); await settle(); },
  intervalCount: function () { return intervals.length; },
};

(async function () {
  try {
    const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
    const result = await new AsyncFunction('H', input.scenario)(H);
    process.stdout.write('\n__RESULT__' + JSON.stringify({ ok: true, result: result === undefined ? null : result }) + '\n');
  } catch (e) {
    process.stdout.write('\n__RESULT__' + JSON.stringify({ ok: false, error: String(e && e.stack || e) }) + '\n');
  }
})();
"""


def read_script(name: str) -> dict:
    return {"name": name, "src": (STATIC / name).read_text(encoding="utf-8")}


def run_page(html: str, scripts: list[str], routes: list[dict], scenario: str):
    """Run `scripts` over `html` under node and return the scenario's JSON result.

    A missing node is a failure with a message, never a skip: every page test would
    otherwise pass by not running.
    """
    node = shutil.which("node")
    assert node, "node is not installed; the History and Performance page tests need it"
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "driver.js").write_text(_DRIVER, encoding="utf-8")
        (tmp_path / "input.json").write_text(
            json.dumps(
                {
                    "html": html,
                    "scripts": [read_script(s) for s in scripts],
                    "routes": routes,
                    "scenario": scenario,
                }
            ),
            encoding="utf-8",
        )
        proc = subprocess.run(
            [node, str(tmp_path / "driver.js"), str(tmp_path / "input.json")],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
        )
    marker = proc.stdout.rfind("__RESULT__")
    assert marker >= 0, f"node produced no result (exit {proc.returncode}):\n{proc.stderr[-2000:]}"
    outcome = json.loads(proc.stdout[marker + len("__RESULT__") :])
    assert outcome["ok"], f"scenario raised inside node:\n{outcome['error']}"
    return outcome["result"]


def route(url_or_pattern: str, body, status: int = 200, *, exact: bool = True) -> dict:
    """A route-table entry answering `url_or_pattern` with `body` (real route output)."""
    pattern = f"^{re.escape(url_or_pattern)}$" if exact else url_or_pattern
    return {"pattern": pattern, "status": status, "json": body}


def reject_route(url_or_pattern: str, *, exact: bool = True) -> dict:
    pattern = f"^{re.escape(url_or_pattern)}$" if exact else url_or_pattern
    return {"pattern": pattern, "reject": True}


def captured(client, url: str) -> dict:
    """Ask the real Flask route and package its answer as a route-table entry."""
    resp = client.get(url)
    return route(url, resp.get_json(), resp.status_code)


def page_html(client, path: str) -> str:
    resp = client.get(path)
    assert resp.status_code == 200, f"GET {path} answered {resp.status_code}"
    return resp.get_data(as_text=True)


# ---------------------------------------------------------------------------
# Database seeding from the committed production captures (tests/fixtures/prod_droplet)
# ---------------------------------------------------------------------------

_SHADOW_INSERT = (
    "INSERT INTO shadow_history "
    "(ts_utc, ts_et, trading_day, symphony_id, account_id, cycle_id, "
    " current_return, shadow_return, is_post_trigger, trigger_id, position_epoch) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def captured_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


IF_HELD_STAMP = "shadow_history"


def pm_capture(day: str) -> dict:
    """One captured production post-mortem day, verbatim (tests/fixtures/prod_droplet/post_mortems)."""
    return json.loads(
        (FIXTURES / "post_mortems" / f"post_mortem_{day}.json").read_text(encoding="utf-8")
    )


def stamped(trigger: dict) -> dict:
    """The captured trigger plus the one key the read-time validity guard requires.

    The committed droplet captures predate the `if_held_source` stamp, so the guard
    (analytics.is_valid_post_mortem_entry) excludes every verbatim record; adding
    exactly that key is the derived edit recorded in tests/fixtures/ux_data_truth/PROVENANCE.md.
    """
    return {**trigger, "if_held_source": IF_HELD_STAMP}


def seed_shadow_rows(db_path: str, rows: list[dict]) -> None:
    """Insert shadow_history rows with the exact statement database.record_shadow_observation
    runs, in one transaction (the per-row writer opens a connection per call, which
    takes about 50 ms a row on this host). The first test below proves both agree."""
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        conn.executemany(
            _SHADOW_INSERT,
            [
                (
                    r["ts_utc"],
                    r["ts_et"],
                    r["trading_day"],
                    r["symphony_id"],
                    r.get("account_id"),
                    r.get("cycle_id"),
                    r["current_return"],
                    r["shadow_return"],
                    r.get("is_post_trigger", 0),
                    r.get("trigger_id"),
                    r.get("position_epoch"),
                )
                for r in rows
            ],
        )
        conn.commit()
    finally:
        conn.close()


def shadow_row(symphony_id: str, day: str, current_return: float, shadow_return: float) -> dict:
    return {
        "symphony_id": symphony_id,
        "account_id": "ACCOUNT_ID_REDACTED",
        "cycle_id": f"{day.replace('-', '')}_1604",
        "ts_utc": f"{day}T20:04:03Z",
        "ts_et": f"{day}T16:04:01",
        "trading_day": day,
        "current_return": current_return,
        "shadow_return": shadow_return,
        "is_post_trigger": 0,
        "trigger_id": None,
        "position_epoch": None,
    }


# ---------------------------------------------------------------------------
# Harness self-checks: a fault here must not read as a page fault
# ---------------------------------------------------------------------------

_MINI_HTML = (
    "<html><body><div id='box' class='a b' data-x='1'><span class='k'>one &amp; two</span>"
    "<ul id='list'><li>1</li><li>2</li></ul></div>"
    "<button id='go' onclick=\"window.clicked = (window.clicked || 0) + 1\">go</button></body></html>"
)


def test_harness_dom_round_trips_markup_and_selectors():
    result = run_page(
        _MINI_HTML,
        [],
        [],
        """
        const box = H.$('#box');
        box.querySelector('.k').textContent = '<b>x</b> & y';
        return {
          text: H.text('.k'),
          html: box.querySelector('.k').innerHTML,
          child: H.$$('#box > ul > li').length,
          desc: H.$$('div li').length,
          attr: H.$$('[data-x="1"]').length,
        };
        """,
    )
    assert result["text"] == "<b>x</b> & y"
    assert result["html"] == "&lt;b&gt;x&lt;/b&gt; &amp; y", "markup in text is escaped, not parsed"
    assert result["child"] == 2 and result["desc"] == 2 and result["attr"] == 1


def test_harness_fetch_answers_sequences_and_records_unrouted_calls():
    routes = [
        {
            "pattern": "^/a$",
            "responses": [{"status": 200, "json": {"n": 1}}, {"status": 500, "json": None}],
        },
        {"pattern": "^/gone$", "reject": True},
    ]
    result = run_page(
        _MINI_HTML,
        [],
        routes,
        """
        const first = await (await fetch('/a')).json();
        const second = await fetch('/a');
        let rejected = false, missing = false;
        await fetch('/gone').catch(function () { rejected = true; });
        await fetch('/nowhere').catch(function () { missing = true; });
        return { first: first, secondOk: second.ok, secondStatus: second.status,
                 rejected: rejected, missing: missing, unrouted: H.unrouted };
        """,
    )
    assert result["first"] == {"n": 1}
    assert result["secondOk"] is False and result["secondStatus"] == 500
    assert result["rejected"] and result["missing"] and result["unrouted"] == ["/nowhere"]


def test_harness_click_runs_inline_handlers_and_listeners_in_bubbling_order():
    result = run_page(
        _MINI_HTML,
        [],
        [],
        """
        const order = [];
        H.$('#box').addEventListener('click', function () { order.push('box'); });
        H.$('#list').addEventListener('click', function (e) { order.push('list'); e.stopPropagation(); });
        await H.click('#go');
        await H.click('#list');
        return { clicked: window.clicked, order: order };
        """,
    )
    assert result == {"clicked": 1, "order": ["list"]}


def test_shadow_seeding_helper_writes_the_row_the_real_writer_writes(_isolate_db):
    import os

    import database

    db_file = os.environ["DB_PATH"]
    row = shadow_row("5XjzXjdGnjh99MIsdM97", "2026-06-22", 0.51, 1.85)
    database.record_shadow_observation(
        symphony_id=row["symphony_id"],
        account_id=row["account_id"],
        cycle_id=row["cycle_id"],
        ts_utc=row["ts_utc"],
        ts_et=row["ts_et"],
        trading_day=row["trading_day"],
        current_return=row["current_return"],
        shadow_return=row["shadow_return"],
        is_post_trigger=0,
        trigger_id=None,
    )
    seed_shadow_rows(db_file, [{**row, "cycle_id": "second"}])
    conn = sqlite3.connect(db_file)
    try:
        stored = conn.execute(
            "SELECT ts_utc, ts_et, trading_day, symphony_id, account_id, current_return, "
            "shadow_return, is_post_trigger, trigger_id, position_epoch "
            "FROM shadow_history ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
    assert len(stored) == 2 and stored[0] == stored[1]


@pytest.fixture
def pm_dir_factory(tmp_path, monkeypatch):
    """Not a test: see the History tests, which import this fixture."""
    import analytics

    def make(days_to_triggers: dict[str, list[dict]]) -> Path:
        pm = tmp_path / "post_mortems"
        pm.mkdir(exist_ok=True)
        for day, triggers in days_to_triggers.items():
            (pm / f"post_mortem_{day}.json").write_text(
                json.dumps({"date": day, "triggers": triggers}), encoding="utf-8"
            )
        monkeypatch.setattr(analytics, "_POST_MORTEMS_DIR", str(pm))
        return pm

    return make
