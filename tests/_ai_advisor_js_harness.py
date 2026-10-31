"""Run the real ``static/ai_advisor.js`` under node and read what it writes.

The script is loaded unmodified into a ``vm`` context with minimal ``document``,
``fetch`` and timer stubs. The harness fires the page's ``DOMContentLoaded`` handlers
and, on request, the 15 s poll callback, and returns the autotune list's markup after
each render. No client function is re-implemented here: every string a test reads was
written by the code under test, from route output the test obtained in the same run.

Test-double limits: ``fetch`` answers from the route table the test passes in, charts
are not drawn (``Chart`` is undefined), and ``document.querySelectorAll`` finds nothing.
A visible render is judged on a live page, never here.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

AI_ADVISOR_JS = pathlib.Path(__file__).resolve().parents[1] / "static" / "ai_advisor.js"

_HARNESS = r"""
const fs = require("fs");
const vm = require("vm");
const cfg = JSON.parse(fs.readFileSync(0, "utf8"));
function makeEl(id) {
  return {
    id, innerHTML: "", textContent: "", value: "", disabled: false, style: {}, dataset: {},
    classList: { add() {}, remove() {} }, addEventListener() {}, setAttribute() {},
    getAttribute() { return null; }, appendChild() {}, getContext() { return {}; },
  };
}
const list = makeEl("autotune-runs-list");
const docHandlers = [];
const intervals = [];
const document = {
  getElementById: (id) => (id === "autotune-runs-list" ? list : null),
  querySelectorAll: () => [],
  querySelector: () => null,
  createElement: () => makeEl(""),
  documentElement: {},
  addEventListener: (type, fn) => { if (type === "DOMContentLoaded") docHandlers.push(fn); },
};
function fetchStub(url) {
  const route = cfg.routes[url];
  if (route === undefined) {
    return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) });
  }
  if (route && route.__reject__) { return Promise.reject(new Error(route.__reject__)); }
  return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(route) });
}
const ctx = vm.createContext({
  document, fetch: fetchStub, console, Promise, JSON, window: {},
  setTimeout, clearTimeout, setInterval: (fn) => { intervals.push(fn); return intervals.length; },
  getComputedStyle: () => ({ getPropertyValue: () => "" }),
  sessionStorage: { setItem() {}, getItem() { return null; } },
});
const settle = () => new Promise((resolve) => setTimeout(resolve, 25));
(async () => {
  vm.runInContext(fs.readFileSync(cfg.js_path, "utf8"), ctx);
  const renders = [];
  docHandlers.forEach((fn) => fn());
  await settle();
  renders.push(list.innerHTML);
  for (let i = 0; i < cfg.polls; i++) {
    intervals.forEach((fn) => fn());
    await settle();
    renders.push(list.innerHTML);
  }
  process.stdout.write(JSON.stringify({ renders }));
})();
"""


def render_autotune_list(routes: dict, polls: int = 0) -> list[str]:
    """The ``#autotune-runs-list`` markup after the page load and after each poll tick.

    ``routes`` maps a URL to the JSON the stub returns; a value of
    ``{"__reject__": "<message>"}`` makes that fetch reject.
    """
    assert shutil.which("node"), "node is required to run the real advisor script"
    payload = json.dumps({"routes": routes, "polls": polls, "js_path": str(AI_ADVISOR_JS)})
    result = subprocess.run(
        ["node", "-e", _HARNESS],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, f"node harness failed: {result.stderr[-2000:]}"
    return json.loads(result.stdout)["renders"]
