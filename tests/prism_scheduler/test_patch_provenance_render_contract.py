"""
RED render-contract tests for DE-PRISM-SOURCES-001 v2.

v2 design: app.py's ai_advisor_tab() fetches BOTH:
  - database.get_latest_market_prism_summary() -> the MARKET_PRISM row
  - database.get_latest_market_prism_sources_for_run(run_id) -> the MARKET_PRISM_SOURCES row

It then MERGES the SOURCES row's per_lens_digest[lens].article_corpus into the MARKET_PRISM
row's per_lens_digest before humanization and render.

This test file guards the route's contract for Overview Market Prism sources:
  RC-1  GIVEN both a MARKET_PRISM row and a MARKET_PRISM_SOURCES row with article_corpus
        url dicts -> the Overview render emits <a href> per citation.
  RC-2  GIVEN urlless plain-string sources (in the MARKET_PRISM row, no SOURCES row)
        -> plain spans, no <a href="#">, no crash.
  RC-3  Empty-state: SOURCES row is None (sources accessor returns None)
        -> route does NOT crash, no <a href> fabricated.
  RC-4  Cross-run mismatch: MARKET_PRISM row's run_id differs from available SOURCES row's
        run_id -> sources accessor returns None -> per_lens_digest unchanged (no stale
        citation bleed). (PM-required addition: AC-9 critical guard.)

Design:
  - Patches BOTH database.get_latest_market_prism_summary AND
    database.get_latest_market_prism_sources_for_run per test.
  - _disable_auth_for_tests (autouse conftest fixture) handles the auth gate.
  - Fixture shapes follow production contracts:
    MARKET_PRISM raw_response: {run_id, run_ts (== run_id per synthesizer.md:130-132), ...}
    MARKET_PRISM_SOURCES raw_response: {run_id, per_lens_digest: {lens: {article_corpus: [...]}}}
  - No hardcoded producer-computed values; asserts html fragment presence/absence only.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Flask test client (function-scoped)
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
# Shared run_ids (production shape: UUID4 for run_id == run_ts)
# ---------------------------------------------------------------------------

_RUN_ID_CURRENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_RUN_ID_STALE = "dddddddd-dddd-dddd-dddd-dddddddddddd"

# MARKET_PRISM summary fixture: production shape (prism-synthesizer.md:130-132).
# run_ts == run_id (same UUID4 string).
_MARKET_PRISM_SUMMARY_WITH_RUN_ID = {
    "id": 10,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "created_at": "2026-06-24 03:00:00",
    "raw_response": {
        "run_id": _RUN_ID_CURRENT,
        "run_ts": _RUN_ID_CURRENT,
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
                # No article_corpus yet — will be merged from SOURCES row by the route.
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

# MARKET_PRISM_SOURCES row: per_lens_digest carries only article_corpus for url-bearing lenses.
_SOURCES_ROW_CURRENT = {
    "id": 11,
    "advisor_role": "MARKET_PRISM_SOURCES",
    "subject_id": "global",
    "verdict": None,
    "created_at": "2026-06-24 03:01:00",
    "raw_response": {
        "run_id": _RUN_ID_CURRENT,
        "per_lens_digest": {
            "sentiment": {
                "article_corpus": [
                    {
                        "title": "Test Article Render",
                        "url": "https://example.com/render-test-article",
                        "published": "2026-06-24",
                        "lens": "sentiment",
                    }
                ],
            },
            "macro": {
                "article_corpus": [
                    {
                        "title": "FRED T10Y2Y",
                        "url": "https://fred.stlouisfed.org/series/T10Y2Y",
                        "published": "2026-06-24",
                        "lens": "macro",
                    }
                ],
            },
        },
    },
}

# MARKET_PRISM summary with urlless sources (no SOURCES row needed).
_MARKET_PRISM_SUMMARY_URLLESS = {
    "id": 20,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "created_at": "2026-06-24 03:00:00",
    "raw_response": {
        "run_id": _RUN_ID_CURRENT,
        "run_ts": _RUN_ID_CURRENT,
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

# MARKET_PRISM summary with a DIFFERENT run_id from the available SOURCES row.
_MARKET_PRISM_SUMMARY_DIFFERENT_RUN_ID = {
    "id": 30,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "created_at": "2026-06-24 04:00:00",
    "raw_response": {
        "run_id": _RUN_ID_STALE,
        "run_ts": _RUN_ID_STALE,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Different run test.",
        "per_lens_digest": {
            "sentiment": {
                "available": True,
                "summary": "Different run sentiment",
                "sources": [],
            },
        },
        "sources": [],
    },
}


# ---------------------------------------------------------------------------
# RC-1: url-bearing direction -> <a href> links rendered
# ---------------------------------------------------------------------------


def test_render_article_corpus_emits_anchor_links_v2(client):
    """RC-1 / AC-5: GIVEN a MARKET_PRISM row (run_id=CURRENT) AND a MARKET_PRISM_SOURCES
    row (run_id=CURRENT) with article_corpus url dicts, WHEN the route fetches both and
    merges them, THEN the HTML emits <a href="https://..."> for each citation.

    v2: both get_latest_market_prism_summary AND get_latest_market_prism_sources_for_run
    must be mocked because the route now calls both.
    """
    with (
        patch(
            "database.get_latest_market_prism_summary",
            return_value=_MARKET_PRISM_SUMMARY_WITH_RUN_ID,
        ),
        patch(
            "database.get_latest_market_prism_sources_for_run",
            return_value=_SOURCES_ROW_CURRENT,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'href="https://example.com/render-test-article"' in html, (
        "article_corpus url must be rendered as href in <a> tag after route merges "
        "the SOURCES row's article_corpus into per_lens_digest. "
        f"First 2000 chars of HTML: {html[:2000]!r}"
    )
    assert "Test Article Render" in html, "article_corpus title must appear in rendered HTML"
    assert 'data-testid="prism-source-item"' in html, (
        "prism-source-item testid must be rendered for any source entry"
    )
    assert 'class="prism-source-link"' in html, (
        "article_corpus links must carry class=prism-source-link"
    )


# ---------------------------------------------------------------------------
# RC-2: urlless direction -> <span>, no <a href="#">
# ---------------------------------------------------------------------------


def test_render_urlless_sources_emits_span_not_anchor_v2(client):
    """RC-2 / AC-5 (second direction): GIVEN per_lens_digest with a plain-string sources
    entry and SOURCES accessor returns None,
    THEN the HTML contains a <span class="prism-source-citation"> for that entry
    AND no <a href="#"> is emitted (AC-3 violation check).
    """
    with (
        patch(
            "database.get_latest_market_prism_summary",
            return_value=_MARKET_PRISM_SUMMARY_URLLESS,
        ),
        patch(
            "database.get_latest_market_prism_sources_for_run",
            return_value=None,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'class="prism-source-citation"' in html, (
        "Urlless plain-string sources must render as <span class=prism-source-citation>, "
        "not as anchor links"
    )
    assert 'href="#"' not in html, (
        'No <a href="#"> must appear for urlless sources; '
        "a href='#' means a fabricated/defaulted url was emitted (AC-3 violation)."
    )
    assert 'data-testid="prism-source-item"' in html, (
        "prism-source-item testid must be rendered for urlless sources too"
    )


# ---------------------------------------------------------------------------
# RC-3: empty-state — SOURCES row None -> no crash, no fabricated links
# ---------------------------------------------------------------------------


def test_render_no_sources_row_honest_empty_state_v2(client):
    """RC-3 / AC-4: GIVEN a MARKET_PRISM row exists but SOURCES accessor returns None
    (e.g. all lenses unavailable that night), WHEN GET /ai-advisor is rendered,
    THEN: (a) response is 200 — no crash, (b) no <a href> for sentiment (no article_corpus
    to merge), (c) page still renders (MARKET_PRISM summary text present).

    This tests the honest empty-state path: if no SOURCES row for this run, render
    proceeds without article_corpus — no fallback to stale links.
    """
    with (
        patch(
            "database.get_latest_market_prism_summary",
            return_value=_MARKET_PRISM_SUMMARY_WITH_RUN_ID,
        ),
        patch(
            "database.get_latest_market_prism_sources_for_run",
            return_value=None,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        "Route must return 200 even when SOURCES row is None — honest empty-state"
    )
    html = resp.get_data(as_text=True)

    # No article_corpus was merged -> no <a href="https://example.com/render-test-article">
    assert 'href="https://example.com/render-test-article"' not in html, (
        "When SOURCES row is None, no article_corpus urls should appear as links — "
        "they were not merged into per_lens_digest (honest empty-state)."
    )


# ---------------------------------------------------------------------------
# RC-4: cross-run mismatch -> no stale citation bleed (AC-9 critical guard)
# ---------------------------------------------------------------------------


def test_render_cross_run_mismatch_no_stale_citation_bleed_v2(client):
    """RC-4 / AC-9 CRITICAL: GIVEN the latest MARKET_PRISM row has run_id=STALE and
    get_latest_market_prism_sources_for_run(STALE) returns None (no SOURCES row for
    that run), WHEN GET /ai-advisor is rendered, THEN no article_corpus urls from
    any OTHER run appear in the render — no stale citation bleed.

    This proves the no-fallback contract: the route must not fall back to the latest
    SOURCES row if the run_ids don't match. A night where all lenses are unavailable
    produces no SOURCES row; falling back would show last night's citations against
    tonight's read.
    """
    with (
        patch(
            "database.get_latest_market_prism_summary",
            return_value=_MARKET_PRISM_SUMMARY_DIFFERENT_RUN_ID,
        ),
        patch(
            # Accessor returns None: no SOURCES row for _RUN_ID_STALE.
            "database.get_latest_market_prism_sources_for_run",
            return_value=None,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'href="https://example.com/render-test-article"' not in html, (
        "Cross-run mismatch: SOURCES row from a DIFFERENT run must not bleed into "
        "the current MARKET_PRISM render (no stale citation bleed, AC-9)."
    )
    assert 'href="https://fred.stlouisfed.org/series/T10Y2Y"' not in html, (
        "Cross-run mismatch: macro citation from stale SOURCES row must not appear."
    )
