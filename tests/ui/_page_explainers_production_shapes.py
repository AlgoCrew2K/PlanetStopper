"""Production-shaped seeds and readings for the page-explainers fix round.

TEST-ONLY. The slice's seeded worlds pass layouts that fail on the data the product really holds
(a review that read a copy of the production database found six such layouts). This module builds
the states those layouts need, through the real writers, and says for each one what it is derived
from. No production database copy and no account value is read or committed.

Provenance of each seed:

* ``seed_same_batch_autotune_runs``: rows through ``database.save_autotune_run`` with the values of
  the captured ``autotune_run_reverted_to_fallback.json`` fixture. The shape (every symphony's
  latest run stamped by one weekly batch, so the run-time cell is identical in every row, and a
  streak of 4 or 5 runs) is the shape the production capture showed; the stamps are synthetic.
* the tenure review of the production shape is ``undecided_record`` of
  ``tests/app/_tenure_review_panel_builders.py``: the real review compute over constructed series,
  so the undecided peers are joined as the producer joins them (this module held a hand-edited
  record that joined them with a semicolon, which the producer never writes; it is gone).
* ``seed_long_prism_rationale``: the captured Market Prism row of
  ``tests/fixtures/ai_advisor/market_prism_summary_full.json`` with ``sentiment_rationale``
  replaced by a synthetic paragraph of the length a nightly council writes (about 1,000
  characters; the captured fixture holds 67).
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta

import database
from advisors import tenure_review
from tests._ux_truth_support import captured_symphonies
from tests.app import _tenure_review_panel_builders as pb
from tests.ui import _page_explainers_advisor as adv

# The production capture measured these eight tabs needing 909 px of strip; the seeded render
# needs 872 px in the same browser. Font metrics differ between machines, so the strip must not
# depend on an exact text width: this widens every tab's text by the measured ratio (4.2 percent).
WIDER_TAB_TEXT_CSS = ".cap-tab { letter-spacing: 0.03em; }"

# The weekly batch every symphony's latest run belongs to (synthetic stamp, shared by all rows).
LATEST_BATCH = datetime(2026, 11, 6, 19, 8, 0)

# A council-length rationale (synthetic text, about 1,000 characters).
LONG_RATIONALE = (
    "Five lenses describe one regime from different vantage points: a rate-driven unwind of a "
    "crowded trade and a defensive rotation inside an intact longer-term uptrend, a correction "
    "or rotation and not a confirmed bear break. The risk-off timing lenses, breadth near the "
    "bottom of its range and a volatility curve in backwardation, dominate the overnight "
    "direction once the macro and fundamentals lenses concede that their constructive reads are "
    "multi-week floors that do not operate overnight. Flat sentiment tone, with no fear spike, "
    "reads as an orderly rotation and not a panic. The index holding its long average, "
    "earnings that are not deteriorating and a soft-landing macro backdrop cap the downside and "
    "keep the net read neutral with a defensive tilt. Elevated near-term downside risk is "
    "concentrated in the largest growth names and long-duration exposure; the all-clear signal "
    "is the volatility term structure re-steepening toward contango."
)


def seed_same_batch_autotune_runs(weeks: int = 4) -> list[str]:
    """Every captured symphony's runs stamped by one weekly batch; the keys are returned.

    The latest batch gives every row the same run time, the same outcome and the same N/A
    cells, so the table lifts four columns into its shared line; the first two symphonies also
    hold one run more, so the streak column reads 5 there and ``weeks`` elsewhere.
    """
    keys = [database.normalize_name(name) for _, name in captured_symphonies()]
    for index, key in enumerate(keys):
        runs = weeks + (1 if index < 2 else 0)
        for week in range(runs):
            stamp = LATEST_BATCH - timedelta(weeks=runs - 1 - week)
            adv.save_run_like("reverted", key, stamp.strftime("%Y-%m-%dT%H:%M:%S"))
    return keys


def seed_long_prism_rationale() -> None:
    """A later Market Prism row, the captured one with a council-length rationale."""
    source = adv._fixture("market_prism_summary_full.json")
    raw = copy.deepcopy(source["raw_response"])
    raw["sentiment_rationale"] = LONG_RATIONALE
    database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="global",
        verdict=source["verdict"],
        raw_response=raw,
    )


# ---------------------------------------------------------------------------
# In-page readings
# ---------------------------------------------------------------------------

# Rectangles are [left, top, width, height] in CSS pixels.
RECT_JS = """(el) => { if (!el) return null; const b = el.getBoundingClientRect();
  return { left: b.left, top: b.top, right: b.right, bottom: b.bottom, width: b.width, height: b.height }; }"""

HERO_JS = (
    """() => {
  const rect = """
    + RECT_JS
    + """;
  const q = (s) => document.querySelector(s);
  const d = q('[data-testid="guard-alpha-descriptor"]');
  // The headline as a poll writes it once a real figure is known.
  q('#guard-alpha-headline').textContent = '-0.73%';
  const lineHeight = parseFloat(getComputedStyle(d).lineHeight);
  const top = q('.hero-top');
  const tr = top.getBoundingClientRect();
  const ts = getComputedStyle(top);
  return {
    headline: rect(q('#guard-alpha-headline')), descriptor: rect(d),
    lines: Math.round(d.getBoundingClientRect().height / lineHeight),
    heroTopContentWidth: tr.width - parseFloat(ts.paddingLeft) - parseFloat(ts.paddingRight),
  };
}"""
)

DETAIL_LEAD_JS = (
    """() => {
  const rect = """
    + RECT_JS
    + """;
  const panel = document.querySelector('[data-testid="detail-panel"]');
  const lead = panel.querySelector('[data-explainer="dp.lead"]');
  const disclosure = panel.querySelector('details[data-terms="dp.lead"]');
  const section = panel.querySelector('.detail-section');
  const ss = getComputedStyle(section);
  return {
    lead: rect(lead), disclosure: rect(disclosure),
    sectionContentLeft: section.getBoundingClientRect().left + parseFloat(ss.paddingLeft),
    panel: rect(panel),
  };
}"""
)

TABS_JS = """() => {
  const strip = document.querySelector('[role="tablist"]');
  const sr = strip.getBoundingClientRect();
  const shown = (el) => !!el && el.getClientRects().length > 0 && getComputedStyle(el).display !== 'none'
      && getComputedStyle(el).visibility !== 'hidden' && parseFloat(getComputedStyle(el).opacity) > 0;
  const cues = ['start', 'end'].map((side) => {
    const c = document.querySelector('[data-testid="cap-nav-cue-' + side + '"]');
    const r = c ? c.getBoundingClientRect() : null;
    return { side, shown: shown(c), left: r && r.left, right: r && r.right };
  });
  return {
    strip: { left: sr.left, right: sr.right, scrollWidth: strip.scrollWidth, clientWidth: strip.clientWidth },
    tabs: [...strip.querySelectorAll('[role="tab"]')].map((t) => {
      const r = t.getBoundingClientRect();
      return { name: t.getAttribute('data-tab'), left: r.left, right: r.right };
    }),
    cues,
    pageScroll: document.documentElement.scrollWidth, pageClient: document.documentElement.clientWidth,
  };
}"""

PRISM_JS = (
    """() => {
  const rect = """
    + RECT_JS
    + """;
  const header = document.querySelector('[data-testid="market-prism-block"] .prism-block-header');
  const description = document.querySelector('[data-explainer="O15"]');
  const rationale = document.querySelector('[data-testid="prism-rationale"]');
  return { header: rect(header), description: rect(description), rationale: rect(rationale) };
}"""
)

TENURE_ENTRIES_JS = """() => [...document.querySelectorAll('[data-testid="tenure-review-entry"]')].map((entry) => {
  const items = [...entry.querySelectorAll('.tenure-review-evidence')].map((i) => {
    const r = i.getBoundingClientRect();
    const name = (i.getAttribute('data-testid') || '').replace('tenure-review-evidence-', '');
    return { name, left: r.left, top: r.top, right: r.right, bottom: r.bottom };
  });
  const e = entry.getBoundingClientRect();
  return { items, left: e.left, right: e.right, top: e.top, bottom: e.bottom };
})"""

HISTORY_BARS_JS = """() => {
  const note = document.querySelector('[data-explainer="Y10"]');
  const nr = note.getBoundingClientRect();
  const tops = [...document.querySelectorAll('#daily-chart-svg rect[data-date]')]
    .map((r) => r.getBoundingClientRect())
    .filter((b) => b.height > 0)
    .map((b) => b.top);
  return { noteBottom: nr.bottom, barTops: tops };
}"""

FR_LAYOUT_JS = """() => {
  const rootSize = parseFloat(getComputedStyle(document.documentElement).fontSize);
  const panel = document.querySelector('[data-testid="tab-panel-frontrunner-builder"]');
  const ps = getComputedStyle(panel);
  const pr = panel.getBoundingClientRect();
  const content = { left: pr.left + parseFloat(ps.paddingLeft), right: pr.right - parseFloat(ps.paddingRight) };
  const rect = (el) => { if (!el) return null; const b = el.getBoundingClientRect();
    return { left: b.left, top: b.top, right: b.right, bottom: b.bottom, width: b.width }; };
  const cards = [...document.querySelectorAll('[data-testid="frontrunner-proposal-card"]')].map((card) => {
    const get = (s) => rect(card.querySelector(s));
    return {
      card: rect(card), table: get('[data-testid="stats-table"]'), approve: get('[data-testid="fr-approve-btn"]'),
      reject: get('[data-testid="fr-reject-btn"]'), overlay: get('[data-testid="fr-overlay-preview"]'),
      preview: get('[data-testid="fr-raw-preview"]'), summary: get('[data-testid="fr-overlay-summary"]'),
    };
  });
  return { rootSize, content, cards };
}"""
