"""Run the shipped dashboard scripts under ``node`` without hand-copying their code.

Two runners, both of which execute the real file from ``static/``:

* ``run_settings_helpers`` evaluates ``static/settings.js`` and calls named functions
  from the script's own closure. The file is one IIFE, so the harness appends an
  export statement just before the closing ``})();``; nothing else in the source is
  touched. A helper the script does not define comes back as an assertion failure
  naming it, never as a silent pass.
* ``run_modal_scenario`` evaluates ``static/settings-modal.js`` against a small
  document sink. The sink only records what the script writes (``innerHTML`` of the
  modal, appended fragments, ``fetch`` calls) and answers ``getElementById`` from
  the ids that appear in the HTML the script itself produced, so the markup the real
  code builds is what decides which controls exist.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
from html.parser import HTMLParser

_STATIC = pathlib.Path(__file__).resolve().parents[2] / "static"
_NODE_TIMEOUT_SECONDS = 60

_IIFE_CLOSE = re.compile(r"\}\)\(\);?\s*$|\}\(\)\);?\s*$")

_SETTINGS_RUNNER = r"""
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const noop = () => {};
const ctx = {
  document: {
    addEventListener: noop, removeEventListener: noop,
    getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
  },
  fetch: () => Promise.reject(new Error('no network in the helper harness')),
  alert: noop, console, setTimeout, clearTimeout,
};
ctx.window = ctx;
vm.createContext(ctx);
vm.runInContext(input.source, ctx, { filename: 'settings.js' });
const t = ctx.__t || {};
const deepFreeze = (v) => { if (v && typeof v === 'object') { Object.values(v).forEach(deepFreeze); Object.freeze(v); } return v; };
const out = input.calls.map((c) => {
  if (typeof t[c.fn] !== 'function') return { missing: c.fn };
  c.args.forEach(deepFreeze);
  try { return { value: t[c.fn](...c.args) }; }
  catch (e) { return { error: String(e && e.message || e) }; }
});
process.stdout.write(JSON.stringify(out));
"""

_MODAL_RUNNER = r"""
const vm = require('vm');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));

const fetches = [];
const docListeners = {};
let modalEl = null;
let cache = {};
const appended = [];

function listenersFor(id) { return (cache[id] && cache[id].listeners) || {}; }
function mkFake(id, extra) {
  const listeners = {};
  return Object.assign({
    id, listeners, style: {}, parentNode: { removeChild: () => {} }, textContent: '',
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {}, setAttribute() {}, focus() {}, appendChild() {},
    getAttribute(name) { return (this.attrs || {})[name]; },
    querySelector() { return null; },
    // The real DOM answers whether a node is inside another: nothing here holds the focus.
    contains() { return false; },
  }, extra || {});
}
function allHtml() { return (modalEl ? modalEl.innerHTML : '') + appended.join(''); }
function byId(id) {
  if (id === 'sym-settings-modal') return modalEl;
  if (!modalEl) return null;
  if (!allHtml().includes('id="' + id + '"')) return null;
  if (!cache[id]) cache[id] = mkFake(id);
  return cache[id];
}
function lockButtons() {
  if (!modalEl) return [];
  if (!cache.__locks) {
    cache.__locks = [];
    const re = /class="sym-settings-locked-var-btn" data-key="([^"]*)"/g;
    let m;
    while ((m = re.exec(modalEl.innerHTML)) !== null) {
      const key = m[1];
      cache.__locks.push(mkFake(null, { attrs: { 'data-key': key } }));
    }
  }
  return cache.__locks;
}

function makeModal() {
  let html = '';
  const el = mkFake('sym-settings-modal');
  Object.defineProperty(el, 'innerHTML', {
    get() { return html; },
    set(v) { html = v; cache = {}; },
  });
  el.__isModal = true;
  el.querySelector = (sel) => {
    if (sel === '[data-modal-surface]' && html.includes('data-modal-surface')) {
      return mkFake(null, { appendChild(child) { appended.push(child.__html || ''); } });
    }
    return null;
  };
  return el;
}

const document = {
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
  removeEventListener() {},
  createElement(tag) {
    if (tag === 'div' && !modalEl) { return makeModal(); }
    const frag = mkFake(null);
    Object.defineProperty(frag, 'firstElementChild', { get() { return { __html: frag.innerHTML }; } });
    frag.innerHTML = '';
    return frag;
  },
  body: { appendChild(el) { if (el.__isModal) { modalEl = el; } } },
  activeElement: null,
  getElementById: byId,
  querySelectorAll(sel) { return sel === '.sym-settings-locked-var-btn' ? lockButtons() : []; },
  querySelector() { return null; },
};

const fakeFetch = (url, opts) => {
  const method = (opts && opts.method) || 'GET';
  fetches.push({ url, method, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const route = input.routes[method + ' ' + url];
  if (url === '/api/csrf-token') return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ csrf_token: 'tok' }) });
  if (!route) return Promise.reject(new Error('unrouted ' + method + ' ' + url));
  if (route.reject) return Promise.reject(new Error('network down'));
  const status = route.status || 200;
  return Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(route.json) });
};

const ctx = { document, fetch: fakeFetch, console, setTimeout, clearTimeout, encodeURIComponent };
ctx.window = ctx;
vm.createContext(ctx);

// The page loads the token holder before any page script (templates/_chrome.html).
vm.runInContext(input.guardSource, ctx, { filename: 'request_guard.js' });
vm.runInContext(input.source, ctx, { filename: 'settings-modal.js' });
(docListeners['DOMContentLoaded'] || []).forEach((fn) => fn());

const settle = async () => { for (let i = 0; i < 8; i += 1) await new Promise((r) => setImmediate(r)); };
const snapshots = {};
const errors = [];

(async () => {
  for (const step of input.steps) {
    if (step.op === 'open') ctx.window.openSymphonySettings(null, step.id);
    else if (step.op === 'settle') { await settle(); await new Promise((r) => setTimeout(r, 80)); await settle(); }
    else if (step.op === 'snapshot') snapshots[step.label] = modalEl ? modalEl.innerHTML : null;
    else if (step.op === 'snapshotAppended') snapshots[step.label] = appended.join('');
    else if (step.op === 'click') {
      const el = byId(step.id);
      if (!el) errors.push('no element with id ' + step.id);
      else (el.listeners.click || []).forEach((fn) => fn({ stopPropagation() {}, preventDefault() {} }));
    } else if (step.op === 'clickLock') {
      const btn = lockButtons().find((b) => b.attrs['data-key'] === step.key);
      if (!btn) errors.push('no lock button for ' + step.key);
      else (btn.listeners.click || []).forEach((fn) => fn({}));
    }
  }
  await settle();
  process.stdout.write(JSON.stringify({ snapshots, fetches, errors }));
})();
"""


def _read_source(name: str) -> str:
    return (_STATIC / name).read_text(encoding="utf-8")


def _node(script: str, payload: dict) -> str:
    result = subprocess.run(
        ["node", "-e", script],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=_NODE_TIMEOUT_SECONDS,
        check=False,
    )
    assert result.returncode == 0, f"node failed: {result.stderr[-2000:]}"
    return result.stdout


def settings_source_with_exports(names: list[str]) -> str:
    """``static/settings.js`` with its closure-local helpers exported on ``globalThis.__t``."""
    source = _read_source("settings.js")
    match = _IIFE_CLOSE.search(source.rstrip() + "\n")
    assert match, "static/settings.js no longer ends with an IIFE close the harness can export from"
    trimmed = source.rstrip()
    match = _IIFE_CLOSE.search(trimmed)
    exports = ", ".join(f"{n}: typeof {n} === 'function' ? {n} : undefined" for n in names)
    return (
        trimmed[: match.start()]
        + f"\nglobalThis.__t = {{ {exports} }};\n"
        + trimmed[match.start() :]
    )


def run_settings_helpers(calls: list[tuple[str, list]]) -> list:
    """Call ``fn(*args)`` for each ``(fn, args)`` inside settings.js and return the values.

    A function that settings.js does not define fails the test naming it. Arguments are
    deep-frozen first, so a helper that mutates its input raises (the script is strict).
    """
    names = sorted({fn for fn, _ in calls})
    payload = {
        "source": settings_source_with_exports(names),
        "calls": [{"fn": fn, "args": args} for fn, args in calls],
    }
    results = json.loads(_node(_SETTINGS_RUNNER, payload))
    values = []
    for (fn, _args), res in zip(calls, results, strict=True):
        assert "missing" not in res, f"static/settings.js defines no function named {fn}"
        assert "error" not in res, f"{fn} raised in settings.js: {res['error']}"
        values.append(res.get("value"))
    return values


def run_modal_scenario(routes: dict, steps: list[dict]) -> dict:
    """Run ``static/settings-modal.js`` through ``steps``; returns snapshots, fetches, errors.

    ``routes`` maps ``"<METHOD> <url>"`` to ``{"json": ...}``, ``{"status": n, "json": ...}``
    or ``{"reject": true}``. ``/api/csrf-token`` is always answered.
    """
    payload = {
        "guardSource": _read_source("request_guard.js"),
        "source": _read_source("settings-modal.js"),
        "routes": routes,
        "steps": steps,
    }
    out = json.loads(_node(_MODAL_RUNNER, payload))
    assert not out["errors"], f"modal scenario could not find a control: {out['errors']}"
    return out


class _Collector(HTMLParser):
    """Collects text nodes and attributes, and the text under each element that has a title."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts: list[str] = []
        self.attrs: list[tuple[str, str, str]] = []
        self.titled: list[dict] = []
        self._stack: list[dict | None] = []

    def handle_starttag(self, tag, attrs):
        entry = None
        for name, value in attrs:
            self.attrs.append((tag, name, value or ""))
            if name == "title":
                entry = {"tag": tag, "title": value or "", "text": ""}
                self.titled.append(entry)
        self._stack.append(entry)

    def handle_endtag(self, tag):
        if self._stack:
            self._stack.pop()

    def handle_data(self, data):
        self.texts.append(data)
        for entry in self._stack:
            if entry is not None:
                entry["text"] += data


def parse_markup(html: str) -> _Collector:
    collector = _Collector()
    collector.feed(html)
    collector.close()
    return collector
