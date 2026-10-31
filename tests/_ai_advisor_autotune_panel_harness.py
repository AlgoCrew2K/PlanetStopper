"""Run the real ``static/ai_advisor.js`` under node against the autotune panel's elements.

TEST-ONLY. The script is loaded unmodified into a ``vm`` context. The elements the autotune
panel owns are stand-ins that keep the properties a render touches (``innerHTML``,
``textContent``, ``hidden``, ``open``, ``style.display``, attributes) and remember whether a
write recreated them, and ``Chart`` is a counting stand-in that behaves like the real library
where it matters here: a second chart on a canvas that still holds one throws, and
``Chart.getChart`` reports the chart a canvas holds.

The elements, by id and by ``data-testid`` (the page's markup must carry both):

* ``autotune-runs-list``: the cards container;
* ``autotune-sparkline-canvas`` / ``autotune-sparkline``: the sparkline canvas, inline
  ``display:block`` as the template writes it;
* ``autotune-older-runs``: the ``<details>`` of older runs, hidden until it has rows;
* ``autotune-older-summary``: its ``<summary>``; ``autotune-older-list``: its rows container.

Steps (one list per call, one snapshot returned per step):

* ``{"op": "load"}``: fire the page's ``DOMContentLoaded`` handlers;
* ``{"op": "poll"}``: fire the callbacks the page registered with ``setInterval``;
* ``{"op": "routes", "routes": {...}}``: replace the answers ``fetch`` gives;
* ``{"op": "open"}``: the operator opens the older-runs ``<details>``;
* ``{"op": "foreign_chart"}``: a chart owned by something else now lives on the canvas.

``fetch`` answers from the route table; a value of ``{"__reject__": "<message>"}`` rejects.
No layout exists here: whether a hidden canvas takes space is judged in a browser module.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

AI_ADVISOR_JS = pathlib.Path(__file__).resolve().parents[1] / "static" / "ai_advisor.js"
REQUEST_GUARD_JS = AI_ADVISOR_JS.with_name("request_guard.js")

_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(fs.readFileSync(0, "utf8"));

const registry = {};
const byTestid = {};
function makeEl(id, testid, extra) {
  const el = {
    id, testid, _html: "", _text: "", value: "", disabled: false, hidden: false, open: false,
    style: Object.assign({}, (extra && extra.style) || {}), dataset: {}, attrs: {},
    children: [], parentNode: null, recreated: 0, writes: 0,
    classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
    addEventListener() {}, removeEventListener() {},
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; },
    hasAttribute(name) { return name in this.attrs; },
    setAttribute(name, value) {
      this.attrs[name] = String(value);
      if (name === "data-testid") { this.testid = String(value); byTestid[this.testid] = this; }
      if (name === "hidden") this.hidden = true;
      if (name === "open") this.open = true;
    },
    removeAttribute(name) {
      delete this.attrs[name];
      if (name === "hidden") this.hidden = false;
      if (name === "open") this.open = false;
    },
    appendChild(child) { return child; },
    insertBefore(child) { link(this, child); return child; },
    getContext() { return { canvas: this }; },
    querySelector(sel) { return findIn(this, sel); },
    querySelectorAll() { return []; },
    closest() { return null; },
  };
  const markDescendants = (node) => {
    node.children.forEach((c) => { c.recreated += 1; markDescendants(c); });
  };
  Object.defineProperty(el, "innerHTML", {
    get() { return this._html; },
    set(v) { this._html = String(v); this.writes += 1; markDescendants(this); },
  });
  Object.defineProperty(el, "outerHTML", {
    get() { return this._html; },
    set(v) { this.recreated += 1; markDescendants(this); },
  });
  Object.defineProperty(el, "textContent", {
    get() { return this._text; },
    set(v) { this._text = String(v); this.writes += 1; markDescendants(this); },
  });
  Object.defineProperty(el, "parentElement", { get() { return this.parentNode; } });
  if (id) registry[id] = el;
  if (testid) byTestid[testid] = el;
  return el;
}
function link(parent, child) { parent.children.push(child); child.parentNode = parent; }
function findIn(root, sel) {
  const m = /^\[data-testid="([^"]+)"\]$/.exec(sel);
  const hit = m ? byTestid[m[1]] : (sel[0] === "#" ? registry[sel.slice(1)] : null);
  if (!hit) return null;
  let p = hit.parentNode;
  while (p) { if (p === root) return hit; p = p.parentNode; }
  return root === document ? hit : null;
}

const aside = makeEl("autotune-panel-aside", "autotune-panel");
const canvas = makeEl("autotune-sparkline-canvas", "autotune-sparkline", { style: { display: "block" } });
const list = makeEl("autotune-runs-list", "autotune-runs-list");
const details = makeEl("autotune-older-runs", "autotune-older-runs");
details.hidden = true; details.attrs.hidden = "";
const summary = makeEl("autotune-older-summary", "autotune-older-summary");
const olderList = makeEl("autotune-older-list", "autotune-older-list");
[canvas, list, details].forEach((c) => link(aside, c));
link(details, summary); link(details, olderList);

const docHandlers = [];
const intervals = [];
const document = {
  getElementById: (id) => registry[id] || null,
  querySelectorAll: () => [],
  querySelector: (sel) => findIn(document, sel),
  createElement: () => makeEl("", ""),
  documentElement: {},
  body: makeEl("", ""),
  addEventListener: (type, fn) => { if (type === "DOMContentLoaded") docHandlers.push(fn); },
};

let routes = cfg.routes;
function fetchStub(url) {
  const route = routes[url];
  if (route === undefined) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  }
  if (route && route.__reject__) { return Promise.reject(new Error(route.__reject__)); }
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(route) });
}

// A Chart stand-in: one live chart per canvas, and a second construction on a canvas that
// still holds one throws, as the library does.
const liveCharts = new Map();
const constructed = [];
const thrown = [];
let nextChartId = 0;
function canvasOf(target) {
  if (typeof target === "string") return registry[target];
  return target && target.canvas ? target.canvas : target;
}
function Chart(target, config) {
  const cv = canvasOf(target);
  if (liveCharts.has(cv)) {
    const message = "Canvas is already in use. Chart with ID '" + liveCharts.get(cv).id +
      "' must be destroyed before the canvas with ID '" + cv.id + "' can be reused.";
    thrown.push(message);
    throw new Error(message);
  }
  this.id = nextChartId++;
  this.canvas = cv;
  this.config = config;
  this.data = config.data;
  this.options = config.options;
  this.destroyed = false;
  this.destroy = () => { if (liveCharts.get(cv) === this) liveCharts.delete(cv); this.destroyed = true; };
  this.update = () => {};
  liveCharts.set(cv, this);
  constructed.push({ id: this.id, points: (config.data.datasets[0] || { data: [] }).data.length });
}
Chart.getChart = (target) => liveCharts.get(canvasOf(target));
Chart.version = "stub";

const win = {
  addEventListener() {}, removeEventListener() {}, location: { hash: "" },
  history: { replaceState() {} },
};
const context = {
  document, fetch: fetchStub, console, Promise, JSON, window: win, location: win.location,
  history: win.history, Intl, Date, Math,
  setTimeout, clearTimeout, setInterval: (fn) => { intervals.push(fn); return intervals.length; },
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  sessionStorage: { setItem() {}, getItem() { return null; } },
  performance: { now: () => Date.now() },
};
if (cfg.chart) context.Chart = Chart;
const ctx = vm.createContext(context);

const settle = () => new Promise((resolve) => setTimeout(resolve, 30));
function isDisplayed(el) {
  if (el.style.display === "none") return false;
  if (el.hidden && (el.style.display === "" || el.style.display === undefined)) return false;
  return true;
}
function snapshot() {
  const live = liveCharts.get(canvas);
  return {
    list: list.innerHTML,
    older: {
      html: olderList.innerHTML,
      summary: summary.textContent,
      displayed: isDisplayed(details),
      open: details.open,
      detailsRecreated: details.recreated,
      summaryRecreated: summary.recreated,
      listRecreated: olderList.recreated,
    },
    canvas: { displayed: isDisplayed(canvas) },
    notice: byTestid["autotune-runs-notice"] ? byTestid["autotune-runs-notice"].textContent : null,
    chart: {
      constructed: constructed.slice(),
      thrown: thrown.slice(),
      liveCount: liveCharts.size,
      livePoints: live ? ((live.data.datasets[0] || { data: [] }).data.slice()) : null,
      liveLabelCount: live ? (live.data.labels || []).length : null,
      liveIsForeign: live ? !!live.foreign : false,
    },
  };
}

(async () => {
  // The page loads the token holder before ai_advisor.js (templates/_chrome.html); its global is bare in a browser.
  vm.runInContext(fs.readFileSync(cfg.guard_path, "utf8"), ctx, { filename: "request_guard.js" });
  ctx.PSRequest = win.PSRequest;
  vm.runInContext(fs.readFileSync(cfg.js_path, "utf8"), ctx);
  const out = [];
  for (const step of cfg.steps) {
    if (step.op === "load") {
      docHandlers.forEach((fn) => fn());
    } else if (step.op === "poll") {
      intervals.forEach((fn) => fn());
    } else if (step.op === "routes") {
      routes = step.routes;
    } else if (step.op === "open") {
      details.open = true; details.attrs.open = "";
    } else if (step.op === "foreign_chart" && cfg.chart) {
      const foreign = new Chart(canvas.getContext("2d"), { data: { labels: [1], datasets: [{ data: [1] }] }, options: {} });
      foreign.foreign = true;
    }
    await settle();
    out.push(snapshot());
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def drive(steps: list[dict], routes: dict, *, chart: bool = True) -> list[dict]:
    """Run the page script under node through ``steps``; one snapshot per step.

    ``routes`` maps a URL to the JSON ``fetch`` answers with. ``chart=False`` leaves ``Chart``
    undefined, as when the library's script could not load.
    """
    assert shutil.which("node"), "node is required to run the real advisor script"
    payload = json.dumps(
        {
            "routes": routes,
            "steps": steps,
            "chart": chart,
            "js_path": str(AI_ADVISOR_JS),
            "guard_path": str(REQUEST_GUARD_JS),
        }
    )
    result = subprocess.run(
        ["node", "-e", _HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, f"node harness failed: {result.stderr[-2000:]}"
    return json.loads(result.stdout)
