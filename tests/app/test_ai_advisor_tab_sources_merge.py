"""
RED tests for ai_advisor_tab() MARKET_PRISM_SOURCES merge logic (DE-PRISM-SOURCES-001 v2).

v2 design: app.py's ai_advisor_tab() fetches BOTH:
  - database.get_latest_market_prism_summary() -> the MARKET_PRISM row
  - database.get_latest_market_prism_sources_for_run(run_id) -> the MARKET_PRISM_SOURCES row

It then merges the SOURCES row's per_lens_digest[lens].article_corpus into the MARKET_PRISM
row's per_lens_digest[lens] BEFORE humanization and render, matching by run_id.

These tests target the ROUTE LOGIC (the merge step in ai_advisor_tab), not the DB or
prism_scheduler logic. They verify:

  M-1  When a SOURCES row exists for the current MARKET_PRISM run_id, the route passes
       article_corpus to the template context for the relevant lenses.
  M-2  When the SOURCES accessor returns None (no SOURCES row for this run), the route
       does not crash and the template context per_lens_digest is unchanged (no article_corpus
       injected — honest empty-state).
  M-3  When MARKET_PRISM run_id differs from the SOURCES row's run_id (accessor returns None
       for the stale run_id), the route does NOT inject the stale article_corpus —
       no cross-run citation bleed (AC-9 critical).
  M-4  The merge is additive: pre-existing per_lens_digest keys are preserved.

Design:
  - Patches database.get_latest_market_prism_summary and
    database.get_latest_market_prism_sources_for_run per test.
  - Patches render_template to capture the context dict (avoids full Jinja render overhead
    while testing the merge logic). Alternatively, uses get_data + html parsing.
  - _disable_auth_for_tests (autouse conftest fixture) handles auth gate.
  - Fixture shapes follow production contracts (run_id == run_ts per synthesizer.md:130-132).
  - No hardcoded producer-computed values; asserts dict presence/shape only.
"""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client with TESTING mode."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Shared fixture data (production-shape)
# ---------------------------------------------------------------------------

_RUN_ID_CURRENT = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_RUN_ID_STALE = "ffffffff-ffff-ffff-ffff-ffffffffffff"

# MARKET_PRISM row: no article_corpus in per_lens_digest (council never populates it).
# run_id == run_ts (same UUID4) per prism-synthesizer.md:130-132.
_MARKET_PRISM_ROW = {
    "id": 50,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "created_at": "2026-06-24 03:00:00",
    "raw_response": {
        "run_id": _RUN_ID_CURRENT,
        "run_ts": _RUN_ID_CURRENT,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Merge test.",
        "per_lens_digest": {
            "technicals": {"available": True, "summary": "SMA above", "sources": []},
            "sentiment": {"available": True, "summary": "Neutral", "sources": []},
            "macro": {"available": True, "summary": "10yr 4.4%", "sources": []},
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
        "sources": [],
    },
}

# SOURCES row: carries article_corpus for sentiment and macro only.
_SOURCES_ROW = {
    "id": 51,
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
                        "title": "Merge Test Article",
                        "url": "https://example.com/merge-test-article",
                        "published": "2026-06-24",
                        "lens": "sentiment",
                    }
                ]
            },
            "macro": {
                "article_corpus": [
                    {
                        "title": "FRED Merge Test",
                        "url": "https://fred.stlouisfed.org/series/merge-test",
                        "published": "2026-06-24",
                        "lens": "macro",
                    }
                ]
            },
        },
    },
}

# SOURCES row with a DIFFERENT run_id (stale).
_SOURCES_ROW_STALE_RUN_ID = {
    "id": 99,
    "advisor_role": "MARKET_PRISM_SOURCES",
    "subject_id": "global",
    "verdict": None,
    "created_at": "2026-06-23 03:01:00",
    "raw_response": {
        "run_id": _RUN_ID_STALE,
        "per_lens_digest": {
            "sentiment": {
                "article_corpus": [
                    {
                        "title": "Stale Article",
                        "url": "https://example.com/stale-article",
                        "published": "2026-06-23",
                        "lens": "sentiment",
                    }
                ]
            },
        },
    },
}


# ---------------------------------------------------------------------------
# M-1: SOURCES row present -> article_corpus merged into render
# ---------------------------------------------------------------------------


def test_ai_advisor_tab_merges_sources_article_corpus_into_render(client):
    """M-1: GIVEN a MARKET_PRISM row (run_id=CURRENT) AND a SOURCES row (run_id=CURRENT)
    with article_corpus for sentiment and macro, WHEN GET /ai-advisor is called,
    THEN the HTML render contains the article_corpus urls as <a href> links.

    This proves the route performs the merge before rendering.
    """
    with (
        patch(
            "database.get_latest_market_prism_summary",
            return_value=_MARKET_PRISM_ROW,
        ),
        patch(
            "database.get_latest_market_prism_sources_for_run",
            return_value=_SOURCES_ROW,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'href="https://example.com/merge-test-article"' in html, (
        "Route must merge SOURCES row article_corpus into per_lens_digest before render. "
        "Expected sentiment article_corpus url as <a href> in the rendered HTML. "
        f"HTML snippet (first 3000 chars): {html[:3000]!r}"
    )


# ---------------------------------------------------------------------------
# M-2: SOURCES row is None -> no crash, per_lens_digest unchanged
# ---------------------------------------------------------------------------


def test_ai_advisor_tab_no_crash_when_sources_row_none(client):
    """M-2: GIVEN a MARKET_PRISM row exists but SOURCES accessor returns None,
    WHEN GET /ai-advisor is called, THEN response is 200 and no article_corpus urls
    are injected (honest empty-state — per_lens_digest unchanged).
    """
    with (
        patch(
            "database.get_latest_market_prism_summary",
            return_value=_MARKET_PRISM_ROW,
        ),
        patch(
            "database.get_latest_market_prism_sources_for_run",
            return_value=None,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        "Route must return 200 when SOURCES row is None — honest empty-state, no crash"
    )
    html = resp.get_data(as_text=True)

    assert 'href="https://example.com/merge-test-article"' not in html, (
        "When SOURCES row is None, no article_corpus urls must appear — "
        "per_lens_digest unchanged (honest empty-state, no fabricated links)."
    )


# ---------------------------------------------------------------------------
# M-3: Cross-run mismatch -> no stale citation bleed (AC-9 critical)
# ---------------------------------------------------------------------------


def test_ai_advisor_tab_no_stale_citation_bleed_on_run_id_mismatch(client):
    """M-3 / AC-9 CRITICAL: GIVEN a MARKET_PRISM row with run_id=CURRENT and
    SOURCES accessor returns None (because no SOURCES row exists for CURRENT run_id),
    THEN no article_corpus from the stale SOURCES row (run_id=STALE) appears in the render.

    The route must call get_latest_market_prism_sources_for_run with the MARKET_PRISM
    row's run_id. The accessor itself enforces the no-fallback rule (returns None on
    mismatch). This test verifies the route passes the correct run_id to the accessor
    and handles None correctly.
    """
    # The accessor is called with the MARKET_PRISM row's run_id (_RUN_ID_CURRENT).
    # It returns None because no SOURCES row exists for that run_id.
    # The stale SOURCES row (_SOURCES_ROW_STALE_RUN_ID, run_id=STALE) must NOT appear.
    with (
        patch(
            "database.get_latest_market_prism_summary",
            return_value=_MARKET_PRISM_ROW,
        ),
        patch(
            "database.get_latest_market_prism_sources_for_run",
            return_value=None,  # accessor correctly returns None for run_id=CURRENT
        ) as mock_accessor,
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # Verify the accessor was called with the correct run_id.
    (
        mock_accessor.assert_called_once_with(_RUN_ID_CURRENT),
        (
            f"get_latest_market_prism_sources_for_run must be called with the MARKET_PRISM "
            f"row's run_id={_RUN_ID_CURRENT!r}; actual calls: {mock_accessor.call_args_list!r}"
        ),
    )

    # No stale article_corpus urls must appear.
    assert 'href="https://example.com/stale-article"' not in html, (
        "Stale SOURCES row article_corpus must not appear when accessor returns None "
        "(cross-run mismatch / no-stale-bleed guard, AC-9)."
    )


# ---------------------------------------------------------------------------
# M-4: Merge is additive — pre-existing per_lens_digest keys preserved
# ---------------------------------------------------------------------------


def test_ai_advisor_tab_merge_preserves_existing_per_lens_digest_keys(client):
    """M-4: The merge must be ADDITIVE — pre-existing keys in per_lens_digest[lens]
    (e.g. summary, sources, available) must be preserved alongside the new article_corpus.

    GIVEN a MARKET_PRISM row with sentiment.summary="Neutral" and
    a SOURCES row with sentiment.article_corpus=[...],
    WHEN the route merges them and renders,
    THEN the rendered HTML still contains "Neutral" (the pre-existing summary).
    """
    with (
        patch(
            "database.get_latest_market_prism_summary",
            return_value=_MARKET_PRISM_ROW,
        ),
        patch(
            "database.get_latest_market_prism_sources_for_run",
            return_value=_SOURCES_ROW,
        ),
    ):
        resp = client.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    # The pre-existing summary text "Neutral" must still appear.
    assert "Neutral" in html, (
        "Pre-existing per_lens_digest[lens].summary must be preserved after merge. "
        "Merge must be additive (inject article_corpus only), not a replacement."
    )
    # And the new article_corpus url must also appear.
    assert 'href="https://example.com/merge-test-article"' in html, (
        "article_corpus url must also appear after additive merge."
    )
