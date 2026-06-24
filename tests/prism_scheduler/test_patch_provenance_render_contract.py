"""
RED render-contract tests for DE-PRISM-SOURCES-001.

Guards the template/route contract for Overview Market Prism sources:
  - article_corpus url dicts → <a href> links in the rendered HTML
  - urlless plain-string sources → <span class="prism-source-citation"> (no <a href>)
  - No <a href="#"> fallback links emitted for urlless sources (AC-3: no invented urls)

Acceptance criteria:
  AC-5  GIVEN raw_response with article_corpus url dicts → Overview render emits
        <a href> per citation. GIVEN urlless sources → plain spans, no <a href="#">.

Design:
  - Reuses the `client` fixture pattern from test_cycle5_market_prism_surface.py:
    import app, TESTING=True, test_client(). _disable_auth_for_tests (autouse
    conftest fixture) disables the auth gate — no explicit session setup needed.
  - database.get_latest_market_prism_summary is patched per-test with a fixture
    row so no real DB write is needed (same pattern as the Cycle 5 surface tests).
  - Both directions are tested (PR #81 lesson): real urls AND urlless sources.
  - No <a href="#"> must appear for urlless sources — template defaults missing url
    to '#' (ai_advisor.html:L963) and gates the <a> on url-truthiness (L1034);
    a '#' link means a fabricated url leaked through (AC-3 violation).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Flask test client (function-scoped, same pattern as cycle-5 surface tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client with TESTING mode. _disable_auth_for_tests (conftest autouse)
    handles auth gate — no explicit session cookie needed.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Shared raw_response shapes
# ---------------------------------------------------------------------------

_SUMMARY_WITH_ARTICLE_CORPUS = {
    "id": 99,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "created_at": "2026-06-24 03:00:00",
    "raw_response": {
        "run_id": "render-contract-test-run",
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Mixed signals for render test.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": "SMA test",
                "sources": [],
            },
            "sentiment": {
                "available": True,
                "summary": "Sentiment test",
                "sources": [],
                # article_corpus: these should render as <a href> links.
                "article_corpus": [
                    {
                        "title": "Test Article Render",
                        "url": "https://example.com/render-test-article",
                        "published": "2026-06-24",
                        "lens": "sentiment",
                    }
                ],
            },
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "macro": {
                "available": True,
                "summary": "Macro test",
                "sources": [],
            },
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
        "sources": [],
    },
}

_SUMMARY_WITH_URLLESS_SOURCES = {
    "id": 100,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "created_at": "2026-06-24 03:00:00",
    "raw_response": {
        "run_id": "render-contract-urlless-run",
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Urlless sources render test.",
        "per_lens_digest": {
            "technicals": {"available": False, "reason": "StubError", "sources": []},
            "sentiment": {"available": False, "reason": "StubError", "sources": []},
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "macro": {
                "available": True,
                "summary": "Macro urlless test",
                # Plain string citation — no url key — must render as <span>, not <a>.
                "sources": ["FRED:T10YIE"],
            },
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
        "sources": [],
    },
}


# ---------------------------------------------------------------------------
# T8 — AC-5 render-contract (url-bearing direction): article_corpus → <a href>
# ---------------------------------------------------------------------------


def test_render_article_corpus_emits_anchor_links(client):
    """AC-5: GIVEN a raw_response with article_corpus url dicts,
    WHEN GET /ai-advisor is rendered,
    THEN the HTML contains <a href="https://..."> for each citation AND
    the title text appears within an anchor element, AND
    data-testid="prism-source-item" exists for the citation.
    """
    with patch(
        "database.get_latest_market_prism_summary",
        return_value=_SUMMARY_WITH_ARTICLE_CORPUS,
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The article_corpus entry has url="https://example.com/render-test-article"
    assert 'href="https://example.com/render-test-article"' in html, (
        "article_corpus url must be rendered as href in <a> tag; "
        f"searched for href in HTML snippet: {html[html.find('prism-source') : html.find('prism-source') + 500]!r}"
    )
    # The title must appear in the rendered HTML (escaped).
    assert "Test Article Render" in html, "article_corpus title must appear in rendered HTML"
    # The prism-source-item testid must be present.
    assert 'data-testid="prism-source-item"' in html, (
        "prism-source-item testid must be rendered for any source entry"
    )
    # The anchor must carry prism-source-link class (template uses class="prism-source-link").
    assert 'class="prism-source-link"' in html, (
        "article_corpus links must carry class=prism-source-link"
    )


# ---------------------------------------------------------------------------
# T9 — AC-5 render-contract (urlless direction): plain sources → <span>, no <a href="#">
# ---------------------------------------------------------------------------


def test_render_urlless_sources_emits_span_not_anchor(client):
    """AC-5 (second direction): GIVEN per_lens_digest with a plain-string sources entry
    and no article_corpus,
    WHEN GET /ai-advisor is rendered,
    THEN the HTML contains a <span class="prism-source-citation"> for that entry
    AND no <a href="#"> is emitted (no fabricated/defaulted url — AC-3 violation check).
    """
    with patch(
        "database.get_latest_market_prism_summary",
        return_value=_SUMMARY_WITH_URLLESS_SOURCES,
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The plain string citation must render as a <span class="prism-source-citation">.
    assert 'class="prism-source-citation"' in html, (
        "Urlless plain-string sources must render as <span class=prism-source-citation>, "
        "not as anchor links"
    )
    # No fabricated '#' url fallback — a href="#" means the template's default_url
    # leaked through, which means a citation was rendered as a link when it had no url.
    # AC-3: no invented urls.
    assert 'href="#"' not in html, (
        'No <a href="#"> must appear for urlless sources; '
        "a href='#' means a fabricated/defaulted url was emitted (AC-3 violation). "
        "Plain-string sources must render as <span>, never as <a>."
    )
    # Confirm data-testid="prism-source-item" still appears (the source IS rendered).
    assert 'data-testid="prism-source-item"' in html, (
        "prism-source-item testid must be rendered for urlless sources too"
    )
