"""
Regression tests for advisor card overflow fixes — Issues 1, 2, 3.

Source: docs/ux/2026-05-29-advisor-overflow/findings.md
Branch: cycle/advisor-card-fixes

Requires the daemon running at http://localhost:5000 and at least one symphony
with suggestions available. Tests navigate to the advisor page, select a symphony,
trigger suggestions, then assert the three fixed behaviours via browser_evaluate.

These tests use pytest-playwright (sync API). They are marked `live_daemon` and
will skip gracefully if the daemon is not reachable.
"""

from __future__ import annotations

import json
import re

import pytest
import requests
from playwright.sync_api import Page, sync_playwright

BASE_URL = "http://localhost:5000"

# Symphony name used for verification — must have at least 1 suggestion card.
# The long name that surfaced Issue 1 at 1920px.
TARGET_SYMPHONY_SUBSTRING = "Planet of Hunted Cascades"


def _daemon_reachable() -> bool:
    try:
        r = requests.get(BASE_URL + "/ai-advisor", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _pick_symphony(page: Page) -> str | None:
    """Return the value of a symphony option whose text contains TARGET_SYMPHONY_SUBSTRING,
    or any non-empty option if the target is not found."""
    options = page.eval_on_selector_all(
        '#symphony-id-input option',
        'opts => opts.filter(o => o.value).map(o => ({value: o.value, text: o.textContent}))'
    )
    for opt in options:
        if TARGET_SYMPHONY_SUBSTRING in (opt.get('text') or ''):
            return opt['value']
    # fallback to first non-empty option
    for opt in options:
        if opt.get('value'):
            return opt['value']
    return None


def _load_suggestions(page: Page, viewport_width: int, viewport_height: int) -> bool:
    """Navigate to advisor, set viewport, select a symphony, trigger suggestions.
    Returns True if at least one suggestion card is present, False otherwise."""
    page.set_viewport_size({'width': viewport_width, 'height': viewport_height})
    page.goto(BASE_URL + "/ai-advisor")
    page.wait_for_load_state('networkidle')

    sym = _pick_symphony(page)
    if not sym:
        return False

    page.select_option('#symphony-id-input', sym)
    # wait for suggestions to load (the change event auto-triggers getSuggestions)
    page.wait_for_function(
        "() => {"
        "  var el = document.getElementById('suggestions-container');"
        "  if (!el) return false;"
        "  return el.querySelector('[data-testid=\"gate-badge\"]') !== null"
        "    || el.textContent.includes('No suggestions');"
        "}",
        timeout=15000
    )
    has_cards = page.eval_on_selector_all(
        '[data-testid="gate-badge"]',
        'els => els.length > 0'
    )
    return bool(has_cards)


# ---------------------------------------------------------------------------
# Skip marker — applied to all live-daemon tests
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not _daemon_reachable(),
    reason="Live daemon not reachable at http://localhost:5000 — skipping overflow regression tests"
)


@pytest.fixture(scope="module")
def browser_page():
    """Single browser page shared across all three tests (module scope for speed)."""
    with sync_playwright() as pw:
        browser = pw.firefox.launch(headless=True)
        page = browser.new_page()
        yield page
        browser.close()


# ---------------------------------------------------------------------------
# Test 1 — gate badges no-wrap overflow at 1920x1080
# ---------------------------------------------------------------------------


def test_gate_badges_no_wrap_overflow(browser_page: Page):
    """At 1920x1080, no gate badge may have offsetHeight > 20px.

    All 4 badges must sit on the same flex row OR on a clean 2-row layout —
    never with one badge floating at an intermediate y-offset from a mid-word
    line break.

    Fix: display:flex;flex-wrap:wrap on container + white-space:nowrap on each badge.
    """
    loaded = _load_suggestions(browser_page, 1920, 1080)
    if not loaded:
        pytest.skip("No suggestion cards found — cannot verify badge overflow")

    heights = browser_page.eval_on_selector_all(
        '[data-testid="gate-badge"]',
        'badges => badges.map(b => b.offsetHeight)'
    )
    assert heights, "Expected at least one gate-badge element"
    for h in heights:
        assert h <= 20, (
            f"Gate badge has offsetHeight={h}px > 20px — text is wrapping mid-badge. "
            "Container needs display:flex;flex-wrap:wrap and each badge needs white-space:nowrap."
        )


# ---------------------------------------------------------------------------
# Test 2 — rationale full text in DOM (no JS-side truncation)
# ---------------------------------------------------------------------------


def test_rationale_full_text_in_dom(browser_page: Page):
    """The rationale <p> element's textContent length must match the API response
    rationale length — no JS-side substring truncation.

    Fix: removed substring(0, 160) + ellipsis from ai_advisor.js:188.
    """
    # Fetch the suggestions from the API directly to get ground-truth lengths
    page = browser_page
    page.set_viewport_size({'width': 1920, 'height': 1080})
    page.goto(BASE_URL + "/ai-advisor")
    page.wait_for_load_state('networkidle')

    sym = _pick_symphony(page)
    if not sym:
        pytest.skip("No symphony available for rationale test")

    # Call the suggest API directly to capture expected rationale lengths
    resp = requests.post(
        BASE_URL + "/ai-advisor/suggest",
        json={"symphony_id": sym},
        timeout=30,
    )
    assert resp.status_code == 200, f"Suggest API returned {resp.status_code}"
    body = resp.json()
    suggestions = body.get("suggestions", [])
    if not suggestions:
        pytest.skip("Suggest API returned 0 suggestions — cannot verify rationale length")

    # Navigate browser to the page, select symphony, wait for cards
    page.select_option('#symphony-id-input', sym)
    page.wait_for_function(
        "() => {"
        "  var el = document.getElementById('suggestions-container');"
        "  return el && el.querySelector('p') !== null;"
        "}",
        timeout=15000
    )

    # Collect rationale <p> textContent lengths from the DOM
    dom_lengths = page.eval_on_selector_all(
        '#suggestions-container p',
        'paras => paras.map(p => p.textContent.trim().length)'
    )

    # Every API suggestion that has a rationale longer than 160 chars must be
    # fully present (not truncated to 160 + ellipsis)
    for i, s in enumerate(suggestions):
        rationale = s.get("rationale", "")
        if len(rationale) <= 160:
            continue  # short rationale — truncation wasn't possible, skip
        if i >= len(dom_lengths):
            # More suggestions than <p> elements — some may be OOS-rejected collapsed
            continue
        dom_len = dom_lengths[i]
        assert dom_len >= len(rationale), (
            f"Suggestion {i} rationale truncated: DOM has {dom_len} chars, "
            f"API returned {len(rationale)} chars. "
            "JS-side substring truncation must be removed."
        )


# ---------------------------------------------------------------------------
# Test 3 — gate badge a11y spacing (innerText has spaces between badge labels)
# ---------------------------------------------------------------------------


def test_gate_badges_a11y_spacing(browser_page: Page):
    """Gate badge container innerText must contain spaces between the 4 badge labels.

    Without .join(' '), all 4 badge texts are concatenated without a separator,
    producing a single accessibility string like 'allowlist: passrisk direction: pass'.

    Fix: changed .join('') to .join(' ') in the GATE_LABELS map.
    """
    loaded = _load_suggestions(browser_page, 1920, 1080)
    if not loaded:
        pytest.skip("No suggestion cards found — cannot verify badge a11y spacing")

    # Grab the innerText of every gate-badge container (the wrapping flex div
    # is the parent of the gate-badge spans)
    container_texts = browser_page.eval_on_selector_all(
        '[data-testid="gate-badge"]',
        # Get the parent container's innerText from the first badge
        'badges => {'
        '  const seen = new Set();'
        '  return badges.map(b => {'
        '    const parent = b.parentElement;'
        '    if (!parent || seen.has(parent)) return null;'
        '    seen.add(parent);'
        '    return parent.innerText;'
        '  }).filter(t => t !== null);'
        '}'
    )

    assert container_texts, "Expected at least one gate-badge container"

    for text in container_texts:
        # The text should have spaces between the badge labels.
        # "allowlist: pass risk direction: pass" — space after each label
        # The failure mode is "allowlist: passrisk direction: pass" (no space)
        labels = ['allowlist', 'risk direction', 'oos frozen eval', 'locked vars']
        for label in labels:
            # Each label must not be immediately preceded by a non-space char
            # (i.e. the prior badge text must have a space before this label starts)
            idx = text.lower().find(label)
            if idx > 0:
                assert text[idx - 1] == ' ' or text[idx - 1] == '\n', (
                    f"Badge label '{label}' immediately follows previous badge text without "
                    f"a space separator. Container innerText: {text!r}. "
                    "Fix: change .join('') to .join(' ') in GATE_LABELS map."
                )
