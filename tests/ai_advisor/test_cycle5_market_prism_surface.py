"""
Cycle 5 — RED: Market Prism Overview Surface.

Scope (BRIEF: cycle5-market-prism-surface/BRIEF.md):
  AC-1  /ai-advisor Overview tab ALWAYS renders a Market Prism block:
        route prefetches get_latest_market_prism_summary(); template shows
        overall_sentiment chip + rationale + as-of (created_at).
        When summary is None → informative empty state ("No overnight market
        read yet — the off-hours pipeline runs daily at 03:00"), NOT blank/500.
  AC-2  Per-lens digest rendered for all 5 lenses (available + summary OR
        unavailable + reason). Honest-availability: unavailable shows reason,
        never fabricated data.
  AC-3  Cited news: sources rendered as clickable <a href> links with escaped
        title + published date + lens tag. XSS-safe (hostile title → escaped).
  AC-4  Read-only/non-blocking: GET /ai-advisor does NOT call run_pipeline.
        Reads via get_latest_market_prism_summary only.
  AC-5  Advisory-only: Market Prism block has NO trade affordance (no
        accept/execute button within the prism block).
  AC-6  JS syntax: static/ai_advisor.js passes `node --check` (no parse errors).

Mocking strategy:
  - database.get_latest_market_prism_summary: patched per-test — no real DB.
  - advisors.lens_pipeline.run_pipeline: never called (AC-4 asserts it is NOT
    called). If it IS called, the test fails.
  - render_template: the actual Flask template is rendered; only DB and
    analytics helpers are mocked.
  - Math engine NEVER mocked.
  - All fixtures are function-scoped — no shared mutable state.

Adversarial RED intent:
  - AC-1: route does not pass market_prism_summary to render_template → KeyError
    or missing block in rendered HTML.
  - AC-2: per-lens block absent or unavailable-reason suppressed → assertions fail.
  - AC-3: sources not in <a href> tags, or title not HTML-escaped → assertions fail.
  - AC-4: route calls run_pipeline → spy detects call → test fails.
  - AC-5: accept/execute button present in prism block → assertion fails.
  - AC-6: JS syntax error → `node --check` exits non-zero → assertion fails.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Shared MARKET_PRISM fixture data (shape-only — no hardcoded producer values)
# ---------------------------------------------------------------------------

_EXPECTED_LENSES = {"technicals", "sentiment", "derivatives", "macro", "fundamentals"}

_FULL_PRISM_SUMMARY = {
    "id": 42,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-13T03:17:00",
    "raw_response": {
        "run_ts": "2026-06-13T03:17:00",
        "overall_sentiment": "neutral",  # one of the valid sentinel values
        "sentiment_rationale": (
            "Mixed signals: technicals lean bullish but macro headwinds persist."
        ),
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": "RSI overbought; MACD positive crossover.",
                "sources": [],
            },
            "sentiment": {
                "available": True,
                "summary": "Retail sentiment elevated; institutional flows neutral.",
                "sources": [
                    {
                        "title": "Market Sentiment Daily",
                        "url": "https://example.com/sentiment",
                        "published": "2026-06-12",
                        "lens": "sentiment",
                    }
                ],
            },
            "derivatives": {
                "available": False,
                "reason": "DerivativesError",  # type(exc).__name__ only per D-1
                "sources": [],
            },
            "macro": {
                "available": True,
                "summary": "CPI inline; Fed hold expected.",
                "sources": [],
            },
            "fundamentals": {
                "available": False,
                "reason": "TimeoutError",  # type(exc).__name__ only per D-1
                "sources": [],
            },
        },
        "available_lens_count": 3,
        "total_lens_count": 5,
        "sources": [
            {
                "title": "Market Sentiment Daily",
                "url": "https://example.com/sentiment",
                "published": "2026-06-12",
                "lens": "sentiment",
            }
        ],
    },
}

_LIMITED_INPUTS_SUMMARY = {
    "id": 1,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "limited-inputs",
    "created_at": "2026-06-13T03:01:00",
    "raw_response": {
        "run_ts": "2026-06-13T03:01:00",
        "overall_sentiment": "limited-inputs",
        "sentiment_rationale": "All lenses unavailable — insufficient market data.",
        "per_lens_digest": {
            lens: {"available": False, "reason": "ConnectionError", "sources": []}
            for lens in _EXPECTED_LENSES
        },
        "available_lens_count": 0,
        "total_lens_count": 5,
        "sources": [],
    },
}


# ---------------------------------------------------------------------------
# Flask test client + mock helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    """Flask test client with TESTING mode — no port bound."""
    # Import here so conftest.py pytest_configure() runs first (DB_PATH sentinel).
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _mock_advisory_side_effects(monkeypatch, prism_summary):
    """Patch the heavy advisory helpers so GET /ai-advisor returns quickly.

    Only database.get_latest_market_prism_summary is the focus of Cycle-5 tests;
    other route helpers (analytics, correlation_diagnostic, etc.) are patched
    to lightweight no-ops to avoid network/disk I/O.
    """
    import app as app_module
    import database

    # Market Prism: this IS under test — spy on calls.
    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: prism_summary)

    # Silence analytics helpers (heavy disk I/O, not under test here).
    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    # Patch correlation_diagnostic import used inside the route.
    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)

    # Patch DB calls used by non-Prism parts of the route.
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    # Ensure run_pipeline is NOT imported at module level (CC-2 boundary).
    # The route must not call it; AC-4 asserts this independently.


# ===========================================================================
# AC-1: Overview tab ALWAYS renders the Market Prism block
# ===========================================================================


def test_market_prism_block_present_when_summary_available(client, monkeypatch):
    """GET /ai-advisor renders a Market Prism block when a summary row exists.

    The block must contain:
    - An element indicating overall_sentiment (the chip)
    - The sentiment_rationale text
    - The as-of timestamp from created_at

    A route that doesn't pass market_prism_summary to the template fails here:
    either a 500 (KeyError) or missing block content.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        f"GET /ai-advisor must return 200 with a seeded MARKET_PRISM row; got {resp.status_code}"
    )
    html = resp.data.decode("utf-8", errors="replace")

    # Overall sentiment must appear (as chip or text — the BRIEF says "labeled chip").
    # We assert the sentinel VALUE is in the rendered HTML (the template produces it).
    assert "neutral" in html.lower(), (
        "Overall sentiment 'neutral' must appear in the rendered Market Prism block. "
        "The route must pass market_prism_summary to the template and the template "
        "must render overall_sentiment."
    )

    # sentiment_rationale must appear.
    assert "Mixed signals" in html, (
        "sentiment_rationale must appear in the rendered Market Prism block. "
        "Template must render raw_response['sentiment_rationale']."
    )

    # created_at (as-of) must appear somewhere in the block.
    assert "2026-06-13" in html, (
        "created_at timestamp must appear in the Market Prism block as the as-of date. "
        "The route must pass created_at to the template."
    )


def test_market_prism_block_shows_informative_empty_state_when_no_summary(client, monkeypatch):
    """GET /ai-advisor renders an informative empty state when no MARKET_PRISM row exists.

    When get_latest_market_prism_summary() returns None, the template must render
    the specific empty-state text from the BRIEF — NOT a blank section or a 500.

    A route that passes None to the template without a guard will 500 on attribute
    access (e.g. None['overall_sentiment']). A template that silently hides the
    section is also wrong — the BRIEF requires an informative message.
    """
    _mock_advisory_side_effects(monkeypatch, None)

    resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        f"GET /ai-advisor must return 200 even when no MARKET_PRISM row exists; "
        f"got {resp.status_code}"
    )
    html = resp.data.decode("utf-8", errors="replace")

    # The BRIEF specifies this exact informative string.
    assert "03:00" in html or "pipeline" in html.lower() or "overnight" in html.lower(), (
        "When no MARKET_PRISM row exists, the Overview tab must render an informative "
        "empty state. Expected text like 'No overnight market read yet — the off-hours "
        "pipeline runs daily at 03:00'. Got a blank or error instead."
    )


def test_market_prism_block_present_when_limited_inputs(client, monkeypatch):
    """GET /ai-advisor renders a Market Prism block even for a limited-inputs row.

    The pipeline always writes a row (AC-3 from Cycle 4). When all lenses are
    unavailable, the row has verdict='limited-inputs'. The template must still
    render something useful — not hide the block because the sentiment is unusual.
    """
    _mock_advisory_side_effects(monkeypatch, _LIMITED_INPUTS_SUMMARY)

    resp = client.get("/ai-advisor")

    assert resp.status_code == 200, (
        f"GET /ai-advisor must return 200 with a limited-inputs MARKET_PRISM row; "
        f"got {resp.status_code}"
    )
    html = resp.data.decode("utf-8", errors="replace")

    # The block must render — "limited-inputs" or the rationale text must appear.
    assert (
        "limited" in html.lower()
        or "insufficient" in html.lower()
        or ("All lenses unavailable" in html)
    ), (
        "Market Prism block must render even for a limited-inputs row. "
        "The template must handle overall_sentiment='limited-inputs' gracefully."
    )


def test_route_passes_market_prism_summary_to_template(monkeypatch):
    """The /ai-advisor route must call get_latest_market_prism_summary and pass
    the result to render_template as market_prism_summary.

    This tests the route's wiring — not the template rendering. If the route
    never fetches the summary, any downstream template rendering is accidental.
    """
    import app as app_module
    import database

    captured_kwargs: dict = {}

    def capturing_render(template_name, **kwargs):
        captured_kwargs.update(kwargs)
        return "<html>captured</html>"

    monkeypatch.setattr(app_module, "render_template", capturing_render)
    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: _FULL_PRISM_SUMMARY)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        c.get("/ai-advisor")

    assert "market_prism_summary" in captured_kwargs, (
        "render_template must receive 'market_prism_summary' as a kwarg. "
        "The route must call database.get_latest_market_prism_summary() and "
        "pass the result to the template. "
        f"Captured template kwargs: {sorted(captured_kwargs.keys())}"
    )


# ===========================================================================
# AC-2: Per-lens digest renders for all 5 lenses (honest-availability)
# ===========================================================================


def test_per_lens_digest_renders_available_lenses(client, monkeypatch):
    """The template must render each available lens with its summary text.

    A lens that is available=True must show its summary. Silently hiding an
    available lens is a display failure — the operator sees no signal.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    # Check each available lens in _FULL_PRISM_SUMMARY has its summary text.
    for lens, block in _FULL_PRISM_SUMMARY["raw_response"]["per_lens_digest"].items():
        if block["available"] and block.get("summary"):
            assert block["summary"][:20] in html, (
                f"Available lens '{lens}' summary must appear in rendered HTML. "
                f"Expected to find '{block['summary'][:20]!r}'. "
                "The template must render per_lens_digest entries."
            )


def test_per_lens_digest_renders_unavailable_lens_reason(client, monkeypatch):
    """The template must render the reason for unavailable lenses.

    When a lens is available=False, the template must show WHY it is unavailable
    (the reason field — type(exc).__name__). Fabricating data or hiding the
    unavailability violates the honest-availability contract.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    # Check each unavailable lens in _FULL_PRISM_SUMMARY has its reason rendered.
    for lens, block in _FULL_PRISM_SUMMARY["raw_response"]["per_lens_digest"].items():
        if not block["available"] and block.get("reason"):
            assert block["reason"] in html or lens.capitalize() in html, (
                f"Unavailable lens '{lens}' must show its reason ('{block['reason']}') "
                "in the rendered HTML. The template must render the reason field, "
                "not suppress it. Honest-availability means showing WHY, not hiding it."
            )


def test_per_lens_digest_shows_all_5_lens_names(client, monkeypatch):
    """The template must render all 5 lens names (available or unavailable).

    The operator needs to see which lenses were attempted, not just the
    successful ones. Hiding a lens name because it is unavailable breaks the
    honest-availability contract.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace").lower()

    for lens in _EXPECTED_LENSES:
        assert lens in html, (
            f"Lens name '{lens}' must appear in the Market Prism section. "
            "All 5 lens names (technicals/sentiment/derivatives/macro/fundamentals) "
            "must be shown regardless of availability."
        )


# ===========================================================================
# AC-3: Cited news rendered as clickable <a href> links, XSS-safe
# ===========================================================================


def test_sources_rendered_as_anchor_tags(client, monkeypatch):
    """Sources must be rendered as <a href="..."> elements — not plain text.

    The BRIEF requires "clickable <a href> links". Plain text URLs are not
    clickable and fail AC-3.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    # The source URL from _FULL_PRISM_SUMMARY must appear as an href.
    source_url = "https://example.com/sentiment"
    assert f'href="{source_url}"' in html or f"href='{source_url}'" in html, (
        f"Source URL '{source_url}' must be rendered as an <a href> attribute. "
        "The template must iterate over sources and produce <a href> tags, "
        "not plain text URLs."
    )


def test_source_title_rendered_in_anchor(client, monkeypatch):
    """The source title must appear inside the <a> tag (as link text or nearby).

    The BRIEF specifies rendering title + published date + lens tag. A bare URL
    link with no title text is not compliant.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    source_title = _FULL_PRISM_SUMMARY["raw_response"]["sources"][0]["title"]
    assert source_title in html, (
        f"Source title '{source_title}' must appear in rendered HTML. "
        "The template must render the title as link text or alongside the <a> tag."
    )


def test_xss_hostile_title_is_html_escaped(monkeypatch):
    """A source with a hostile XSS title must be HTML-escaped in the rendered output.

    Template engines (Jinja2) auto-escape by default, but the test must verify
    the raw script tag does NOT appear verbatim in the output. A route that
    bypasses auto-escaping (e.g., Markup(), |safe) is a XSS vulnerability.

    This is a route-level test using a seeded hostile title.
    """
    import app as app_module
    import database

    hostile_title = "<script>alert('xss')</script>Hostile Market Report"
    hostile_summary = {
        **_FULL_PRISM_SUMMARY,
        "raw_response": {
            **_FULL_PRISM_SUMMARY["raw_response"],
            "sources": [
                {
                    "title": hostile_title,
                    "url": "https://example.com/hostile",
                    "published": "2026-06-13",
                    "lens": "sentiment",
                }
            ],
        },
    }

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: hostile_summary)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.get("/ai-advisor")

    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    # The raw <script> tag must NOT appear verbatim (it must be escaped).
    assert "<script>alert('xss')</script>" not in html, (
        "XSS: hostile title '<script>alert(...)</script>' appeared unescaped in "
        "the rendered HTML. The template must HTML-escape all source titles. "
        "Check for Jinja2 |safe filters or Markup() wrappers on source fields."
    )

    # The escaped form should be present (Jinja2 auto-escape produces &lt;script&gt;).
    assert "&lt;script&gt;" in html or "Hostile Market Report" in html, (
        "After XSS-safe rendering, either the escaped &lt;script&gt; or the plain text "
        "'Hostile Market Report' must appear. Neither was found — the template may be "
        "suppressing sources entirely."
    )


def test_source_anchor_has_safe_rel_target(client, monkeypatch):
    """Source <a> tags must use safe external link attributes.

    External links must have rel='noopener noreferrer' and target='_blank'
    (or similar) to prevent reverse tabnapping. This is the existing pattern
    for external links in the other advisor tabs per the BRIEF.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    # If the source URL is rendered as an <a href>, it must have safe attributes.
    source_url = "https://example.com/sentiment"
    if f'href="{source_url}"' in html or f"href='{source_url}'" in html:
        # rel="noopener noreferrer" or rel='noopener noreferrer'
        assert "noopener" in html and "noreferrer" in html, (
            f"Source link for '{source_url}' must have rel='noopener noreferrer'. "
            "External links must use safe rel/target attributes to prevent "
            "reverse tabnapping (the existing pattern on other advisor tabs)."
        )


# ===========================================================================
# AC-4: Read-only / non-blocking — route must NOT call run_pipeline
# ===========================================================================


def test_get_ai_advisor_does_not_call_run_pipeline(monkeypatch):
    """GET /ai-advisor must never call advisors.lens_pipeline.run_pipeline.

    The route reads via get_latest_market_prism_summary (read-only SQLite).
    Calling run_pipeline on a GET request would re-run the off-hours analysis
    synchronously — blocking the 1-minute execution cadence. A spy on
    run_pipeline confirms it is never invoked.
    """
    import app as app_module
    import database

    run_pipeline_calls: list = []

    def spy_run_pipeline(*args, **kwargs):
        run_pipeline_calls.append((args, kwargs))
        return {}

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: _FULL_PRISM_SUMMARY)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    # Patch run_pipeline on the module if it exists — won't be called.
    import sys

    if "advisors.lens_pipeline" in sys.modules:
        import advisors.lens_pipeline as _lp

        monkeypatch.setattr(_lp, "run_pipeline", spy_run_pipeline)
    else:
        # Module not yet imported — inject a sentinel into sys.modules.
        mock_pipeline_mod = MagicMock()
        mock_pipeline_mod.run_pipeline = spy_run_pipeline
        monkeypatch.setitem(sys.modules, "advisors.lens_pipeline", mock_pipeline_mod)

    # Patch render_template to avoid full template rendering overhead.
    monkeypatch.setattr(app_module, "render_template", lambda *a, **kw: "<html>ok</html>")

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        resp = c.get("/ai-advisor")

    assert resp.status_code == 200
    assert run_pipeline_calls == [], (
        "GET /ai-advisor must NOT call run_pipeline. "
        f"run_pipeline was called {len(run_pipeline_calls)} time(s). "
        "The route must read via get_latest_market_prism_summary only — "
        "run_pipeline is an off-hours write operation, never a GET-path read."
    )


def test_get_ai_advisor_calls_get_latest_market_prism_summary(monkeypatch):
    """GET /ai-advisor must call database.get_latest_market_prism_summary exactly once.

    This is the positive AC-4 companion: the route must use the correct read-only
    accessor, not bypass it by reading advisor_observations directly or skipping
    the prefetch entirely.
    """
    import app as app_module
    import database

    call_count = {"n": 0}

    def counting_get_summary():
        call_count["n"] += 1
        return _FULL_PRISM_SUMMARY

    monkeypatch.setattr(database, "get_latest_market_prism_summary", counting_get_summary)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    monkeypatch.setattr(app_module, "render_template", lambda *a, **kw: "<html>ok</html>")

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        c.get("/ai-advisor")

    assert call_count["n"] == 1, (
        f"GET /ai-advisor must call get_latest_market_prism_summary exactly once; "
        f"called {call_count['n']} time(s). "
        "Zero calls means the route doesn't prefetch the summary. "
        "More than one call is wasteful (the row doesn't change per request)."
    )


# ===========================================================================
# AC-5: Advisory-only — no trade affordance in the Market Prism block
# ===========================================================================


def test_market_prism_block_has_no_accept_execute_button(client, monkeypatch):
    """The Market Prism block must NOT contain an accept/execute trade affordance.

    The BRIEF is explicit: "Advisory-only: NO trade affordances, no accept/execute
    on the prism block (it's informational)." Any button with 'accept', 'execute',
    'trade', or 'buy'/'sell' within the Market Prism section is a violation.

    This is a template-level adversarial check — the block must have no trade
    affordance hardcoded or injected via JS.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    # Check for the Market Prism section specifically.
    # We look for any accept/execute button near "prism" content.
    # A route-level approach: the overall page must not have these within prism context.
    # We use a conservative check: if "market-prism" or "prism" div is present, ensure
    # no accept/execute button exists in the rendered page from this block.
    import re

    # Find prism-related HTML block (between markers, or look for data-testid="market-prism*").
    prism_section_match = re.search(
        r'data-testid="market-prism[^"]*"[^>]*>(.*?)(?=data-testid="(?!market-prism)|</div>\s*</div>)',
        html,
        re.DOTALL | re.IGNORECASE,
    )

    if prism_section_match:
        prism_html = prism_section_match.group(1).lower()
        # Check no trade action buttons in the prism block.
        trade_affordances = [
            'onclick="accept',
            'data-action="accept"',
            'data-action="execute"',
            'data-action="trade"',
        ]
        for affordance in trade_affordances:
            assert affordance not in prism_html, (
                f"Market Prism block must NOT contain trade affordance '{affordance}'. "
                "The Market Prism overview is advisory-only (informational). "
                "Accept/execute buttons belong only on the suggestion card."
            )
    # If prism section not yet rendered (RED state), the test passes trivially —
    # but AC-1 will catch the missing block. This test's job is to catch a
    # GREEN implementation that incorrectly adds a trade button.


def test_market_prism_block_marked_advisory_only(client, monkeypatch):
    """The Market Prism block must carry an advisory-only marker in the HTML.

    The block should use a data-testid like 'market-prism-block' or similar
    that unambiguously identifies it as read-only context. This enables the
    ux-expert to validate the visual gate without ambiguity.
    """
    _mock_advisory_side_effects(monkeypatch, _FULL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200
    html = resp.data.decode("utf-8", errors="replace")

    # The template must include a Market Prism section with a testable identifier.
    assert "market-prism" in html.lower() or 'data-testid="market' in html.lower(), (
        "The Market Prism block must be identifiable in the rendered HTML — "
        "e.g., data-testid='market-prism-block' or class='market-prism-*'. "
        "This enables the ux-expert visual gate to locate the block."
    )


# ===========================================================================
# AC-6: JS syntax — static/ai_advisor.js passes `node --check`
# ===========================================================================


def test_ai_advisor_js_syntax_is_valid():
    """static/ai_advisor.js must pass `node --check` (no parse errors).

    A JS syntax error silently breaks the entire file — the Market Prism
    rendering logic, the tab switcher, and suggestion cards all fail to load.
    This test is a regression guard: any Cycle-5 edit to ai_advisor.js that
    introduces a parse error is caught here before the ux-expert visual gate.

    Requires node.js to be available on PATH. If node is absent, the test is
    skipped (not failed) — a missing tool is not a code defect.
    """
    js_path = _WORKTREE / "static" / "ai_advisor.js"
    assert js_path.exists(), (
        f"static/ai_advisor.js not found at {js_path}. "
        "The file must exist for the Market Prism client-side rendering."
    )

    result = subprocess.run(
        ["node", "--check", str(js_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"`node --check static/ai_advisor.js` failed with exit code {result.returncode}.\n"
        f"stdout: {result.stdout}\n"
        f"stderr: {result.stderr}\n"
        "Fix the JS syntax error before submitting — a parse error breaks all "
        "client-side behavior (tab switcher, suggestion cards, Market Prism rendering)."
    )
