"""Advisor page polish, judged in a real browser at 1440 and 375.

Browser-only (marker ``browser``, deselected by default; run with
``python -m pytest -m browser -n 0 tests/ui/test_advisor_polish_browser.py``). Firefox headless
through Playwright; a missing Playwright or Firefox fails with a message naming it, never a skip.

* The Strategy Builder's results area takes no space while it is empty and renders as it always
  did once the run has written into it (the script that fills it is not touched here).
* The suggestion cards appear only after a Run. The run's answer is a STUB the browser itself
  fulfils (``page.route``), so the server never sees the POST and no model is called; the cards
  are drawn by the real ``static/ai_advisor.js``. The stub is a labelled construction: the keys
  ``/ai-advisor/suggest`` returns, with made-up values.
* The Chat panel's unavailable state (no ``ANTHROPIC_API_KEY``) keeps warn out of its title text.

Not asserted here: the text contrast of an OOS-rejected card. The design pins ``opacity: 0.7`` on
that card (``test_cycle_4_advisor.py``), which takes its text below 4.5:1; that is a design
decision recorded in the judge-fix report, not a thing this file can settle.
"""

from __future__ import annotations

import json

import pytest

from tests.ui._advisor_browser_support import (  # noqa: F401
    _stub_database_load_state_for_advisor_routes,
    _stub_get_api_state_dict,
    advisor_probe,
    advisor_server,
    fail,
    firefox,
    open_advisor,
    wait_until,
)

pytestmark = pytest.mark.browser

_STUB_ANSWER = {
    "suggestions": [
        {
            "config_key": "TRAILING_STOP_PCT",
            "current_value": 4.0,
            "suggested_value": 3.5,
            "risk_direction": "tighter",
            "confidence": "high",
            "data_sufficiency": "limited",
            "rationale": "Labelled construction: a tighter stop would have exited earlier.",
            "impact": {"metric": "sharpe", "before": 0.812, "after": 0.907, "delta": 0.095},
            "oos_status": "passed",
            "oos_reason": "held-out tail slice improved",
            "four_gates_verdict": {
                "allowlist": True,
                "risk_direction": "pass",
                "oos_frozen_eval": True,
                "locked_vars": "unknown",
            },
        },
        {
            "config_key": "TAKE_PROFIT_MC_PCT",
            "current_value": 30,
            "suggested_value": 35,
            "risk_direction": "neutral",
            "confidence": "low",
            "data_sufficiency": "insufficient",
            "rationale": "Labelled construction.",
            "impact": {"metric": "sharpe", "before": 0.5, "after": 0.51, "delta": 0.01},
            "oos_status": "pending",
            "four_gates_verdict": {"allowlist": True},
        },
    ],
    "lens_data_as_of": "2026-10-31T07:07:00Z",
    "lens_data_stale": False,
}

# Every text element of the suggestion cards: its size, case, weight and contrast against the
# background it sits on (an ancestor's opacity folds into both colours).
_CARD_TEXT_JS = """() => {
  const parse = (c) => {
    let m = c.match(/rgba?\\(([^)]+)\\)/);
    if (m) { const p = m[1].split(/[ ,\\/]+/).filter(Boolean).map(Number);
             return [p[0], p[1], p[2], p.length > 3 ? p[3] : 1]; }
    m = c.match(/color\\(srgb ([^)]+)\\)/);
    if (m) { const p = m[1].split(/[ \\/]+/).filter(Boolean).map(Number);
             return [p[0]*255, p[1]*255, p[2]*255, p.length > 3 ? p[3] : 1]; }
    return [0, 0, 0, 0];
  };
  const lin = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * lin(c[0]) + 0.7152 * lin(c[1]) + 0.0722 * lin(c[2]);
  const over = (fg, bg) => { const a = fg[3];
    return [fg[0]*a + bg[0]*(1-a), fg[1]*a + bg[1]*(1-a), fg[2]*a + bg[2]*(1-a), 1]; };
  const pb = parse(getComputedStyle(document.body).backgroundColor);
  const page = pb[3] === 0 ? [255, 255, 255, 1] : pb;
  const effBg = (el) => {
    const chain = []; for (let e = el; e; e = e.parentElement) chain.push(e);
    let bg = page;
    for (let i = chain.length - 1; i >= 0; i--) {
      const cs = getComputedStyle(chain[i]); const b = parse(cs.backgroundColor);
      if (b[3] > 0) bg = over(b, bg);
    }
    return bg;
  };
  const out = [];
  document.querySelectorAll('#suggestions-container [id^=card-]').forEach((card) => {
    const walker = document.createTreeWalker(card, NodeFilter.SHOW_TEXT);
    const seen = new Set();
    while (walker.nextNode()) {
      const t = walker.currentNode; if (!t.textContent.trim()) continue;
      const el = t.parentElement; if (seen.has(el)) continue; seen.add(el);
      const cs = getComputedStyle(el);
      const bg = effBg(el); const fg = over(parse(cs.color), bg);
      const hi = Math.max(lum(fg), lum(bg)), lo = Math.min(lum(fg), lum(bg));
      out.push({ card: card.id, text: t.textContent.trim().slice(0, 60),
                 px: parseFloat(cs.fontSize), transform: cs.textTransform,
                 ratio: Math.round((hi + 0.05) / (lo + 0.05) * 100) / 100 });
    }
  });
  return out;
}"""

_DOT_JS = """(sel) => {
  const el = document.querySelector(sel);
  const probe = document.createElement('span');
  const resolve = (v) => { probe.style.color = v; document.body.appendChild(probe);
                           const c = getComputedStyle(probe).color; probe.remove(); return c; };
  return {
    text: el.textContent.trim(), px: parseFloat(getComputedStyle(el).fontSize),
    colour: getComputedStyle(el).color,
    ink: resolve('var(--studio-ink)'), warn: resolve('var(--studio-warn)'),
    dot: getComputedStyle(el, '::before').backgroundColor,
  };
}"""


def _run_stubbed(probe, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "polish-test-placeholder")
    probe.page.route(
        "**/ai-advisor/suggest",
        lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps(_STUB_ANSWER)
        ),
    )
    open_advisor(probe)
    probe.page.evaluate(
        """() => {
          const sel = document.getElementById('symphony-id-input');
          const o = document.createElement('option'); o.value = 'stub'; o.textContent = 'stub';
          sel.appendChild(o); sel.value = 'stub';
          document.getElementById('get-suggestions-btn').disabled = false;
          window.getSuggestions();
        }"""
    )
    wait_until(
        probe,
        "() => document.querySelectorAll('#suggestions-container [id^=card-]').length === 2",
        "the stubbed answer never drew its two suggestion cards",
    )


def _theme(probe, theme: str):
    probe.context.add_init_script(
        "localStorage.setItem('studioTweaks', JSON.stringify({theme: '%s'}))" % theme
    )


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("size", [(1440, 900), (375, 812)], ids=["1440", "375"])
def test_suggestion_cards_hold_no_text_below_11px_and_clear_4_5_to_1(
    advisor_probe, monkeypatch, theme, size
):
    _theme(advisor_probe, theme)
    advisor_probe.page.set_viewport_size({"width": size[0], "height": size[1]})
    _run_stubbed(advisor_probe, monkeypatch)
    texts = advisor_probe.page.evaluate(_CARD_TEXT_JS)
    assert len(texts) > 20, "premise: the cards drew their text"
    small = [t for t in texts if t["px"] < 11]
    assert small == [], f"text under 11px: {small}"
    upper = [t for t in texts if t["transform"] != "none"]
    assert upper == [], f"a label is transformed: {upper}"
    low = [t for t in texts if t["ratio"] < 4.5]
    assert low == [], f"text under 4.5:1 ({theme}, {size}): {low}"


def test_the_data_sufficiency_note_is_a_warn_dot_and_a_word_in_primary_ink(
    advisor_probe, monkeypatch
):
    _run_stubbed(advisor_probe, monkeypatch)
    note = advisor_probe.page.evaluate(_DOT_JS, "[data-testid='data-sufficiency-badge']")
    assert note["text"] == "limited data", note
    assert note["px"] >= 11, note
    assert note["colour"] == note["ink"] != note["warn"], "the word is not warn-coloured"
    assert note["dot"] == note["warn"], "the dot carries the warn meaning"


def test_the_confidence_label_is_sentence_case_at_11px_or_more(advisor_probe, monkeypatch):
    _run_stubbed(advisor_probe, monkeypatch)
    labels = advisor_probe.page.evaluate(
        """() => Array.from(document.querySelectorAll('#suggestions-container span'))
          .filter((s) => /confidence$/.test(s.textContent.trim()))
          .map((s) => ({ text: s.textContent.trim(),
                         shown: s.innerText.trim(),
                         px: parseFloat(getComputedStyle(s).fontSize),
                         transform: getComputedStyle(s).textTransform }))"""
    )
    assert [label["text"] for label in labels] == ["high confidence", "low confidence"]
    for label in labels:
        assert label["shown"] == label["text"] and label["transform"] == "none", label
        assert label["px"] >= 11, label


def test_the_impact_figures_line_is_not_smaller_than_the_rest_of_the_card(
    advisor_probe, monkeypatch
):
    _run_stubbed(advisor_probe, monkeypatch)
    sizes = advisor_probe.page.evaluate(
        """() => Array.from(document.querySelectorAll(
            '#suggestions-container [data-testid="projected-impact-bar"] span'))
          .map((s) => parseFloat(getComputedStyle(s).fontSize))"""
    )
    assert sizes and min(sizes) >= 11, sizes


_OVERLAP_JS = """() => {
  const hits = [];
  document.querySelectorAll('#suggestions-container [id^=card-]').forEach((card) => {
    const buttons = Array.from(card.querySelectorAll('button')).map((b) => b.getBoundingClientRect());
    card.querySelectorAll('span, p, code').forEach((t) => {
      if (t.closest('button') || !t.textContent.trim()) return;
      const a = t.getBoundingClientRect();
      if (a.width === 0 || a.height === 0) return;
      buttons.forEach((b) => {
        const apart = a.right <= b.left + 1 || a.left >= b.right - 1 || a.bottom <= b.top + 1 || a.top >= b.bottom - 1;
        if (!apart) hits.push({ card: card.id, text: t.textContent.trim().slice(0, 30) });
      });
    });
    const box = card.getBoundingClientRect();
    card.querySelectorAll('*').forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width && (r.right > box.right + 1 || r.left < box.left - 1)) hits.push({ card: card.id, spill: el.tagName });
    });
  });
  return hits;
}"""


@pytest.mark.parametrize("size", [(1440, 900), (375, 812), (320, 700)], ids=["1440", "375", "320"])
def test_no_suggestion_card_text_sits_under_its_buttons_or_spills_out(
    advisor_probe, monkeypatch, size
):
    advisor_probe.page.set_viewport_size({"width": size[0], "height": size[1]})
    _run_stubbed(advisor_probe, monkeypatch)
    assert advisor_probe.page.evaluate(_OVERLAP_JS) == []


# ---------------------------------------------------------------------------
# Strategy Builder: no dead band while the results area is empty
# ---------------------------------------------------------------------------


def _show_the_builder(probe, monkeypatch):
    # The run area shows only with Composer credentials; the module reads them once at import.
    monkeypatch.setattr("alpha_bot_execution.COMPOSER_KEY_ID", "polish-test-placeholder")
    monkeypatch.setattr("alpha_bot_execution.COMPOSER_SECRET", "polish-test-placeholder")
    open_advisor(probe)
    probe.page.locator('button[data-tab="strategy-builder"]').click()
    wait_until(
        probe,
        "() => document.getElementById('sb-run-results') !== null",
        "the Strategy Builder tab has no results area",
    )


_BOX_JS = """() => {
  const el = document.getElementById('sb-run-results');
  const cs = getComputedStyle(el);
  const b = el.getBoundingClientRect();
  return { height: b.height, display: cs.display, marginTop: cs.marginTop, minHeight: cs.minHeight,
           children: el.childElementCount };
}"""


def test_an_empty_results_area_takes_no_space(advisor_probe, monkeypatch):
    _show_the_builder(advisor_probe, monkeypatch)
    empty = advisor_probe.page.evaluate(_BOX_JS)
    assert empty["children"] == 0, empty
    assert empty["height"] == 0 and empty["display"] == "none", empty


def test_a_filled_results_area_renders_as_it_always_did(advisor_probe, monkeypatch):
    _show_the_builder(advisor_probe, monkeypatch)
    advisor_probe.page.evaluate(
        """() => { document.getElementById('sb-run-results').innerHTML =
                   '<div class="run-controls-note" data-testid="sb-live-summary">Evaluated 3</div>'; }"""
    )
    filled = advisor_probe.page.evaluate(_BOX_JS)
    assert filled["display"] != "none" and filled["height"] >= 64, filled
    assert filled["marginTop"] == "16px" and filled["minHeight"] == "64px", filled
    # and an emptied area (a refused or failed run) collapses again
    advisor_probe.page.evaluate(
        "() => { document.getElementById('sb-run-results').innerHTML = ''; }"
    )
    assert advisor_probe.page.evaluate(_BOX_JS)["height"] == 0


# ---------------------------------------------------------------------------
# Chat: the unavailable title keeps warn out of its text
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_chat_unavailable_title_is_primary_ink_with_a_warn_dot(advisor_probe, theme):
    # advisor_server removes ANTHROPIC_API_KEY, so the page renders the unavailable state.
    _theme(advisor_probe, theme)
    open_advisor(advisor_probe)
    advisor_probe.page.locator('button[data-tab="chat"]').click()
    title = advisor_probe.page.evaluate(_DOT_JS, ".chat-unavailable-title")
    if not title["text"]:
        fail(advisor_probe, "the chat unavailable title never rendered")
    assert title["text"] == "Chat unavailable", title
    assert title["colour"] == title["ink"] != title["warn"], title
    assert title["dot"] == title["warn"], title


@pytest.mark.parametrize(
    ("decision", "dot"),
    [("passed", "--studio-pos"), ("withheld", "--studio-warn"), ("rejected", "--studio-neg")],
)
def test_the_chat_artifact_gate_pill_is_a_dot_and_a_word_never_coloured_text(
    advisor_probe, decision, dot
):
    open_advisor(advisor_probe)
    advisor_probe.page.evaluate(
        """(d) => window.openChatPanel({ artifactId: 'x', artifactType: 'config_suggestion',
          title: 'Explain', contextLabel: 'Config Suggestion', objective: 'o',
          gateDecision: d, keyStat: '', artifactContext: {} })""",
        decision,
    )
    pill = advisor_probe.page.evaluate(
        """(token) => {
          const el = document.getElementById('artifact-gate-pill');
          const probe = document.createElement('span');
          const resolve = (v) => { probe.style.color = v; document.body.appendChild(probe);
                                   const c = getComputedStyle(probe).color; probe.remove(); return c; };
          const cs = getComputedStyle(el);
          return { text: el.textContent.trim(), colour: cs.color, ink: resolve('var(--studio-ink)'),
                   dot: getComputedStyle(el, '::before').backgroundColor, want: resolve('var(' + token + ')'),
                   background: cs.backgroundColor, border: cs.borderTopWidth };
        }""",
        dot,
    )
    assert pill["text"], pill
    assert pill["colour"] == pill["ink"], "the word stays in primary ink"
    assert pill["dot"] == pill["want"], "the decision sets the dot"
    assert pill["background"] == "rgba(0, 0, 0, 0)" and pill["border"] == "0px", "no tinted pill"
