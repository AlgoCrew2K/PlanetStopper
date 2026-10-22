"""Test support: run the real dashboard scripts against the real server-rendered page under node.

What is real here: ``static/chrome.js`` and ``static/index.js`` run whole, in one global scope, exactly
as the browser loads them; the DOM they act on is built from the HTML that ``GET /`` really renders
(parsed by the standard library, never hand-written); the ``/api/state`` bodies handed to ``fetch``
come from the real ``/api/state`` route.

What is stubbed: the network (``fetch`` answers from a route table), timers (recorded, never fired
by themselves), ``EventSource`` (records listeners) and a small DOM (selectors, classes, attributes,
``hidden``). Chart.js is deliberately absent: a draw is judged on a live render, never here.

Nothing here reads or writes the developer ``.env`` and nothing touches the network.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
INDEX_JS = ROOT / "static" / "index.js"
CHROME_JS = ROOT / "static" / "chrome.js"
ACCOUNT_FIXTURE = (
    ROOT / "tests" / "fixtures" / "dashboard" / "account_filter" / "two_accounts_bot_state.json"
)
FROZEN_FIXTURE = (
    ROOT / "tests" / "fixtures" / "dashboard" / "frozen_state" / "frozen_state_with_symphonies.json"
)

REJECT = {"__reject__": True}


def load_accounts_fixture() -> dict:
    return json.loads(ACCOUNT_FIXTURE.read_text(encoding="utf-8"))


def symphony_entries(bot_state: dict) -> dict:
    """The symphony entries of a bot_state map (dicts that carry a name), keyed by id."""
    return {k: v for k, v in bot_state.items() if isinstance(v, dict) and "name" in v}


def ids_for_account(bot_state: dict, account: str) -> list[str]:
    return [k for k, v in symphony_entries(bot_state).items() if v.get("account") == account]


def is_active(sym: dict) -> bool:
    """Active section membership: armed in any form, or already exited."""
    return bool(
        sym.get("armed") or sym.get("tp_armed") or sym.get("para_armed") or sym.get("triggered")
    )


def isolate_environment(monkeypatch, tmp_path) -> None:
    """Every env var the dashboard routes read is deleted, and the env file path points at nothing."""
    import app as app_module

    for name in (
        "LIVE_EXECUTION",
        "EXIT_AUTHORITY",
        "EXECUTION_START_TIME",
        "ACCOUNT_INDIVIDUAL",
        "ACCOUNT_ROTH",
        "ACCOUNT_TRAD",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(app_module, "ENV_FILE_PATH", str(tmp_path / "absent.env"))
    monkeypatch.setattr(app_module, "dotenv_values", lambda *_a, **_k: {})


def analytics_mock() -> MagicMock:
    """Analytics stand-in whose portfolio figures depend on the symphony set it is given.

    A route that computes the strip from a different symphony set than another route therefore
    returns different numbers, instead of the constant a plain mock would return for both.
    """
    m = MagicMock()

    def _by_set(symphonies_list, *_a, **_k):
        n = float(len(symphonies_list))
        return {"if_held": n, "dry_run": n}

    def _mdd_by_set(symphonies_list, *_a, **_k):
        n = float(len(symphonies_list))
        return {"if_held": n, "dry_run": n, "n_obs": 0}

    m.get_portfolio_today_change.side_effect = _by_set
    m.get_portfolio_cumulative_return.side_effect = _by_set
    m.get_portfolio_max_drawdown.side_effect = _mdd_by_set
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 15.0, "dry_run": 14.0}
    m.get_symphony_max_drawdown.return_value = {"if_held": 0.08, "dry_run": 0.07}
    m.get_portfolio_daily_returns_from_shadow.return_value = None
    m.get_history_with_cache_invalidation.return_value = {}
    m.compute_aggregate_returns.return_value = ([], [], [])
    m._POST_MORTEMS_DIR = "/tmp/no-such-dir"
    return m


@contextmanager
def state_api(monkeypatch, bot_state: dict, market_state: str = "open"):
    """A Flask test client for /api/state over ``bot_state``. Only database and analytics are mocked."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with patch.object(app_module, "database") as db:
        db.load_state.side_effect = lambda: copy.deepcopy(bot_state)
        db.normalize_name.side_effect = lambda n: n.strip().lower()
        db.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        db.get_symphony_strategy.return_value = {"params": {}, "locked_vars": []}
        db.read_fleet_alert.return_value = None
        db.get_triggers.return_value = []
        db.get_guard_alpha_by_symphony.return_value = {}
        db.read_port_state.return_value = None
        monkeypatch.setattr(app_module, "render_template", lambda *_a, **_k: "")
        monkeypatch.setattr(app_module, "analytics", analytics_mock())
        monkeypatch.setattr(app_module, "get_market_state", lambda *_a, **_k: market_state)
        # A cold account-totals cache, so the strip's account value is the sum over the symphonies given.
        monkeypatch.setattr(app_module, "_account_totals_cache", app_module._StaleFlagDict())
        monkeypatch.setattr(app_module, "_account_totals_last_good", {})
        with app_module.app.test_client() as client:
            yield client


def frozen_bot_state(fixture: dict) -> dict:
    """bot_state for a closed market: a close snapshot whose roster holds the fixture's symphonies."""
    base = json.loads(FROZEN_FIXTURE.read_text(encoding="utf-8"))["snapshot_input"]
    snapshot = copy.deepcopy(base)
    accounts_map: dict[str, list[dict]] = {}
    for sym_id, sym in symphony_entries(fixture["bot_state"]).items():
        entry = copy.deepcopy(sym)
        entry["id"] = sym_id
        accounts_map.setdefault(sym["account"], []).append(entry)
    snapshot["accounts_map"] = accounts_map
    snapshot["shadow_divergence"] = {"by_symphony": {}, "portfolio_today": None}
    return {"date": fixture["bot_state"]["date"], "last_market_close_snapshot": snapshot}


def render_dashboard_html(bot_state: dict) -> str:
    """The HTML of GET / over ``bot_state`` (real template, real database layer on a temp database)."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    api_state = {
        "status": "active",
        "market_state": "open",
        "live_mode": False,
        "next_run_seconds": 37,
        "data_as_of": "10:31 ET",
        "is_locked": False,
        "bot_state": copy.deepcopy(bot_state),
        "port_state": {},
        "exit_authority": {},
        "daemon_started_at": "2026-10-22T09:00:00",
        "portfolio_strip": {
            "today_change": {"if_held": 0.45, "dry_run": 0.61},
            "cumulative_return": {"if_held": 12.3, "dry_run": 15.8},
            "max_drawdown": {"if_held": 4.2, "dry_run": 3.1},
            "hist_dates": [],
            "hist_bot": [],
            "hist_held": [],
        },
        "meta": {
            "mode": "DRY RUN",
            "system_online": True,
            "tracked": 4,
            "armed": 0,
            "triggered": 2,
            "next_run": "00:37",
            "account": {"label": "Main Account", "uuid_short": "ab12cd34"},
            "market_state": "open",
            "market_state_label": "Market open",
            "clock_et": "10:31:00 AM ET",
            "portfolio": {
                "tc": 0.61,
                "tc_if_held": 0.45,
                "cr": 15.8,
                "cr_if_held": 12.3,
                "mdd": 3.1,
                "mdd_if_held": 4.2,
                "hist_dates": [],
                "hist_bot": [],
                "hist_held": [],
                "data_as_of": "10:31 ET",
            },
        },
        "shadow_divergence": None,
    }
    with patch.object(app_module, "get_api_state_dict", return_value=api_state):
        with app_module.app.test_client() as client:
            response = client.get("/")
    assert response.status_code == 200, f"GET / answered {response.status_code}"
    return response.get_data(as_text=True)


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


class _ElementCollector(HTMLParser):
    """Flattens HTML into elements (tag, attributes, text content, parent index); drops script and style."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[dict] = []
        self._stack: list[int] = []
        self._raw: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._raw = tag
            return
        element = {
            "tag": tag,
            "attrs": {k: ("" if v is None else v) for k, v in attrs},
            "parent": self._stack[-1] if self._stack else -1,
            "text": [],
        }
        self.elements.append(element)
        if tag not in _VOID:
            self._stack.append(len(self.elements) - 1)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and tag not in ("script", "style") and self._stack:
            self._stack.pop()

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._raw = None
            return
        for pos in range(len(self._stack) - 1, -1, -1):
            if self.elements[self._stack[pos]]["tag"] == tag:
                del self._stack[pos:]
                return

    def handle_data(self, data):
        if self._raw:
            return
        for index in self._stack:
            self.elements[index]["text"].append(data)


def parse_elements(html: str) -> list[dict]:
    collector = _ElementCollector()
    collector.feed(html)
    collector.close()
    return [{**e, "text": "".join(e["text"])} for e in collector.elements]


def find_elements(html: str, **attrs: str) -> list[dict]:
    """Elements of the rendered HTML whose attributes equal every given value (``data_testid`` for data-testid)."""
    wanted = {k.replace("_", "-"): v for k, v in attrs.items()}
    return [
        e for e in parse_elements(html) if all(e["attrs"].get(k) == v for k, v in wanted.items())
    ]


def class_list(element: dict) -> list[str]:
    return (element["attrs"].get("class") or "").split()


HARNESS_JS = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const cfg = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const realSetImmediate = setImmediate;

const requests = [];
const unrouted = [];
const textWrites = [];
const errors = [];
const intervals = [];
const docListeners = {};
const eventSources = [];
const allElements = [];

function kebab(s) { return s.replace(/[A-Z]/g, function (c) { return '-' + c.toLowerCase(); }); }

class El {
  constructor(tag, attrs, text) {
    this.tagName = String(tag).toUpperCase();
    this._tag = String(tag).toLowerCase();
    this._attrs = Object.assign({}, attrs || {});
    this.children = [];
    this.parentNode = null;
    this._text = text || '';
    this._html = '';
    this.style = {};
    this._listeners = {};
    this.innerHTMLWrites = [];
    const self = this;
    this.classList = {
      add: function () {
        const s = self._classes();
        for (const c of arguments) if (s.indexOf(c) < 0) s.push(c);
        self._attrs['class'] = s.join(' ');
      },
      remove: function () {
        const rm = Array.from(arguments);
        self._attrs['class'] = self._classes().filter(function (c) { return rm.indexOf(c) < 0; }).join(' ');
      },
      contains: function (c) { return self._classes().indexOf(c) >= 0; },
      toggle: function (c, force) {
        const has = self._classes().indexOf(c) >= 0;
        const want = force === undefined ? !has : !!force;
        if (want) this.add(c); else this.remove(c);
        return want;
      }
    };
    this.dataset = new Proxy({}, {
      get: function (_, k) {
        if (typeof k !== 'string') return undefined;
        const a = 'data-' + kebab(k);
        return Object.prototype.hasOwnProperty.call(self._attrs, a) ? self._attrs[a] : undefined;
      },
      set: function (_, k, v) { self._attrs['data-' + kebab(String(k))] = String(v); return true; },
      has: function (_, k) { return Object.prototype.hasOwnProperty.call(self._attrs, 'data-' + kebab(String(k))); },
      deleteProperty: function (_, k) { delete self._attrs['data-' + kebab(String(k))]; return true; }
    });
    allElements.push(this);
  }
  _classes() { return (this._attrs['class'] || '').split(/\s+/).filter(Boolean); }
  get id() { return this._attrs.id || ''; }
  set id(v) { this._attrs.id = String(v); }
  get className() { return this._attrs['class'] || ''; }
  set className(v) { this._attrs['class'] = String(v); }
  get hidden() { return Object.prototype.hasOwnProperty.call(this._attrs, 'hidden'); }
  set hidden(v) { if (v) this._attrs.hidden = ''; else delete this._attrs.hidden; }
  get textContent() { return this._text; }
  set textContent(v) {
    this._text = String(v);
    textWrites.push({ id: this.id, testid: this._attrs['data-testid'] || '', text: this._text });
  }
  get innerHTML() { return this._html; }
  set innerHTML(v) { this._html = String(v); this.innerHTMLWrites.push(this._html); }
  get firstChild() { return this.children[0] || null; }
  getAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n) ? this._attrs[n] : null; }
  setAttribute(n, v) { this._attrs[n] = String(v); }
  hasAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n); }
  removeAttribute(n) { delete this._attrs[n]; }
  addEventListener(t, f) { (this._listeners[t] = this._listeners[t] || []).push(f); }
  removeEventListener() {}
  appendChild(c) {
    if (c.parentNode) c.parentNode.children = c.parentNode.children.filter(function (x) { return x !== c; });
    c.parentNode = this;
    this.children.push(c);
    return c;
  }
  insertAdjacentHTML() {}
  remove() { if (this.parentNode) { const p = this.parentNode; p.children = p.children.filter((x) => x !== this); this.parentNode = null; } }
  contains(n) { for (let e = n; e; e = e.parentNode) if (e === this) return true; return false; }
  focus() {}
  blur() {}
  scrollIntoView() {}
  getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0, right: 0, bottom: 0 }; }
  getContext() { return new Proxy({}, { get: function () { return function () { return {}; }; }, set: function () { return true; } }); }
  matches(sel) { return compile(sel).some((l) => matchComplex(this, l, l.length - 1)); }
  closest(sel) { const lists = compile(sel); for (let e = this; e && e._tag !== undefined; e = e.parentNode) { if (lists.some((l) => matchComplex(e, l, l.length - 1))) return e; } return null; }
  querySelectorAll(sel) {
    const lists = compile(sel);
    const out = [];
    (function walk(nodes) {
      nodes.forEach(function (e) {
        if (lists.some(function (l) { return matchComplex(e, l, l.length - 1); })) out.push(e);
        walk(e.children);
      });
    })(this.children);
    return out;
  }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
}

function parseCompound(s) {
  const parts = s.match(/#[\w-]+|\.[\w-]+|\[[^\]]+\]|[a-zA-Z][\w-]*|\*/g) || [];
  if (parts.join('') !== s) throw new Error('unsupported selector: ' + s);
  return parts.map(function (p) {
    if (p[0] === '#') return { k: 'id', v: p.slice(1) };
    if (p[0] === '.') return { k: 'cls', v: p.slice(1) };
    if (p[0] === '[') {
      const m = p.slice(1, -1).match(/^([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|(.*)))?$/);
      if (!m) throw new Error('unsupported attribute selector: ' + p);
      const val = m[2] !== undefined ? m[2] : (m[3] !== undefined ? m[3] : m[4]);
      return { k: 'attr', n: m[1], v: val };
    }
    if (p === '*') return { k: 'any' };
    return { k: 'tag', v: p.toLowerCase() };
  });
}
function matchCompound(el, parts) {
  return parts.every(function (p) {
    if (p.k === 'id') return el._attrs.id === p.v;
    if (p.k === 'cls') return el._classes().indexOf(p.v) >= 0;
    if (p.k === 'attr') return p.v === undefined ? el.hasAttribute(p.n) : el._attrs[p.n] === p.v;
    if (p.k === 'any') return true;
    return el._tag === p.v;
  });
}
function splitTop(sel, isSep) {
  const out = [];
  let cur = '';
  let depth = 0;
  for (const ch of sel) {
    if (ch === '[') depth++;
    if (ch === ']') depth--;
    if (depth === 0 && isSep(ch)) { out.push(cur); cur = ''; } else cur += ch;
  }
  out.push(cur);
  return out;
}
function compile(sel) {
  return splitTop(sel, function (c) { return c === ','; }).map(function (one) {
    const spaced = splitTop(one.trim().replace(/\s*>\s*/g, ' > '), function (c) { return /\s/.test(c); }).filter(Boolean);
    const comps = [];
    let comb = ' ';
    const list = [];
    spaced.forEach(function (tok) {
      if (tok === '>') { comb = '>'; return; }
      list.push({ comp: parseCompound(tok), comb: comb });
      comb = ' ';
    });
    return list;
  });
}
function matchComplex(el, list, idx) {
  if (!matchCompound(el, list[idx].comp)) return false;
  if (idx === 0) return true;
  if (list[idx].comb === '>') return !!el.parentNode && el.parentNode._tag !== undefined && matchComplex(el.parentNode, list, idx - 1);
  for (let a = el.parentNode; a && a._tag !== undefined; a = a.parentNode) {
    if (matchComplex(a, list, idx - 1)) return true;
  }
  return false;
}

const htmlEl = new El('html', {});
const bodyEl = new El('body', {});
htmlEl.appendChild(bodyEl);
const built = cfg.elements.map(function (e) { return new El(e.tag, e.attrs, e.text); });
cfg.elements.forEach(function (e, i) { (e.parent >= 0 ? built[e.parent] : bodyEl).appendChild(built[i]); });

function attached(e) { for (let n = e; n; n = n.parentNode) if (n === bodyEl) return true; return false; }

const document = {
  documentElement: htmlEl,
  body: bodyEl,
  readyState: 'complete',
  cookie: '',
  getElementById: function (id) {
    for (const e of allElements) if (e._attrs.id === id && attached(e)) return e;
    return null;
  },
  querySelector: function (sel) { return bodyEl.querySelector(sel); },
  querySelectorAll: function (sel) { return bodyEl.querySelectorAll(sel); },
  createElement: function (tag) { return new El(tag, {}, ''); },
  createTextNode: function (t) { return { nodeType: 3, textContent: String(t) }; },
  addEventListener: function (t, f) { (docListeners[t] = docListeners[t] || []).push(f); },
  removeEventListener: function () {}
};

globalThis.window = globalThis;
globalThis.document = document;
globalThis.getComputedStyle = function () { return { getPropertyValue: function () { return ''; } }; };
globalThis.location = { href: 'http://127.0.0.1/', pathname: '/', search: '', hash: '', reload: function () {} };
globalThis.setInterval = function (fn, ms) { intervals.push({ fn: fn, ms: ms }); return intervals.length; };
globalThis.clearInterval = function () {};
globalThis.setTimeout = function () { return 1; };
globalThis.clearTimeout = function () {};
globalThis.confirm = function () { return false; };
globalThis.alert = function () {};
console.error = function () { errors.push(Array.from(arguments).map(String).join(' ')); };
console.warn = function () {};
globalThis.EventSource = class {
  constructor(url) { this.url = url; this._l = {}; eventSources.push(this); }
  addEventListener(t, f) { (this._l[t] = this._l[t] || []).push(f); }
  close() {}
};

function respond(body, status) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status: status,
    json: function () { return Promise.resolve(JSON.parse(JSON.stringify(body))); },
    text: function () { return Promise.resolve(JSON.stringify(body)); }
  });
}
globalThis.fetch = function (url, opts) {
  const u = String(url);
  requests.push({ url: u, method: (opts && opts.method) || 'GET', headers: (opts && opts.headers) || {} });
  const qi = u.indexOf('?');
  const path = qi < 0 ? u : u.slice(0, qi);
  let key = u;
  if (path === '/api/state') {
    const account = qi < 0 ? null : new URLSearchParams(u.slice(qi + 1)).get('account');
    key = account === null ? path : path + '|' + account;
    if (!Object.prototype.hasOwnProperty.call(cfg.routes, key)) {
      unrouted.push(u);
      return Promise.reject(new Error('unrouted state request: ' + u));
    }
  } else if (!Object.prototype.hasOwnProperty.call(cfg.routes, key)) {
    key = path;
  }
  const route = Object.prototype.hasOwnProperty.call(cfg.routes, key) ? cfg.routes[key] : {};
  if (route && route.__reject__) return Promise.reject(new TypeError('network failure'));
  const status = (route && route.__status__) || 200;
  const body = route && route.__body__ !== undefined ? route.__body__ : route;
  return respond(body, status);
};

const H = {
  params: cfg.params || {},
  window: globalThis,
  document: document,
  requests: requests,
  errors: errors,
  textWrites: textWrites,
  intervals: intervals,
  eventSources: eventSources,
  unrouted: unrouted,
  flush: async function (n) { for (let i = 0; i < (n || 40); i++) await new Promise(function (r) { realSetImmediate(r); }); },
  fire: function (type) { (docListeners[type] || []).slice().forEach(function (f) { f({ type: type }); }); },
  click: function (el) {
    (el._listeners.click || []).slice().forEach(function (f) {
      f({ type: 'click', target: el, stopPropagation: function () {}, preventDefault: function () {} });
    });
  },
  clickCard: function (symId) {
    const card = document.querySelector('.sym-card[data-sym-id="' + symId + '"]');
    if (!card) throw new Error('no card rendered for ' + symId);
    vm.runInThisContext(card.getAttribute('onclick'));
  },
  byId: function (id) { return document.getElementById(id); },
  byTestid: function (t) { return document.querySelector('[data-testid="' + t + '"]'); },
  isHidden: function (el) { return !!(el.hidden || el.style.display === 'none'); },
  cardIds: function () { return document.querySelectorAll('.sym-card[data-sym-id]').map(function (c) { return c.getAttribute('data-sym-id'); }); },
  visibleCardIds: function () {
    return document.querySelectorAll('.sym-card[data-sym-id]')
      .filter(function (c) { return !H.isHidden(c); })
      .map(function (c) { return c.getAttribute('data-sym-id'); });
  },
  mark: function () { return requests.length; },
  stateRequests: function (from) {
    return requests.slice(from || 0)
      .filter(function (r) { return r.url.split('?')[0] === '/api/state'; })
      .map(function (r) { return r.url; });
  },
  poll: function () { intervals.filter(function (i) { return i.ms === 30000; }).forEach(function (i) { i.fn(); }); },
  cycleComplete: function () {
    eventSources.forEach(function (es) { (es._l['cycle-complete'] || []).slice().forEach(function (f) { f({ type: 'cycle-complete' }); }); });
  }
};

vm.runInThisContext(fs.readFileSync(cfg.chromeJs, 'utf8'), { filename: 'chrome.js' });
vm.runInThisContext(fs.readFileSync(cfg.indexJs, 'utf8'), { filename: 'index.js' });

const scenario = vm.runInThisContext('(' + fs.readFileSync(cfg.scenarioPath, 'utf8') + ')');
scenario(H).then(function (result) {
  process.stdout.write(JSON.stringify({ result: result, errors: errors, unrouted: unrouted }));
}).catch(function (e) {
  process.stderr.write(String(e && e.stack || e));
  process.exit(1);
});
"""


def run_page(
    tmp_path: Path, scenario_js: str, *, html: str, routes: dict, params: dict | None = None
):
    """Run the real scripts over the rendered page and return the scenario's JSON result.

    ``scenario_js`` is the source of ``async function (H) { ... return {...}; }``. ``routes`` maps
    ``/api/state`` (no account) and ``/api/state|<account>`` to bodies; other paths answer ``{}``
    unless listed (``REJECT`` makes the request fail).
    """
    node = shutil.which("node")
    assert node, (
        "node is required to run the dashboard scripts (the repository's JS syntax test needs it too)"
    )
    (tmp_path / "harness.js").write_text(HARNESS_JS, encoding="utf-8")
    (tmp_path / "scenario.js").write_text(scenario_js, encoding="utf-8")
    cfg = {
        "elements": parse_elements(html),
        "routes": routes,
        "params": params or {},
        "indexJs": str(INDEX_JS),
        "chromeJs": str(CHROME_JS),
        "scenarioPath": str(tmp_path / "scenario.js"),
    }
    (tmp_path / "cfg.json").write_text(json.dumps(cfg), encoding="utf-8")
    result = subprocess.run(
        [node, str(tmp_path / "harness.js"), str(tmp_path / "cfg.json")],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(tmp_path),
    )
    assert result.returncode == 0, f"node harness crashed:\n{result.stderr[-3000:]}"
    return json.loads(result.stdout)
