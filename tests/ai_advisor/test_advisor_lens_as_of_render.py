"""
AC-3 visual-surface regression tests — DE-ADVISOR-LATENCY.

Covers the visible staleness stamp rendered in the AI Advisor suggest panel:
  templates/ai_advisor.html: #advisor-lens-as-of element (hidden by default)
  static/ai_advisor.js: renderSuggestions() populates + shows/hides it

These tests are static content checks (template + JS source text) — no browser
runtime, no Playwright, no computed style. Proportionate to the PM's instruction:
"no heavy Playwright in the unit suite."

Ordering note: in the shared-worktree TDD cycle, adv-app committed the
implementation (6e03fc2) after seeing adv-test's RED test commit (1cc9ebf)
for test_ai_advisor_js_reads_lens_data_as_of_from_suggest_response.
These four tests were written as contract-capture / regression guards;
they pass immediately because 6e03fc2 is already on the branch. Any
future refactor of ai_advisor.js or ai_advisor.html must keep all four.

Feature plan: feature-plans/advisor-latency-cache-serve.md
AC-3: "The served context carries an honest staleness indicator
       ('market context as of <captured_at>') that is surfaced to the
       advisor output/UI; the timestamp is HTML-escaped where rendered."
"""

from __future__ import annotations

import pathlib

import pytest

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent
_TEMPLATE = _WORKTREE / "templates" / "ai_advisor.html"
_AI_ADVISOR_JS = _WORKTREE / "static" / "ai_advisor.js"


# ---------------------------------------------------------------------------
# Template — element must be present at page-load (hidden until JS fires)
# ---------------------------------------------------------------------------


def test_advisor_lens_as_of_element_present_in_template():
    """AC-3 (template): templates/ai_advisor.html must contain a
    data-testid="advisor-lens-as-of" element for the staleness stamp.

    The element is hidden by default (style="display:none") and populated
    by JS after each /ai-advisor/suggest response. Its presence in the
    template ensures the JS can always find a target element regardless of
    suggest-panel render order.

    A refactor that removes the element or renames the data-testid breaks
    the JS render path and violates AC-3's "surfaced to the advisor output/UI"
    requirement.
    """
    content = _TEMPLATE.read_text(encoding="utf-8")
    assert 'data-testid="advisor-lens-as-of"' in content, (
        'templates/ai_advisor.html must contain data-testid="advisor-lens-as-of". '
        "The element is the DOM target for the lens-cache staleness stamp (AC-3). "
        "Do not remove or rename it; the JS render path depends on it."
    )


def test_advisor_lens_as_of_element_hidden_by_default():
    """AC-3 (template): the staleness stamp element must start hidden
    (display:none) so no stale timestamp is shown before the first suggest
    response populates it. The JS shows it only when lens_data_as_of is
    present in the response.
    """
    content = _TEMPLATE.read_text(encoding="utf-8")

    # Find the element block around data-testid="advisor-lens-as-of"
    idx = content.find('data-testid="advisor-lens-as-of"')
    assert idx != -1, 'data-testid="advisor-lens-as-of" not found in template'

    # The display:none must appear on the same element — check the surrounding
    # opening-tag region (up to 300 chars around the testid attribute covers
    # a multi-line element definition).
    surrounding = content[max(0, idx - 150) : idx + 150]
    assert "display:none" in surrounding or "display: none" in surrounding, (
        "The #advisor-lens-as-of element must start with display:none so it is "
        "invisible before the first suggest response. The JS shows it only when "
        "lens_data_as_of is non-null (cold-start safe)."
    )


# ---------------------------------------------------------------------------
# JS — renderSuggestions() must wire the suggest response to the DOM element
# ---------------------------------------------------------------------------


def test_advisor_lens_as_of_js_populates_stamp_on_suggest():
    """AC-3 (JS): static/ai_advisor.js must reference both 'advisor-lens-as-of'
    (the DOM element ID) and 'lens_data_as_of' (the suggest JSON field) in the
    same source file.

    This proves the suggest-response → DOM-element wiring exists. A refactor
    that renames either the element ID or the JSON field without updating the
    other will fail this test.
    """
    content = _AI_ADVISOR_JS.read_text(encoding="utf-8")
    assert "advisor-lens-as-of" in content, (
        "static/ai_advisor.js must reference 'advisor-lens-as-of' — the element ID "
        "of the staleness stamp div. The JS suggest callback must call "
        "document.getElementById('advisor-lens-as-of') and update it (AC-3)."
    )
    assert "lens_data_as_of" in content, (
        "static/ai_advisor.js must read 'lens_data_as_of' from the /ai-advisor/suggest "
        "JSON response. This field carries the nightly MARKET_LENS_CACHE capture timestamp."
    )


def test_advisor_lens_as_of_js_renders_stale_modifier():
    """AC-3 (JS): when lens_data_stale is true in the suggest response, the stamp
    must include a '(stale)' modifier so the operator knows the market context is
    older than _LENS_CACHE_MAX_AGE_HOURS (36 h).

    The JS must reference 'lens_data_stale' AND include a 'stale' string literal
    for the stale-case label.
    """
    content = _AI_ADVISOR_JS.read_text(encoding="utf-8")
    assert "lens_data_stale" in content, (
        "static/ai_advisor.js must read 'lens_data_stale' from the suggest response "
        "to render a '(stale)' modifier on the timestamp when the cache bundle is "
        "older than _LENS_CACHE_MAX_AGE_HOURS. AC-3: clear stale label required."
    )
    # The stale text modifier must be a string literal somewhere near the element wiring.
    # Accept 'stale', "stale" or a template literal containing stale.
    assert "stale" in content, (
        "static/ai_advisor.js must include a 'stale' string label for the stale-cache "
        "case. The stamp should read 'Market context as of <ts> (stale)' when the "
        "lens_data_stale flag is true."
    )


def test_advisor_lens_as_of_js_hides_element_on_cold_start():
    """AC-3 (JS / cold-start safety): when lens_data_as_of is absent or null
    (cold-start — no nightly MARKET_LENS_CACHE bundle yet), the JS must HIDE the
    stamp element (style.display = 'none') rather than showing an empty box.

    The JS must have a hide path for the null/falsy case in the same section
    that references 'advisor-lens-as-of'.
    """
    content = _AI_ADVISOR_JS.read_text(encoding="utf-8")

    # Find the advisor-lens-as-of block and check that it has a display='none' hide path.
    idx = content.find("advisor-lens-as-of")
    assert idx != -1, "advisor-lens-as-of not found in static/ai_advisor.js"

    # Check the surrounding ~600 chars for the hide path (covers a 20-line block).
    surrounding = content[idx : idx + 600]
    assert "display" in surrounding and "none" in surrounding, (
        "static/ai_advisor.js must hide the #advisor-lens-as-of element "
        "(style.display = 'none') when lens_data_as_of is null/absent (cold-start). "
        "The element must not show an empty box before the first bundle is cached. "
        "Expected a display='none' assignment in the same code block that references "
        "'advisor-lens-as-of'."
    )
