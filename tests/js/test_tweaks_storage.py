"""The stored theme applies the moment ``static/tweaks.js`` runs, and a change made in another tab applies
without a reload.

Real call path: the real ``static/tweaks.js`` runs under node (``vm``) against a stand-in page: a root
element that records the attributes written to it, a ``localStorage`` that counts its writes, and the
window's event dispatch. The stored value is the one the script's own ``studioTweaks.set`` writes (read
back from a first run), or its invalid-JSON variant. The stand-in claims nothing about paint: that the
theme is on the root before the body's first paint is judged in a browser
(tests/ui/test_theme_prepaint_browser.py) and by the head order (tests/ui/test_theme_prepaint_order.py).

Derived record: the invalid-JSON value is the stored value with its closing brace removed:
tests/fixtures/refresh_timing/03-c7/PROVENANCE.md.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.dashboard import _index_js_harness as hx
from tests.dashboard import _refresh_timing_support as sup

REPO = Path(__file__).resolve().parents[2]
TWEAKS_JS = REPO / "static" / "tweaks.js"

DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync(process.argv[2], 'utf8');
const body = fs.readFileSync(process.argv[3], 'utf8');

// A page for tweaks.js: the root records every attribute and property written to it.
function makePage(initialStorage) {
  const store = new Map(Object.entries(initialStorage || {}));
  const writes = [];
  const events = [];
  const listeners = { window: {}, document: {} };
  const attrs = {};
  const root = {
    setAttribute: function (k, v) { attrs[k] = String(v); },
    getAttribute: function (k) { return Object.prototype.hasOwnProperty.call(attrs, k) ? attrs[k] : null; },
    style: {
      setProperty: function (k, v) { attrs['style:' + k] = String(v); },
      removeProperty: function (k) { delete attrs['style:' + k]; }
    }
  };
  const on = function (where) { return function (type, fn) { (listeners[where][type] = listeners[where][type] || []).push(fn); }; };
  const doc = { documentElement: root, getElementById: function () { return null; }, addEventListener: on('document') };
  const win = {
    addEventListener: on('window'),
    dispatchEvent: function (e) {
      events.push({ type: e.type, detail: e.detail });
      (listeners.window[e.type] || []).slice().forEach(function (f) { f(e); });
      return true;
    }
  };
  const sandbox = {
    document: doc, window: win,
    localStorage: {
      getItem: function (k) { return store.has(k) ? store.get(k) : null; },
      setItem: function (k, v) { writes.push({ key: k, value: String(v) }); store.set(k, String(v)); },
      removeItem: function (k) { store.delete(k); }
    },
    getComputedStyle: function () { return { getPropertyValue: function () { return ' #1f7a4d '; } }; },
    CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init && init.detail; } },
    console: console
  };
  sandbox.window.localStorage = sandbox.localStorage;
  vm.createContext(sandbox);
  const page = {
    root: root, attrs: attrs, store: store, writes: writes, events: events, win: win, listeners: listeners,
    thrown: null,
    run: function () { try { vm.runInContext(source, sandbox); } catch (e) { page.thrown = String((e && e.stack) || e); } return page; },
    tools: function () { return sandbox.window.studioTweaks; },
    domReady: function () { (listeners.document['DOMContentLoaded'] || []).slice().forEach(function (f) { f({ type: 'DOMContentLoaded' }); }); },
    storage: function (init) { (listeners.window['storage'] || []).slice().forEach(function (f) { f(Object.assign({ type: 'storage' }, init)); }); },
    listening: function (type) { return (listeners.window[type] || []).length; }
  };
  return page;
}
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
new AsyncFunction('makePage', body)(makePage).then(function (result) {
  process.stdout.write('\n__RESULT__' + JSON.stringify(result === undefined ? null : result) + '\n');
}, function (e) {
  process.stdout.write('\n__RESULT__' + JSON.stringify({ driverError: String((e && e.stack) || e) }) + '\n');
});
"""


def _node(scenario: str):
    node = shutil.which("node")
    if node is None:
        pytest.fail("node is not installed: the tweaks.js tests need it")
    work = tempfile.mkdtemp(prefix="tweaks_run_")
    try:
        driver = Path(work, "driver.js")
        driver.write_text(DRIVER, encoding="utf-8")
        body = Path(work, "scenario.js")
        body.write_text(scenario, encoding="utf-8")
        proc = subprocess.run(
            [node, str(driver), str(TWEAKS_JS), str(body)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            cwd=work,
            env={**os.environ, "NODE_NO_WARNINGS": "1"},
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    assert "__RESULT__" in proc.stdout, f"node produced no result: {proc.stderr[-800:]}"
    result = json.loads(proc.stdout.split("__RESULT__", 1)[1].strip().splitlines()[0])
    assert not (isinstance(result, dict) and "driverError" in result), result["driverError"]
    return result


# What the script's own ``set`` writes for a dark theme: the value a stored dark theme has.
WRITE_DARK = r"""
const first = makePage({}).run();
first.tools().set('theme', 'dark');
return first.store.get('studioTweaks');
"""


def _stored_dark() -> str:
    value = _node(WRITE_DARK)
    assert json.loads(value)["theme"] == "dark"
    return value


class TestTheThemeAppliesWhenTheScriptRuns:
    def test_a_stored_dark_theme_is_on_the_root_before_any_dom_event(self):
        r = _node(
            r"""
        const page = makePage({ studioTweaks: __STORED__ }).run();
        return { theme: page.root.getAttribute('data-theme'), thrown: page.thrown };
        """.replace("__STORED__", json.dumps(_stored_dark()))
        )
        assert r["thrown"] is None
        assert r["theme"] == "dark", "the root has no theme until the document finishes loading"

    def test_every_stored_attribute_is_applied_at_run_not_only_the_theme(self):
        stored = json.dumps(
            json.dumps(
                {
                    "theme": "dark",
                    "density": "compact",
                    "accent": "#123456",
                    "typeface": "Inter",
                    "mathOverlays": False,
                    "numFormat": "compact",
                }
            )
        )
        r = _node(
            r"""
        const page = makePage({ studioTweaks: __STORED__ }).run();
        return page.attrs;
        """.replace("__STORED__", stored)
        )
        assert r.get("data-theme") == "dark" and r.get("data-density") == "compact"
        assert r.get("data-num-format") == "compact" and r.get("data-math-overlays") == "false"
        assert r.get("style:--studio-accent") == "#123456"

    def test_with_nothing_stored_the_default_theme_applies_at_run(self):
        r = _node(
            r"""
        const page = makePage({}).run();
        return { theme: page.root.getAttribute('data-theme'), thrown: page.thrown };
        """
        )
        assert r["thrown"] is None and r["theme"] == "light"

    def test_a_stored_value_that_is_not_json_gives_the_default_theme_and_no_error(self):
        broken = _stored_dark()[:-1]  # the closing brace removed
        with pytest.raises(json.JSONDecodeError):
            json.loads(broken)
        r = _node(
            r"""
        const page = makePage({ studioTweaks: __STORED__ }).run();
        page.domReady();
        return { theme: page.root.getAttribute('data-theme'), thrown: page.thrown };
        """.replace("__STORED__", json.dumps(broken))
        )
        assert r["thrown"] is None, "an unreadable stored value stopped the script"
        assert r["theme"] == "light"

    def test_the_dom_ready_handler_still_leaves_the_same_theme(self):
        r = _node(
            r"""
        const page = makePage({ studioTweaks: __STORED__ }).run();
        page.domReady();
        return { theme: page.root.getAttribute('data-theme') };
        """.replace("__STORED__", json.dumps(_stored_dark()))
        )
        assert r["theme"] == "dark"


class TestAChangeInAnotherTabApplies:
    def test_a_storage_event_for_the_tweaks_key_applies_the_new_theme_and_announces_it_once(self):
        r = _node(
            r"""
        const page = makePage({}).run();
        const before = page.root.getAttribute('data-theme');
        const other = makePage({}).run();
        other.tools().set('theme', 'dark');
        const stored = other.store.get('studioTweaks');
        page.store.set('studioTweaks', stored);          // what the browser shares between tabs
        const writes0 = page.writes.length;
        const events0 = page.events.length;
        page.storage({ key: 'studioTweaks', newValue: stored });
        return { before: before, after: page.root.getAttribute('data-theme'),
                 announced: page.events.slice(events0).filter(function (e) { return e.type === 'tweakchange'; }).length,
                 writes: page.writes.length - writes0, listening: page.listening('storage') };
        """
        )
        assert r["listening"] == 1, "no storage listener is registered"
        assert r["before"] == "light" and r["after"] == "dark"
        assert r["announced"] == 1, "the charts recolour on tweakchange, once"
        assert r["writes"] == 0, "applying another tab's change must not write storage back"

    def test_a_storage_event_for_another_key_changes_nothing(self):
        r = _node(
            r"""
        const page = makePage({}).run();
        const events0 = page.events.length;
        const attrs = JSON.stringify(page.attrs);
        page.storage({ key: 'somethingElse', newValue: '{"theme":"dark"}' });
        return { same: JSON.stringify(page.attrs) === attrs, announced: page.events.length - events0 };
        """
        )
        assert r["same"] and r["announced"] == 0

    def test_two_tabs_cannot_loop(self):
        r = _node(
            r"""
        const a = makePage({}).run();
        const b = makePage({}).run();
        a.tools().set('theme', 'dark');
        const stored = a.store.get('studioTweaks');
        b.store.set('studioTweaks', stored);
        b.storage({ key: 'studioTweaks', newValue: stored });
        return { bWrites: b.writes.filter(function (w) { return w.value === stored; }).length,
                 bEvents: b.events.filter(function (e) { return e.type === 'tweakchange'; }).length };
        """
        )
        assert r["bWrites"] == 0 and r["bEvents"] == 1

    def test_the_number_format_change_sets_the_attribute_the_next_polls_formatter_reads(self):
        r = _node(
            r"""
        const page = makePage({}).run();
        const other = makePage({}).run();
        other.tools().set('numFormat', 'compact');
        const stored = other.store.get('studioTweaks');
        page.store.set('studioTweaks', stored);
        page.storage({ key: 'studioTweaks', newValue: stored });
        return { attr: page.root.getAttribute('data-num-format') };
        """
        )
        assert r["attr"] == "compact"

    def test_a_poll_after_the_attribute_changes_prints_the_new_format(self):
        """The poll's own formatter reads the attribute at each format (no cached setting)."""
        world = hx.get_world("default")
        stamp = sup.render_stamp(world)
        strip = sup.strip_with(world["strips"]["30d"], guard_alpha=1234.5)
        r = hx.run_scenario(
            world,
            r"""
        await H.boot({ extras: true });
        const D = H.sandbox.document;
        D.documentElement.dataset.numFormat = 'compact';
        H.route('/api/state', { body: H.bodies.later });
        await H.tick(); await H.completeAnimations();
        return { headline: H.text('#guard-alpha-headline') };
        """,
            scripts=sup.CHROME_AND_INDEX,
            extra_bodies={
                "strips": {**world["strips"], "30d": strip},
                "later": sup.with_stamp(world["next"]["state"], sup.later_stamp(stamp)),
            },
            now_iso=hx.seeded_now_iso(world),
        )["result"]
        assert r["headline"] == "1.2k pp"
