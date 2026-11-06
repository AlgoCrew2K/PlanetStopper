"""Shared measurement and capture harness of the page-explainers work.

TEST-ONLY. Three things live here, each used by more than one cycle:

* **Seeded worlds** (:func:`seed_world`): the states the pages are read over, every one written
  through the real writers from the captured records (``tests/fixtures/prod_droplet/``,
  ``tests/fixtures/ai_advisor/``, ``tests/fixtures/retirement/``) or the real accessors with the
  real producers' output shapes. Nothing is built to match a test.
* **In-page measurements**, as JavaScript strings run with ``page.evaluate``:
  :data:`EMPTY_REGION_JS` (the exact empty-rectangle search of the plan's AC-6),
  :data:`FIGURE_SPAN_JS`, :data:`HEIGHT_FACTS_JS` (AC-7 heights), :data:`FIGURES_JS` and
  :data:`CONTROLS_JS` (the AC-5 figure and control inventories).
* **The readings the reference capture and the checks share**: :func:`read_server_side` (headings,
  banner markup, route bodies, the login and logout pages, the Discord payload, content pins) and
  :func:`read_host_in_browser`. ``tests/fixtures/page_explainers/produce_ref_capture.py`` calls the
  same functions at the reference commit, so a difference between the capture and a head is a
  difference between the trees and never between two readers.

The clock is fixed as in ``tests/ui/_heading_margin_capture.py`` (``CAPTURE_INSTANT`` for the app
and the browser, ``WINDOW_ANCHOR`` for the date windows count back from) and the CSRF token that
every page embeds is fixed, so two renders of one state are the same bytes.

Prose is not typed here: the texts a change rewords come from ``tests/ui/_page_copy.py``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import re
import subprocess
from collections.abc import Callable

import app as app_module
import database
from tests._ux_truth_support import captured_symphonies
from tests.app._refresh_timing_state_world import _pinned_window_cutoff, frozen_datetime_class
from tests.app._state_poll_worlds import PROCESS_KEYS
from tests.ui._heading_margin_capture import CAPTURE_INSTANT, WINDOW_ANCHOR
from tests.ui._shell_support import (
    NARROW,
    PAGES,
    WIDE,
    open_page,
    open_probe,
    seed_candidate_alert_survivor,
    seed_dashboard_state,
    seed_history_exits,
    seed_pending_frontrunner_proposal,
)

REPO = pathlib.Path(__file__).resolve().parents[2]
FIXTURE_DIR = REPO / "tests" / "fixtures" / "page_explainers"
REF_CAPTURE_PATH = FIXTURE_DIR / "ref_capture.json"

REFERENCE_COMMIT = "1988ce5f"
BUILD_BASE_COMMIT = "8f8be28d"

FIXED_CSRF_TOKEN = "page-explainers-fixed-csrf-token"
PLACEHOLDER_KEY = "placeholder-not-a-credential"
# An operator override of an advisor model is read verbatim and loaded from the developer .env.
MODEL_OVERRIDE_VARIABLES = ("ADVISOR_SUGGESTION_MODEL", "ADVISOR_SYNTHESIS_MODEL")
SETTLE_MS = 1500

# World name -> the hosts read in it. The worlds are cumulative: a later one holds everything an
# earlier one seeds. The dashboard-only worlds are read at ``/`` alone, which is what they change.
WORLD_HOSTS: dict[str, tuple[str, ...]] = {
    "empty": PAGES,
    "dashboard_full": ("/",),
    "dashboard_sleeves": ("/",),
    "full": PAGES,
}
WORLDS = tuple(WORLD_HOSTS)
WORLD_HOST_PAIRS = [(w, h) for w, hosts in WORLD_HOSTS.items() for h in hosts]

# ---------------------------------------------------------------------------
# Selectors the plan names
# ---------------------------------------------------------------------------

# Elements the three copy tables reword; the AC-7 bound adds each one's growth. A selector that
# matches nothing on a host in a world is recorded with no heights, never an error.
RELABEL_SELECTORS: dict[str, tuple[str, ...]] = {
    "/": (
        '[data-testid="guard-alpha-descriptor"]',
        '[data-field="mdd-lifetime-cell"]',
        ".detail-stat-label",
        ".legend-key",
        '[data-testid="status-pill"]',
        '[data-testid="card-verdict"]',
    ),
    "/history": (".hp-fig-label", "th", ".hp-caption", ".hp-note"),
    "/performance": (
        ".metrics-caption",
        "th",
        '[data-testid="insufficient-banner"]',
        ".legend-key",
    ),
    "/settings": (".sec-subtitle", ".live-desc", ".field-hint", ".cred-warn"),
    "/ai-advisor": (
        '[data-testid="prism-model-badge"]',
        ".cap-tab-attribution",
        ".run-controls-note",
        '[data-testid="fr-overlay-preview"] > summary',
    ),
}

# Banners the plan pins byte for byte (S-5.3): key -> (host, selector). Every match's markup is
# read; a banner a seeded world does not render is recorded as an empty list.
BANNER_SELECTORS: dict[str, tuple[str, str]] = {
    "crisis_caveat": ("/ai-advisor", '[data-testid="crisis-caveat-banner"]'),
    "advise_only": ("/ai-advisor", '[data-testid="advise-only-banner"]'),
    "logic_change_fdr": ("/ai-advisor", '[data-testid="fdr-warning-banner"]'),
    "strategy_builder_risk": ("/ai-advisor", '[data-testid="strategy-builder-risk-banner"]'),
    "frontrunner_risk": ("/ai-advisor", '[data-testid="frontrunner-builder-risk-banner"]'),
    "settings_live_warning": ("/settings", "#live-banner"),
    "settings_credential_warning": ("/settings", ".cred-warn"),
    "settings_maintenance_note": ("/settings", ".maint-warn"),
    # The panel's own two notes; the description a page adds beside them is not one of them.
    "cvar_notes": ("/", "#cvar-diagnostic-panel .dash-panel-note:not([data-explainer])"),
    "fleet_banner": ("/", "#fleet-correlation-banner"),
    "fleet_modal": ("/", "#fleet-info-modal"),
}

# The read-only routes whose bodies must not change (S-5.6).
ROUTE_PATHS = (
    "/api/state",
    "/api/performance",
    "/api/history/30",
    "/api/retirement-recommendations",
    "/api/guard-alpha-summary",
    "/api/autotune-runs",
    "/api/settings",
)

# The three controls AC-5 excepts: rendered at the reference, handled by no script.
REMOVED_CONTROL_TEXTS = ("View logs", "Go to cash", "Edit variables")

# The panels AC-6 measures.
PANEL_SELECTOR = (
    ".hero-section, .dash-panel, .hp-panel, .adv-panel, .adv-card, .sym-card, .settings-section"
)

# ---------------------------------------------------------------------------
# In-page measurements
# ---------------------------------------------------------------------------

EMPTY_REGION_JS = r"""
(args) => {
  const [selector, minW, minH] = args;
  const out = [];
  const num = (v) => parseFloat(v) || 0;
  const shown = (el) => el.getClientRects().length > 0 &&
      getComputedStyle(el).visibility !== 'hidden';
  const alphaOf = (c) => {
    const m = c.match(/rgba?\(([^)]+)\)/);
    if (!m) return 0;
    const p = m[1].split(',').map((s) => s.trim());
    return p.length > 3 ? parseFloat(p[3]) : 1;
  };
  for (const [index, el] of [...document.querySelectorAll(selector)].entries()) {
    if (!shown(el)) continue;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    const box = {
      l: r.left + num(cs.borderLeftWidth) + num(cs.paddingLeft),
      t: r.top + num(cs.borderTopWidth) + num(cs.paddingTop),
      r: r.right - num(cs.borderRightWidth) - num(cs.paddingRight),
      b: r.bottom - num(cs.borderBottomWidth) - num(cs.paddingBottom),
    };
    const rects = [];
    const add = (x) => {
      const c = { l: Math.max(x.left, box.l), t: Math.max(x.top, box.t),
                  r: Math.min(x.right, box.r), b: Math.min(x.bottom, box.b) };
      if (c.r > c.l && c.b > c.t) rects.push(c);
    };
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      if (!n.nodeValue.trim()) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const g of range.getClientRects()) if (g.width > 0 && g.height > 0) add(g);
    }
    for (const c of el.querySelectorAll('input,select,textarea,button,canvas,svg,img')) {
      if (!shown(c)) continue;
      const g = c.getBoundingClientRect();
      if (g.width > 0 && g.height > 0) add(g);
    }
    for (const c of el.querySelectorAll('*')) {
      if (c.children.length || (c.textContent || '').trim() || !shown(c)) continue;
      const s = getComputedStyle(c);
      if (alphaOf(s.backgroundColor) > 0 || s.backgroundImage !== 'none') {
        const g = c.getBoundingClientRect();
        if (g.width > 0 && g.height > 0) add(g);
      }
    }
    const xs = [...new Set([box.l, box.r, ...rects.flatMap((q) => [q.l, q.r])])].sort((a, b) => a - b);
    const ys = [...new Set([box.t, box.b, ...rects.flatMap((q) => [q.t, q.b])])].sort((a, b) => a - b);
    const cols = xs.length - 1, rows = ys.length - 1;
    const taken = Array.from({ length: rows }, () => new Array(Math.max(cols, 0)).fill(false));
    for (const q of rects) {
      const i0 = xs.indexOf(q.l), i1 = xs.indexOf(q.r), j0 = ys.indexOf(q.t), j1 = ys.indexOf(q.b);
      for (let j = j0; j < j1; j++) for (let i = i0; i < i1; i++) taken[j][i] = true;
    }
    const acc = new Array(Math.max(cols, 0)).fill(0);
    let best = null;
    for (let j = 0; j < rows; j++) {
      const bandH = ys[j + 1] - ys[j];
      for (let i = 0; i < cols; i++) acc[i] = taken[j][i] ? 0 : acc[i] + bandH;
      let i = 0;
      while (i < cols) {
        if (acc[i] < minH) { i++; continue; }
        let k = i, width = 0, height = Infinity;
        while (k < cols && acc[k] >= minH) { width += xs[k + 1] - xs[k]; height = Math.min(height, acc[k]); k++; }
        if (width >= minW && (!best || width * height > best.w * best.h)) {
          best = { x: xs[i], y: ys[j + 1] - height, w: width, h: height };
        }
        i = k;
      }
    }
    out.push({
      selector, index,
      testid: el.getAttribute('data-testid') || '',
      id: el.id || '',
      cls: String(el.className).slice(0, 60),
      content: { w: box.r - box.l, h: box.b - box.t },
      largest_empty: best,
    });
  }
  return out;
}
"""

FIGURE_SPAN_JS = r"""
(selector) => {
  const out = [];
  for (const [index, row] of [...document.querySelectorAll(selector)].entries()) {
    if (row.getClientRects().length === 0) continue;
    const parent = row.parentElement;
    const ps = getComputedStyle(parent);
    const pr = parent.getBoundingClientRect();
    const content = pr.width - parseFloat(ps.paddingLeft || 0) - parseFloat(ps.paddingRight || 0)
        - parseFloat(ps.borderLeftWidth || 0) - parseFloat(ps.borderRightWidth || 0);
    let left = Infinity, right = -Infinity;
    for (const child of row.children) {
      const range = document.createRange();
      range.selectNodeContents(child);
      for (const g of range.getClientRects()) {
        if (g.width > 0 && g.height > 0) { left = Math.min(left, g.left); right = Math.max(right, g.right); }
      }
    }
    out.push({
      index, testid: row.getAttribute('data-testid') || '', cls: String(row.className).slice(0, 60),
      children: row.children.length, content_width: content,
      extent: right > left ? right - left : 0,
      ratio: right > left && content > 0 ? (right - left) / content : 0,
    });
  }
  return out;
}
"""

HEIGHT_FACTS_JS = r"""
(relabel) => {
  const h = (el) => el.getBoundingClientRect().height;
  const sum = (sel) => [...document.querySelectorAll(sel)].reduce((t, el) => t + h(el), 0);
  const labelled = {};
  for (const [name, selectors] of Object.entries(relabel)) {
    labelled[name] = selectors.map((sel) => [...document.querySelectorAll(sel)]
        .filter((el) => el.getClientRects().length > 0).map(h));
  }
  const root = document.documentElement;
  return {
    document_height: Math.max(root.scrollHeight, document.body ? document.body.scrollHeight : 0),
    scroll_width: root.scrollWidth,
    viewport_width: innerWidth,
    explainer_sum: sum('[data-explainer]'),
    terms_summary_sum: sum('[data-terms] > summary'),
    relabelled: labelled,
  };
}
"""

FIGURES_JS = r"""
() => {
  const out = [];
  for (const el of document.querySelectorAll('[data-testid],[id]')) {
    if (el.children.length) continue;
    if (['SCRIPT', 'STYLE', 'OPTION', 'LINK', 'META'].includes(el.tagName)) continue;
    const key = el.id ? 'id:' + el.id : 'testid:' + el.getAttribute('data-testid');
    out.push([key, (el.textContent || '').replace(/\s+/g, ' ').trim()]);
  }
  return out;
}
"""

CONTROLS_JS = r"""
() => {
  const out = [];
  for (const el of document.querySelectorAll('button,a[href],input,select,textarea,summary,[role="button"]')) {
    const cs = getComputedStyle(el);
    out.push({
      tag: el.tagName.toLowerCase(),
      id: el.id || '',
      testid: el.getAttribute('data-testid') || '',
      type: el.getAttribute('type') || '',
      text: (el.textContent || el.value || '').replace(/\s+/g, ' ').trim().slice(0, 60),
      href: el.getAttribute('href') || '',
      disabled: !!el.disabled,
      displayed: el.getClientRects().length > 0 && cs.visibility !== 'hidden',
    });
  }
  return out;
}
"""

# ---------------------------------------------------------------------------
# Worlds
# ---------------------------------------------------------------------------

_PRISM_FIXTURE = REPO / "tests" / "fixtures" / "ai_advisor" / "market_prism_summary_full.json"
_RETIREMENT_ROW = REPO / "tests" / "fixtures" / "retirement" / "run_summary_captured.json"


def _record_cvar_for_first_symphony() -> None:
    """One CVaR diagnostic row through the real writer (synthetic figures, no captured row)."""
    first_id = captured_symphonies()[0][0]
    database.record_cvar_diagnostic(
        "page-explainers-cvar-cycle",
        first_id,
        -0.031,
        0.004,
        24,
        -0.029,
        26,
        mode="live",
    )


def _seed_sleeve() -> None:
    sleeve_id = database.create_sleeve("Explainer sleeve", 1000.0)
    database.create_sleeve_rule(sleeve_id, "Explainer rule")


def _seed_advisor_records() -> None:
    """Autotune runs, a Market Prism row, a retirement run and a stored tenure review."""
    from datetime import datetime, timedelta

    from tests.app import _tenure_review_panel_builders as pb

    first_week = datetime(2026, 9, 1, 4, 0, 0)
    keys = [database.normalize_name(name) for _, name in captured_symphonies()]
    for week in range(2):
        for i, key in enumerate(keys):
            moment = first_week + timedelta(weeks=week, minutes=i)
            database.save_autotune_run(
                moment.strftime("%Y-%m-%dT%H:%M:%S"),
                key,
                0.5,
                1.0,
                "Reverted to Fallback",
                0.4,
                0.3,
                selection_tstat=2.0 + i * 0.01,
                naive_sharpe=1.0 + i * 0.01 + week * 0.1,
            )
    prism = json.loads(_PRISM_FIXTURE.read_text(encoding="utf-8"))
    database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="global",
        verdict=prism["verdict"],
        raw_response=prism["raw_response"],
    )
    captured = json.loads(_RETIREMENT_ROW.read_text(encoding="utf-8"))[0]
    database.insert_advisor_observation(
        advisor_role="RETIREMENT_RUN_SUMMARY",
        subject_type=captured["subject_type"],
        subject_id=captured["subject_id"],
        verdict=captured["verdict"],
        raw_response=json.loads(captured["raw_response"]),
    )
    pb.store_run(pb.full_record())


def fix_clock_and_token(setattr_fn: Callable = setattr) -> None:
    """Fix the app clock, the date windows and the CSRF token every page embeds."""
    import analytics

    setattr_fn(app_module, "datetime", frozen_datetime_class(CAPTURE_INSTANT))
    setattr_fn(analytics, "_window_cutoff_date", _pinned_window_cutoff(WINDOW_ANCHOR))
    setattr_fn(app_module, "_CSRF_TOKEN", FIXED_CSRF_TOKEN)


def seed_world(name: str, tmp_path: pathlib.Path, setattr_fn: Callable = setattr) -> None:
    """Fix the clock and write the state of world ``name`` through the real writers.

    ``setattr_fn`` is ``monkeypatch.setattr`` in a test (undone at teardown) or ``setattr`` in the
    reference capture. The Strategy Builder survivor and backtest-failure rows and the pending
    proposals of AC-8's five-card case are written by the cycle that reads them.
    """
    if name not in WORLD_HOSTS:
        raise KeyError(f"unknown world {name!r}; known: {sorted(WORLD_HOSTS)}")
    import analytics

    fix_clock_and_token(setattr_fn)
    # History and Performance read post-mortem days; a world holds only the days it seeds, never
    # whatever a checkout keeps beside the code.
    no_days = tmp_path / "no_post_mortems"
    no_days.mkdir(exist_ok=True)
    setattr_fn(analytics, "_POST_MORTEMS_DIR", str(no_days))
    if name == "empty":
        return
    seed_dashboard_state()
    _record_cvar_for_first_symphony()
    if name == "dashboard_sleeves":
        _seed_sleeve()
    if name == "full":
        _seed_sleeve()
        seed_history_exits(tmp_path, setattr_fn)
        seed_pending_frontrunner_proposal()
        seed_candidate_alert_survivor()
        _seed_advisor_records()


# ---------------------------------------------------------------------------
# Server-side readings (the Flask test client; no browser)
# ---------------------------------------------------------------------------


def _soup(html: str):
    from bs4 import BeautifulSoup

    return BeautifulSoup(html, "html.parser")


def section_headings(html: str) -> list[str]:
    """Every ``.section-header`` text in document order (the sections' order, S-5.5)."""
    return [" ".join(h.get_text().split()) for h in _soup(html).select(".section-header")]


def banner_markup(html: str, selector: str) -> list[str]:
    """The markup of every element matching ``selector``, in document order."""
    return [str(el) for el in _soup(html).select(selector)]


def element_text(html: str, selector: str, *, containing: str) -> str | None:
    """The whitespace-collapsed text of the first ``selector`` match that contains ``containing``."""
    for el in _soup(html).select(selector):
        collapsed = " ".join(el.get_text().split())
        if containing in collapsed:
            return collapsed
    return None


# Where a kept row's text sits at the reference (Copy table A rows kept as they are whose ref
# text the plan gives as a pointer): key -> (host, selector, a phrase the element contains).
KEPT_ROW_LOCATORS: dict[str, tuple[str, str, str]] = {
    "F13": ("/performance", ".metrics-caption", "Positive deltas on risk metrics"),
    "S14": ("/settings", ".sec-subtitle", "Override globals"),
    "S17": ("/settings", ".sec-subtitle", "Destructive data operations"),
    "B03": ("/ai-advisor", "p.run-controls-note", "generates candidate strategies"),
    "X03": ("/ai-advisor", "p.run-controls-note", "Runs over every live symphony"),
}


def read_kept_rows(client) -> dict[str, str]:
    """The rendered text of every kept row the plan points at (read at the reference)."""
    import os
    from unittest import mock

    pages: dict[str, str] = {}
    out: dict[str, str] = {}
    # The two Advisor notes render only when the Anthropic and the Composer keys are configured;
    # the placeholder is not a credential and no request leaves the process.
    import alpha_bot_execution

    with (
        mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": PLACEHOLDER_KEY}),
        mock.patch.object(alpha_bot_execution, "COMPOSER_KEY_ID", PLACEHOLDER_KEY),
        mock.patch.object(alpha_bot_execution, "COMPOSER_SECRET", PLACEHOLDER_KEY),
    ):
        for key, (host, selector, phrase) in KEPT_ROW_LOCATORS.items():
            if host not in pages:
                pages[host] = client.get(host).get_data(as_text=True)
    for key, (host, selector, phrase) in KEPT_ROW_LOCATORS.items():
        found = element_text(pages[host], selector, containing=phrase)
        if found is None:
            raise AssertionError(f"kept row {key}: no {selector} on {host} contains {phrase!r}")
        out[key] = found
    return out


# A stored row carries the wall-clock time its writer stamped (the CVaR diagnostic's ``ts_utc``);
# it differs on every run, so it is masked wherever it appears in a body.
WALL_CLOCK_KEYS = ("ts_utc",)
WALL_CLOCK_MASK = "<wall clock>"


def mask_wall_clock(value):
    if isinstance(value, dict):
        return {
            k: (
                WALL_CLOCK_MASK
                if k in WALL_CLOCK_KEYS and isinstance(v, str)
                else mask_wall_clock(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [mask_wall_clock(v) for v in value]
    return value


def _json_body(response):
    return mask_wall_clock(json.loads(response.get_data(as_text=True)))


def read_route_bodies(client) -> dict:
    """Each route's body, read twice; a top-level key that differs between the two reads is volatile.

    ``{route: {"status": int, "body": <json without the volatile keys>, "volatile": [keys]}}``.
    A route that does not answer JSON records the raw text.
    """
    out: dict = {}
    for path in ROUTE_PATHS:
        first, second = client.get(path), client.get(path)
        record = {"status": first.status_code, "volatile": []}
        try:
            one, two = _json_body(first), _json_body(second)
        except ValueError:
            record["body"] = first.get_data(as_text=True)
            out[path] = record
            continue
        if isinstance(one, dict) and isinstance(two, dict):
            # A key that changes between two reads, plus the keys that describe the process (its
            # start time is constant inside one process and differs between two).
            volatile = sorted(
                {k for k in set(one) | set(two) if one.get(k) != two.get(k)} | set(PROCESS_KEYS)
            )
            record["volatile"] = volatile
            record["body"] = {k: v for k, v in one.items() if k not in volatile}
        else:
            record["body"] = one
        out[path] = record
    return out


def strip_volatile(body, volatile: list[str]):
    if isinstance(body, dict):
        return {k: v for k, v in body.items() if k not in volatile}
    return body


def read_auth_pages(client) -> dict[str, str]:
    """The login page and the logout confirm page (S-5.9), as served."""
    return {
        "/login": client.get("/login").get_data(as_text=True),
        "/logout": client.get("/logout").get_data(as_text=True),
    }


_CAPTURED_POST_MORTEM = (
    REPO / "tests" / "fixtures" / "prod_droplet" / "post_mortems" / "post_mortem_2026-06-30.json"
)


def read_discord_payload(tmp_path: pathlib.Path, setattr_fn: Callable = setattr) -> list:
    """Every Discord request ``reporting.send_eod_discord_post`` makes over the captured day.

    The network is the boundary: ``requests.post`` is replaced and records each call's JSON
    payload (a multipart ``payload_json`` is decoded); the sleep between posts is skipped.
    """
    import reporting

    directory = tmp_path / "discord_post_mortems"
    directory.mkdir()
    day = "2026-06-30"
    report = directory / f"post_mortem_{day}.json"
    report.write_text(_CAPTURED_POST_MORTEM.read_text(encoding="utf-8"), encoding="utf-8")
    captured: list = []

    def _post(url, *args, **kwargs):
        payload = kwargs.get("json")
        data = kwargs.get("data")
        if payload is None and isinstance(data, dict) and "payload_json" in data:
            payload = json.loads(data["payload_json"])
        captured.append(payload)

        class _Response:
            status_code = 200

            def json(self):
                return {}

        return _Response()

    setattr_fn(reporting, "_POST_MORTEMS_DIR", str(directory))
    setattr_fn(reporting.requests, "post", _post)
    setattr_fn(reporting.time, "sleep", lambda *a, **k: None)
    reporting.send_eod_discord_post(
        day, str(report), {}, "https://discord.invalid/webhook/page-explainers"
    )
    return captured


def _lf_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()


def working_tree_hashes() -> dict[str, str]:
    """SHA-256 of each pinned file's bytes with CRLF normalized to LF (S-5.13)."""
    return {
        name: _lf_sha256((REPO / name).read_bytes())
        for name in ("alpha_bot_execution.py", "math_engine.py", "database.py")
    }


def git_object_hash(commit: str, path: str) -> str:
    """The same hash over a file as git holds it at ``commit`` (used by the capture only)."""
    raw = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{commit}:{path}"], capture_output=True, check=True
    ).stdout
    return _lf_sha256(raw)


def migration_listing(root: pathlib.Path = REPO) -> dict[str, str]:
    """``{file name: LF sha256}`` of every file in ``migrations/``."""
    folder = root / "migrations"
    return {p.name: _lf_sha256(p.read_bytes()) for p in sorted(folder.iterdir()) if p.is_file()}


def migration_listing_at(commit: str) -> dict[str, str]:
    names = subprocess.run(
        ["git", "-C", str(REPO), "ls-tree", "--name-only", commit, "migrations/"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    return {n.split("/", 1)[1]: git_object_hash(commit, n) for n in sorted(names)}


def route_table_facts() -> dict:
    """The route table and hooks as ``tests/_hygiene_surface_facts.py`` reads them (S-5.7)."""
    from tests._hygiene_surface_facts import url_map_facts

    return url_map_facts(app_module)


def scheduler_slot_digest() -> str:
    """SHA-256 of the AST dump of ``run_scheduler`` (no line numbers): its slots and their calls."""
    tree = ast.parse((REPO / "app.py").read_text(encoding="utf-8"))
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run_scheduler"
    )
    return hashlib.sha256(ast.dump(node).encode("utf-8")).hexdigest()


_BUTTON = re.compile(r"<button\b(?P<attrs>[^>]*)>(?P<body>.*?)</button>", re.S | re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*"([^"]*)"')


def handlerless_buttons() -> list[list[str]]:
    """Template buttons no script, ``onclick`` or form can act on, as ``[file, id or testid, text]``.

    A button counts as handled when it has an ``onclick``, sits inside a ``<form``, or its id or
    ``data-testid`` appears quoted or as ``#id`` in some ``static/*.js`` file or in an inline
    ``<script>`` of its own template (its own markup does not count). Jinja inside an attribute is left as written.
    """
    scripts = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in sorted((REPO / "static").glob("*.js"))
    )
    found: list[list[str]] = []
    for path in sorted((REPO / "templates").glob("*.html")):
        source = path.read_text(encoding="utf-8", errors="replace")
        inline = " ".join(re.findall(r"<script[^>]*>(.*?)</script>", source, re.S | re.I))
        haystack = scripts + " " + inline
        for match in _BUTTON.finditer(source):
            attrs = dict(_ATTR.findall(match["attrs"]))
            text = " ".join(
                re.sub(r"<[^>]+>|\{[%#].*?[%#]\}|\{\{.*?\}\}", " ", match["body"]).split()
            )
            ident = attrs.get("id") or attrs.get("data-testid") or ""
            before = source[: match.start()]
            in_form = before.rfind("<form") > before.rfind("</form>")
            handled = "onclick" in attrs or in_form
            for token in filter(None, (attrs.get("id"), attrs.get("data-testid"))):
                if "{" in token or re.search(rf"['\"#]{re.escape(token)}['\"\s\])]", haystack):
                    handled = True
            if not handled:
                found.append([path.name, ident, text[:40]])
    return found


def read_server_side(client, tmp_path: pathlib.Path, setattr_fn: Callable = setattr) -> dict:
    """Everything the Flask-client checks compare, for the world already seeded.

    ``{"headings": {host: [...]}, "banners": {key: [...]}, "routes": {...}, "auth_pages": {...}}``.
    """
    pages = {host: client.get(host).get_data(as_text=True) for host in PAGES}
    return {
        "headings": {host: section_headings(html) for host, html in pages.items()},
        "banners": {
            key: banner_markup(pages[host], selector)
            for key, (host, selector) in BANNER_SELECTORS.items()
        },
        "routes": read_route_bodies(client),
        "auth_pages": read_auth_pages(client),
    }


# ---------------------------------------------------------------------------
# Browser readings
# ---------------------------------------------------------------------------


# Texts that follow the wall clock and not the state: a stored row's write time ("2026-11-06 13:34
# ET"), the header's countdown to the next minute ("Next 00:58"). They are masked in both the
# capture and the checks so two readings of one state agree.
_WALL_CLOCK_TEXT = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?Z?(?: ET| UTC)?|Next \d{2}:\d{2}"
)
WALL_CLOCK_TEXT_MASK = "<clock>"


def mask_clock_text(text: str) -> str:
    return _WALL_CLOCK_TEXT.sub(WALL_CLOCK_TEXT_MASK, text)


def _masked_reading(record: dict) -> dict:
    """The figure and control texts of a browser reading with the clock texts masked."""
    if "figures_1440" in record:
        record["figures_1440"] = [[k, mask_clock_text(t)] for k, t in record["figures_1440"]]
    for width in ("1440", "375"):
        for control in record.get(f"controls_{width}", []):
            control["text"] = mask_clock_text(control["text"])
    return record


def read_host_in_browser(firefox, server, host: str) -> dict:
    """One host in the current world: AC-7 heights at 375, figure and control inventories at 1440.

    ``{"heights_375": ..., "controls_375": [...], "figures_1440": [[key, text]...],
    "controls_1440": [...]}``. The browser clock is fixed at ``CAPTURE_INSTANT``.
    """
    record: dict = {}
    for tag, (width, height) in (("375", NARROW), ("1440", WIDE)):
        with open_probe(firefox, server, width=width, height=height) as probe:
            probe.context.clock.install(time=CAPTURE_INSTANT)
            open_page(probe, host)
            probe.page.wait_for_timeout(SETTLE_MS)
            if tag == "375":
                record["heights_375"] = probe.page.evaluate(
                    HEIGHT_FACTS_JS, {host: list(RELABEL_SELECTORS[host])}
                )
            else:
                record["figures_1440"] = probe.page.evaluate(FIGURES_JS)
            record[f"controls_{tag}"] = probe.page.evaluate(CONTROLS_JS)
            record[f"page_errors_{tag}"] = list(probe.page_errors)
    return _masked_reading(record)


def credential_free_environment(env_file: pathlib.Path, setattr_fn: Callable, delenv: Callable):
    """The state both the capture and the checks run in: no credential, the developer ``.env`` unread.

    ``delenv(name)`` removes an environment variable (``monkeypatch.delenv`` with ``raising=False``
    in a test, ``os.environ.pop`` in the capture).
    """
    import model_config
    from tests.ui._advisor_browser_support import (
        _CREDENTIAL_VARIABLES,
        restore_credential_free_module_state,
    )

    for name in (*_CREDENTIAL_VARIABLES, *MODEL_OVERRIDE_VARIABLES):
        delenv(name)
    # The suite's autouse seed resolves both model families from a captured listing; a daemon
    # that has listed nothing (no key) resolves neither, and the pages are read in that state.
    model_config._reset_registry()
    restore_credential_free_module_state(setattr_fn)
    env_file.write_text("", encoding="utf-8")
    setattr_fn(app_module, "ENV_FILE_PATH", str(env_file))
    # ``/api/state`` also reads ``.env`` by its relative name, which is the developer's file in a
    # checkout that has one; point that read at the empty file too.
    real_dotenv_values = app_module.dotenv_values
    setattr_fn(
        app_module,
        "dotenv_values",
        lambda path=None, *a, **k: real_dotenv_values(
            str(env_file) if path == ".env" else path, *a, **k
        ),
    )
