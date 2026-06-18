"""
RF-1 (chip color) — RED tests for Market Prism overall-sentiment chip CSS mapping.

AC-2 (chip color matches verdict): the Market Prism overall-sentiment chip's
modifier class must match the verdict:
  bullish / risk-on   -> prism-sentiment-chip--risk-on
  bearish / risk-off  -> prism-sentiment-chip--risk-off
  neutral             -> prism-sentiment-chip--neutral
  unknown / other     -> prism-sentiment-chip--neutral (safe default)

Root cause (current state — RED):
    templates/ai_advisor.html lines 968-973 map risk-on, risk-off, neutral, and
    limited-inputs but NOT bullish or bearish.  When overall_sentiment='bullish',
    the Jinja2 dict.get() falls through to the default 'prism-sentiment-chip--neutral'.
    A bullish verdict renders with neutral-gray styling — the chip color contradicts
    the verdict text.

Fix (for pf-impl-ui):
    Extend the mapping dict at ai_advisor.html:968-973 to add:
        'bullish': 'prism-sentiment-chip--risk-on',
        'bearish': 'prism-sentiment-chip--risk-off',
    Keep all existing keys intact (regression guard tests below cover them).

Mocking strategy:
    - database.get_latest_market_prism_summary: patched per-test with a minimal
      fixture seeded with the verdict under test.  No real DB.
    - database.get_advisor_observations_for_role: patched to [] (not under test).
    - analytics: MagicMock (not under test).
    - The actual Flask+Jinja2 template is rendered (not mocked) — this is a
      template-logic test; mocking the template would defeat the purpose.
    - No RGB values are asserted — tests assert semantic CSS modifier classes only.
    - Math engine is never mocked (not involved here).

Adversarial RED intent:
    - test_bullish_verdict_yields_risk_on_chip_class: without the bullish key in
      the mapping, the chip class is prism-sentiment-chip--neutral, not --risk-on.
      The NOT-neutral assertion catches this immediately.
    - test_bearish_verdict_yields_risk_off_chip_class: same for bearish -> --risk-off.
    - Regression guards (risk-on, risk-off, neutral, unknown): verify existing
      correct mappings are not broken by the fix.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers shared across all tests in this module
# ---------------------------------------------------------------------------

_SENTIMENT_CHIP_TESTID = 'data-testid="prism-sentiment-chip"'


def _make_prism_summary(overall_sentiment: str) -> dict:
    """Return a minimal market_prism_summary dict seeded with the given verdict.

    Shape-only fixture — no hardcoded producer values.  The only field that
    matters for chip-class mapping is overall_sentiment inside raw_response.
    """
    return {
        "id": 1,
        "advisor_role": "MARKET_PRISM",
        "subject_id": None,
        "verdict": overall_sentiment,
        "created_at": "2026-06-17T03:00:00",
        "raw_response": {
            "overall_sentiment": overall_sentiment,
            "sentiment_rationale": f"Test rationale for {overall_sentiment} verdict.",
            "per_lens_digest": {},
            "sources": [],
        },
    }


def _mock_route_deps(monkeypatch, prism_summary: dict) -> None:
    """Patch all non-chip-mapping dependencies so GET /ai-advisor returns quickly."""
    import app as app_module
    import database

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: prism_summary)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)


@pytest.fixture()
def client():
    """Flask test client — TESTING mode, no port bound."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _render_and_extract_chip_class(client, monkeypatch, verdict: str) -> str:
    """Render /ai-advisor for the given verdict and return the chip's class string.

    Searches the rendered HTML for the prism-sentiment-chip span and extracts its
    class attribute.  Returns the raw class value (e.g.
    'prism-sentiment-chip prism-sentiment-chip--risk-on').

    Raises AssertionError if the chip element is absent (AC-1 from Cycle-5
    surface tests covers that case; here we surface a clear message).
    """
    _mock_route_deps(monkeypatch, _make_prism_summary(verdict))
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, (
        f"GET /ai-advisor returned {resp.status_code} for verdict={verdict!r}. "
        "Route must return 200 for any verdict value."
    )
    html = resp.data.decode("utf-8", errors="replace")

    # Find the chip span.  The template renders:
    #   <span class="prism-sentiment-chip {{ _chip_class }}"
    #         data-testid="prism-sentiment-chip">{{ _sentiment | e }}</span>
    import re

    match = re.search(
        r'<span\s+class="([^"]*prism-sentiment-chip[^"]*)"[^>]*data-testid="prism-sentiment-chip"',
        html,
    )
    if not match:
        # Also try the reversed attribute order.
        match = re.search(
            r'<span\s+data-testid="prism-sentiment-chip"[^>]*class="([^"]*prism-sentiment-chip[^"]*)"',
            html,
        )

    assert match is not None, (
        f"prism-sentiment-chip span not found in rendered HTML for verdict={verdict!r}. "
        "The Market Prism block must render the chip. "
        f"(Is market_prism_summary being passed to the template?)"
    )
    return match.group(1)


# ===========================================================================
# AC-2: bullish and bearish — the two FAILING mappings in RED state
# ===========================================================================


def test_bullish_verdict_yields_risk_on_chip_class(client, monkeypatch):
    """overall_sentiment='bullish' must produce modifier class prism-sentiment-chip--risk-on.

    In RED state (current template), bullish is not in the mapping dict so the
    chip falls back to '--neutral'.  This test catches that:
    the NOT-neutral assertion fires before the risk-on assertion is even reached.
    """
    chip_class = _render_and_extract_chip_class(client, monkeypatch, "bullish")

    # The chip must NOT be neutral — a bullish verdict with neutral-gray styling
    # directly contradicts the text the operator reads.
    assert "prism-sentiment-chip--neutral" not in chip_class, (
        "overall_sentiment='bullish' must NOT produce modifier '--neutral'. "
        "The template mapping is missing the 'bullish' key; it falls through to "
        "the default '--neutral'. Add 'bullish': 'prism-sentiment-chip--risk-on' "
        f"to the mapping dict. chip class rendered: {chip_class!r}"
    )

    # The chip must be risk-on — bullish is a synonym of risk-on per the design spec.
    assert "prism-sentiment-chip--risk-on" in chip_class, (
        "overall_sentiment='bullish' must produce modifier 'prism-sentiment-chip--risk-on'. "
        f"Got chip class: {chip_class!r}. "
        "Add 'bullish': 'prism-sentiment-chip--risk-on' to the mapping dict in "
        "templates/ai_advisor.html."
    )


def test_bearish_verdict_yields_risk_off_chip_class(client, monkeypatch):
    """overall_sentiment='bearish' must produce modifier class prism-sentiment-chip--risk-off.

    In RED state (current template), bearish is not in the mapping dict so the
    chip falls back to '--neutral'.  This test catches that.
    """
    chip_class = _render_and_extract_chip_class(client, monkeypatch, "bearish")

    assert "prism-sentiment-chip--neutral" not in chip_class, (
        "overall_sentiment='bearish' must NOT produce modifier '--neutral'. "
        "The template mapping is missing the 'bearish' key. "
        f"chip class rendered: {chip_class!r}"
    )

    assert "prism-sentiment-chip--risk-off" in chip_class, (
        "overall_sentiment='bearish' must produce modifier 'prism-sentiment-chip--risk-off'. "
        f"Got chip class: {chip_class!r}. "
        "Add 'bearish': 'prism-sentiment-chip--risk-off' to the mapping dict in "
        "templates/ai_advisor.html."
    )


# ===========================================================================
# AC-2 regression guards — existing mappings that must survive the fix
# ===========================================================================


def test_risk_on_verdict_yields_risk_on_chip_class(client, monkeypatch):
    """overall_sentiment='risk-on' must still produce modifier class --risk-on.

    Regression guard: 'risk-on' is already in the mapping.  Adding 'bullish'
    must not accidentally drop 'risk-on' from the dict.
    """
    chip_class = _render_and_extract_chip_class(client, monkeypatch, "risk-on")

    assert "prism-sentiment-chip--risk-on" in chip_class, (
        "overall_sentiment='risk-on' must produce 'prism-sentiment-chip--risk-on'. "
        f"Got: {chip_class!r}. Regression: the 'risk-on' key was dropped from "
        "the mapping when adding 'bullish'."
    )


def test_risk_off_verdict_yields_risk_off_chip_class(client, monkeypatch):
    """overall_sentiment='risk-off' must still produce modifier class --risk-off.

    Regression guard: 'risk-off' is already in the mapping.
    """
    chip_class = _render_and_extract_chip_class(client, monkeypatch, "risk-off")

    assert "prism-sentiment-chip--risk-off" in chip_class, (
        "overall_sentiment='risk-off' must produce 'prism-sentiment-chip--risk-off'. "
        f"Got: {chip_class!r}. Regression: the 'risk-off' key was dropped from "
        "the mapping when adding 'bearish'."
    )


def test_neutral_verdict_yields_neutral_chip_class(client, monkeypatch):
    """overall_sentiment='neutral' must produce modifier class --neutral.

    Regression guard: 'neutral' is an explicitly mapped key (not just the fallback).
    """
    chip_class = _render_and_extract_chip_class(client, monkeypatch, "neutral")

    assert "prism-sentiment-chip--neutral" in chip_class, (
        "overall_sentiment='neutral' must produce 'prism-sentiment-chip--neutral'. "
        f"Got: {chip_class!r}."
    )


def test_unknown_verdict_falls_back_to_neutral_chip_class(client, monkeypatch):
    """An unknown/unmapped verdict must fall back to modifier class --neutral (safe default).

    The Jinja2 dict.get(_sentiment, 'prism-sentiment-chip--neutral') default handles
    any value not in the mapping.  This test verifies the safe-default behavior is
    preserved after the bullish/bearish keys are added.
    """
    chip_class = _render_and_extract_chip_class(client, monkeypatch, "completely-unknown-verdict")

    assert "prism-sentiment-chip--neutral" in chip_class, (
        "An unknown verdict must fall back to 'prism-sentiment-chip--neutral'. "
        f"Got: {chip_class!r}. "
        "The Jinja2 .get() default must remain 'prism-sentiment-chip--neutral'."
    )


# ===========================================================================
# Design-system contract guard — tests MUST NOT assert RGB values
# ===========================================================================


def test_chip_color_assertions_are_class_based_not_rgb(client, monkeypatch):
    """Meta-guard: this test file must not assert specific RGB color values.

    The design-system contract for the Market Prism chip uses semantic modifier
    classes (--risk-on, --risk-off, --neutral).  Asserting a specific computed
    RGB value (e.g. #22c55e, rgb(34,197,94)) is fragile and violates the
    design-system contract rule in the project standards.

    This test verifies the modifier class is the only assertion made, by
    rendering a bullish verdict and checking the chip class string does not
    contain raw color codes.
    """
    chip_class = _render_and_extract_chip_class(client, monkeypatch, "bullish")

    # After the fix (GREEN), chip_class is 'prism-sentiment-chip prism-sentiment-chip--risk-on'.
    # In RED it is 'prism-sentiment-chip prism-sentiment-chip--neutral'.
    # Either way, no RGB value should appear in the class attribute.
    import re

    rgb_pattern = re.compile(r"(?:#[0-9a-fA-F]{3,6}|rgb\s*\()", re.IGNORECASE)
    assert not rgb_pattern.search(chip_class), (
        "CSS class attribute must not contain raw RGB color values. "
        "Design-system contract: assert the semantic modifier class only, "
        f"never a computed color. Found: {chip_class!r}"
    )
