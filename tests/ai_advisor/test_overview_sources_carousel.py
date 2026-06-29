"""
RED tests — DE-SOURCES-CAROUSEL-001: Overview Market Prism Sources Carousel.

Replaces the vertical ``<ul class="prism-sources-list">`` with a bounded
horizontal carousel of clickable source cards (feature-plans/overview-sources-carousel.md).

Rendering approach: render the real ``ai_advisor.html`` template via the Flask
test client (``GET /ai-advisor``) with monkeypatched DB helpers supplying a
fabricated ``market_prism_summary``.  All analytics/network helpers are patched
to no-ops — the same pattern established in test_cycle5_market_prism_surface.py.

All fixtures are function-scoped — no shared mutable state.
No hardcoded producer-computed values; asserts structure/presence/format only.
"""

from __future__ import annotations

import pathlib
import re
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Worktree root — used for the CSS file-read tests (AC-7).
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Fabricated market_prism_summary fixtures (shape only — no producer values)
# ---------------------------------------------------------------------------

_RUN_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

# Two distinct article_corpus sources for AC-2 / AC-3 tests.
_SUMMARY_WITH_CORPUS = {
    "id": 99,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-29T03:05:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Test rationale for carousel.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": "SMA test ok.",
                "sources": [],
                "article_corpus": [
                    {
                        "url": "https://example.com/article-one",
                        "title": "Article One Title",
                        "published": "2026-06-28",
                    },
                    {
                        "url": "https://example.com/article-two",
                        "title": "Article Two Title",
                        "published": "2026-06-27",
                    },
                ],
            },
            "sentiment": {"available": False, "reason": "StubError", "sources": []},
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "macro": {"available": False, "reason": "StubError", "sources": []},
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
    },
}

# Three article_corpus sources for AC-1 (enough to prove scroll, not just display).
_SUMMARY_WITH_THREE_CORPUS = {
    "id": 100,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-29T03:05:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Three-source test.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": "Three sources test.",
                "sources": [],
                "article_corpus": [
                    {"url": "https://example.com/s1", "title": "S1", "published": "2026-06-28"},
                    {"url": "https://example.com/s2", "title": "S2", "published": "2026-06-27"},
                    {"url": "https://example.com/s3", "title": "S3", "published": "2026-06-26"},
                ],
            },
            "sentiment": {"available": False, "reason": "StubError", "sources": []},
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "macro": {"available": False, "reason": "StubError", "sources": []},
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
    },
}

# Mixed: one url source + one plain-string citation for AC-5.
_SUMMARY_MIXED = {
    "id": 101,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-29T03:05:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Mixed corpus test.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": "SMA ok.",
                "sources": ["Plain citation text from sources list"],
                "article_corpus": [
                    {
                        "url": "https://example.com/url-source",
                        "title": "URL Source Title",
                        "published": "2026-06-28",
                    }
                ],
            },
            "sentiment": {"available": False, "reason": "StubError", "sources": []},
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "macro": {"available": False, "reason": "StubError", "sources": []},
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
    },
}

# XSS in title: `<b>Inject</b>` must be entity-encoded, never rendered as live HTML.
_SUMMARY_XSS_TITLE = {
    "id": 102,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-29T03:05:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "XSS test.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": "XSS check.",
                "sources": [],
                "article_corpus": [
                    {
                        "url": "https://example.com/xss",
                        "title": "<b>Inject</b>",
                        "published": "2026-06-28",
                    }
                ],
            },
            "sentiment": {"available": False, "reason": "StubError", "sources": []},
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "macro": {"available": False, "reason": "StubError", "sources": []},
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
    },
}

# javascript: URL must NOT become a clickable link (security).
_SUMMARY_JS_URL = {
    "id": 103,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-29T03:05:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "JS URL security test.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": "JS URL check.",
                "sources": [],
                "article_corpus": [
                    {
                        "url": "javascript:alert(1)",
                        "title": "Hostile Source",
                        "published": "2026-06-28",
                    }
                ],
            },
            "sentiment": {"available": False, "reason": "StubError", "sources": []},
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "macro": {"available": False, "reason": "StubError", "sources": []},
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
    },
}

# Empty per_lens_digest (no sources at all) for AC-6.
_SUMMARY_NO_SOURCES = {
    "id": 104,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-29T03:05:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "No sources test.",
        "per_lens_digest": {
            "technicals": {"available": True, "summary": "No sources.", "sources": []},
            "sentiment": {"available": False, "reason": "StubError", "sources": []},
            "derivatives": {"available": False, "reason": "StubError", "sources": []},
            "macro": {"available": False, "reason": "StubError", "sources": []},
            "fundamentals": {"available": False, "reason": "StubError", "sources": []},
        },
    },
}


# ---------------------------------------------------------------------------
# Helper: patch all non-Prism route helpers to silent no-ops (same approach
# as test_cycle5_market_prism_surface.py::_mock_advisory_side_effects).
# ---------------------------------------------------------------------------


def _mock_route_helpers(monkeypatch, prism_summary):
    """Patch DB and analytics helpers so GET /ai-advisor responds quickly.

    Only ``market_prism_summary`` is fabricated per-test; everything else
    returns an empty/no-op value so the route can complete without network or
    disk I/O.
    """
    import app as app_module
    import database

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: prism_summary)

    # Sources accessor — return None (no SOURCES row) so the route doesn't
    # try to merge article_corpus from a separate DB row; our fixture already
    # embeds article_corpus inside per_lens_digest directly.
    monkeypatch.setattr(
        database,
        "get_latest_market_prism_sources_for_run",
        lambda run_id: None,
    )

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)

    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])


# ---------------------------------------------------------------------------
# Flask test client fixture (function-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Flask test client in TESTING mode.

    The ``_disable_auth_for_tests`` autouse fixture in ``tests/conftest.py``
    disables the auth gate, so no session cookie is required.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ===========================================================================
# AC-1: Carousel container replaces the vertical list
# ===========================================================================


def test_sources_carousel_container_replaces_vertical_list(client, monkeypatch):
    """GET /ai-advisor with article_corpus sources must render a carousel container,
    NOT the old vertical ``<ul class="prism-sources-list">``.

    A correct carousel implementation uses a horizontally-scrolling container
    (e.g. ``<div class="prism-sources-carousel">``).  Leaving the old ``<ul>``
    in place fails this test.  A carousel with a class name that doesn't contain
    "carousel" also fails — it must be identifiable from the class name.

    RED intent: the current template uses ``<ul class="prism-sources-list">``;
    this test fails until that block is replaced.
    """
    _mock_route_helpers(monkeypatch, _SUMMARY_WITH_THREE_CORPUS)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # Old vertical list must be gone.
    assert 'class="prism-sources-list"' not in html, (
        'The old ``<ul class="prism-sources-list">`` must be removed. '
        "Replace the vertical list with a horizontal carousel container."
    )
    assert "<ul" not in html.split('data-testid="prism-sources"')[-1].split("</div>")[0], (
        "No ``<ul>`` element should be present inside the prism-sources block. "
        "The container must be a flex row / horizontal scroller, not a list."
    )

    # New carousel container must be present — identified by its class name.
    assert "prism-sources-carousel" in html, (
        "The carousel container must carry the class 'prism-sources-carousel'. "
        "The current template renders a vertical list instead. "
        'Replace the ``<ul class="prism-sources-list">`` block with a '
        '``<div class="prism-sources-carousel">`` (or similar horizontal scroller).'
    )

    # data-testid hook must be on (or inside) the new container.
    assert 'data-testid="prism-sources"' in html, (
        'data-testid="prism-sources" must be present when sources exist (AC-8 companion).'
    )


# ===========================================================================
# AC-1 (CSS refinement): .prism-sources-carousel must have overflow-x + height cap
# ===========================================================================


def test_carousel_css_has_horizontal_scroll_and_height_cap():
    """The ``.prism-sources-carousel`` CSS rule must declare both:
    - ``overflow-x`` (value: auto or scroll) — enables horizontal scroll.
    - a height-bounding property (``height`` or ``max-height``) — prevents the
      carousel from growing vertically with more sources (the operator's complaint).

    This test reads the template file directly — no Flask render needed.

    RED intent: the current CSS has ``.prism-sources-list`` (vertical flex-column),
    no ``.prism-sources-carousel`` rule, no ``overflow-x``, and no height cap.
    """
    template_path = _WORKTREE / "templates" / "ai_advisor.html"
    assert template_path.exists(), f"Template not found at {template_path}"

    content = template_path.read_text(encoding="utf-8")

    # Locate the .prism-sources-carousel rule block.
    # We match from the selector up to the closing brace.
    carousel_rule_match = re.search(
        r"\.prism-sources-carousel\s*\{([^}]*)\}",
        content,
        re.DOTALL,
    )
    assert carousel_rule_match is not None, (
        "No ``.prism-sources-carousel { ... }`` CSS rule found in templates/ai_advisor.html. "
        "The implementer must add this rule to style the horizontal carousel container."
    )

    rule_body = carousel_rule_match.group(1)

    # overflow-x is required for horizontal scrolling.
    assert "overflow-x" in rule_body, (
        "The ``.prism-sources-carousel`` CSS rule must contain ``overflow-x`` "
        "(e.g. ``overflow-x: auto`` or ``overflow-x: scroll``) to enable horizontal "
        "scroll.  Found rule body:\n" + rule_body.strip()
    )

    # A height-bounding property is required so the carousel doesn't grow vertically.
    has_height_cap = "max-height" in rule_body or (
        # plain `height` but not preceded by `max-` (already checked) or `min-`
        re.search(r"(?<!max-)(?<!min-)height\s*:", rule_body) is not None
    )
    assert has_height_cap, (
        "The ``.prism-sources-carousel`` CSS rule must contain a height-bounding "
        "property (``max-height`` or ``height``) so the carousel stays single-row "
        "and doesn't expand the page vertically when many sources are present. "
        "Found rule body:\n" + rule_body.strip()
    )


# ===========================================================================
# AC-2: Each article_corpus source is an <a> card with correct attrs
# ===========================================================================


def test_each_article_corpus_source_is_an_anchor_card(client, monkeypatch):
    """Each ``article_corpus`` entry must render as an ``<a>`` element with:
    - ``href`` equal to the source URL.
    - ``target="_blank"`` (opens in a new tab).
    - ``rel="noopener noreferrer"`` (reverse-tabnabbing prevention).

    The fixture has 2 sources with distinct URLs; both must appear as anchors.

    RED intent: the current template renders an ``<a class="prism-source-link">``
    that is a NESTED link inside a ``<li>``, not a whole-card anchor.  This test
    is written against the whole-card-is-anchor requirement — the current nested
    structure may pass the href check but a redesign that wraps the whole card
    as ``<a>`` is what the plan requires.  We assert both href values AND the
    card-level attributes so a nested-link-only implementation is insufficient.
    """
    _mock_route_helpers(monkeypatch, _SUMMARY_WITH_CORPUS)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # Both URLs must appear as href values.
    assert 'href="https://example.com/article-one"' in html, (
        "The first article_corpus URL must appear as an href in the rendered HTML. "
        "Each source card must be (or contain) an ``<a href=url>`` element."
    )
    assert 'href="https://example.com/article-two"' in html, (
        "The second article_corpus URL must appear as an href in the rendered HTML."
    )

    # target="_blank" and rel="noopener noreferrer" are security requirements.
    assert 'target="_blank"' in html, (
        'target="_blank" must be present on each source anchor (AC-2). '
        "The current template may be missing this attribute on the new carousel cards."
    )
    assert 'rel="noopener noreferrer"' in html, (
        'rel="noopener noreferrer" must be present on each source anchor (AC-2). '
        "This prevents reverse-tabnabbing on target=_blank links."
    )

    # The whole card must use <a> — assert the carousel container class appears
    # alongside an anchor (not only inside a <li> as it was before).
    assert "prism-source-card" in html, (
        "Source cards must carry class 'prism-source-card'. "
        "The current template uses 'prism-source-link' inside a list item — "
        "refactor so the whole card is wrapped in the anchor."
    )


# ===========================================================================
# AC-3: Card fields present and HTML-escaped (no | safe)
# ===========================================================================


def test_card_fields_present_and_html_escaped(client, monkeypatch):
    """Source card fields (title, published, lens) must appear in the HTML,
    and the title must be HTML-escaped — not injected as raw markup.

    The ``<b>Inject</b>`` title must appear as the entity-encoded string
    ``&lt;b&gt;Inject&lt;/b&gt;`` (or equivalent Jinja auto-escape form),
    NOT as a live ``<b>`` tag.  Any use of ``| safe`` on the title would
    cause the raw tag to appear, failing this test.

    RED intent: the current template uses ``{{ _src.get('title', 'Untitled') | e }}``
    which IS escaped — but this test verifies the carousel refactor preserves that.
    The carousel must keep ``| e`` (or rely on Jinja auto-escape) on every field.
    """
    _mock_route_helpers(monkeypatch, _SUMMARY_XSS_TITLE)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # The raw <b> tag must NOT appear as live HTML in the sources block.
    # If the title were rendered with | safe, we'd see <b>Inject</b> in the output.
    assert "<b>Inject</b>" not in html, (
        "The title '<b>Inject</b>' must be HTML-escaped, not injected as live markup. "
        "Do not use ``| safe`` on any source card field.  Use ``| e`` or Jinja "
        "auto-escaping so ``<b>`` becomes ``&lt;b&gt;``."
    )

    # The word "Inject" must still appear (entity-encoded form is fine).
    assert "Inject" in html, (
        "The title text 'Inject' must appear in the rendered HTML (entity-encoded). "
        "If it's missing entirely, the card is not rendering the title field."
    )

    # Re-use the two-source fixture to check published and lens presence.
    _mock_route_helpers(monkeypatch, _SUMMARY_WITH_CORPUS)
    resp2 = client.get("/ai-advisor")
    html2 = resp2.data.decode("utf-8", errors="replace")

    assert "Article One Title" in html2, (
        "The source title 'Article One Title' must appear in the carousel card. "
        "The card must render the 'title' field from the article_corpus entry."
    )
    assert "2026-06-28" in html2, (
        "The published date '2026-06-28' must appear in the carousel card. "
        "The card must render the 'published' field from the article_corpus entry."
    )
    assert "technicals" in html2, (
        "The lens tag 'technicals' must appear in the carousel card. "
        "The card must render the '_src.lens' field (the per_lens_digest key)."
    )


# ===========================================================================
# AC-5: Plain-string citation entry renders as NON-<a> card
# ===========================================================================


def test_plain_citation_entry_renders_as_non_anchor_card(client, monkeypatch):
    """A plain-string ``sources`` entry (no URL) must render as a non-clickable
    element — NOT as ``<a href="#">`` or any anchor.

    The template's ``_all_sources`` aggregation appends plain-string sources as
    ``{'citation': _src_str, 'lens': _sln}`` (no 'url' key).  These must render
    as citation cards (e.g. ``<div>``, ``<span>``), not as empty/broken links.

    RED intent: the current template wraps citation-only entries in a ``<span>``,
    which is correct.  The carousel refactor must preserve this — no citation
    entry may ever produce ``<a href=``; only http(s) URL entries may be anchors.
    """
    _mock_route_helpers(monkeypatch, _SUMMARY_MIXED)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # The plain citation text must appear in the HTML.
    assert "Plain citation text from sources list" in html, (
        "The plain-string citation entry 'Plain citation text from sources list' "
        "must appear in the rendered HTML.  The carousel must include citation-only "
        "entries as non-clickable cards."
    )

    # The url source must be a link (regression: make sure mixed rendering works).
    assert 'href="https://example.com/url-source"' in html, (
        "The url-bearing article_corpus entry must still render as an anchor "
        "in the mixed (citation + url) scenario."
    )

    # The citation text must NOT appear inside an <a href> element.
    # Strategy: find every <a href=... block and confirm the citation text
    # does not appear between the opening <a and its closing </a>.
    anchor_pattern = re.compile(r"<a\s[^>]*href[^>]*>.*?</a>", re.DOTALL | re.IGNORECASE)
    for match in anchor_pattern.finditer(html):
        anchor_text = match.group(0)
        assert "Plain citation text from sources list" not in anchor_text, (
            "The plain-string citation entry must NOT be wrapped in an ``<a>`` element. "
            f"Found it inside an anchor: {anchor_text[:200]!r}"
        )


# ===========================================================================
# Security: javascript: URL must not produce a clickable link
# ===========================================================================


def test_javascript_url_does_not_become_clickable_link(client, monkeypatch):
    """An ``article_corpus`` entry with ``url = "javascript:alert(1)"`` must NOT
    render as ``<a href="javascript:``  — the hostile URL must be rejected by
    the template's http(s) guard and fall through to a non-clickable citation card.

    RED intent: the current template checks only ``{% if _src.get('url') %}`` —
    any truthy URL (including ``javascript:``) becomes an anchor.  The carousel
    refactor must tighten this to ``_src.url.startswith('http')`` (or equivalent)
    so only http/https URLs produce clickable links.
    """
    _mock_route_helpers(monkeypatch, _SUMMARY_JS_URL)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # The javascript: protocol must not appear as an href value.
    assert 'href="javascript:' not in html, (
        "A source with url='javascript:alert(1)' must NOT render as "
        '``<a href="javascript:...">``.  Only http/https URLs may become anchors. '
        "Add a Jinja guard: ``{% if _src.url and _src.url.startswith('http') %}``."
    )

    # Belt-and-suspenders: the raw string must not appear inside any href attr.
    assert "javascript:alert" not in html, (
        "The hostile URL 'javascript:alert(1)' must not appear anywhere in the "
        "rendered HTML as an href value.  The template must reject non-http URLs."
    )


# ===========================================================================
# AC-6: Empty _all_sources → no carousel, no header rendered
# ===========================================================================


def test_empty_sources_renders_no_carousel_and_no_header(client, monkeypatch):
    """When all per_lens_digest entries have empty ``sources: []`` and no
    ``article_corpus`` key, ``_all_sources`` stays empty.  The template's
    ``{% if _all_sources %}`` guard must prevent ANY carousel or header from
    rendering — no empty shell, no "Sources" heading.

    RED intent: this behaviour exists in the current template via the
    ``{% if _all_sources %}`` guard.  The carousel refactor must preserve it —
    i.e. the guard must not be removed or weakened.
    """
    _mock_route_helpers(monkeypatch, _SUMMARY_NO_SOURCES)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # The prism-sources data-testid must be absent when there are no sources.
    assert 'data-testid="prism-sources"' not in html, (
        'When _all_sources is empty, data-testid="prism-sources" must NOT appear '
        "in the rendered HTML.  The carousel and its header must be fully suppressed "
        "by the {% if _all_sources %} guard."
    )

    # The carousel container class must also be absent.
    assert "prism-sources-carousel" not in html, (
        "When _all_sources is empty, no carousel container must be rendered. "
        "The carousel class 'prism-sources-carousel' must not appear in the HTML."
    )


# ===========================================================================
# AC-7: Design tokens used — no raw hex color literals in carousel CSS
# ===========================================================================


def test_carousel_css_uses_design_tokens_not_raw_hex():
    """The CSS rules for ``.prism-sources-carousel`` and ``.prism-source-card``
    must use ``var(--...)`` tokens for all color values, never raw hex literals.

    This test reads ``templates/ai_advisor.html`` directly and extracts the
    ``<style>`` block to inspect the carousel/card CSS rules.

    Design-contract assertion (not a computed-value assertion): the test checks
    that the rule *refers* to existing CSS custom properties, not that any
    specific rendered color resolves to a particular RGB value.

    RED intent: the refactored CSS does not yet exist.  Once added, it must
    follow the design-system token convention already used throughout the template
    (e.g. ``var(--studio-ink-dim)``, ``var(--studio-border)``).
    """
    template_path = _WORKTREE / "templates" / "ai_advisor.html"
    assert template_path.exists(), f"Template not found at {template_path}"

    content = template_path.read_text(encoding="utf-8")

    # Extract the <style> block for inspection.
    style_match = re.search(r"<style>(.*?)</style>", content, re.DOTALL | re.IGNORECASE)
    assert style_match is not None, "No <style> block found in templates/ai_advisor.html."
    style_block = style_match.group(1)

    # Locate .prism-sources-carousel rule (must exist — AC-7 depends on it).
    carousel_rule_match = re.search(
        r"\.prism-sources-carousel\s*\{([^}]*)\}",
        style_block,
        re.DOTALL,
    )
    assert carousel_rule_match is not None, (
        "No ``.prism-sources-carousel { ... }`` rule found in the <style> block. "
        "The implementer must add this CSS rule for the carousel container."
    )

    # Locate .prism-source-card rule (must also exist).
    card_rule_match = re.search(
        r"\.prism-source-card\s*\{([^}]*)\}",
        style_block,
        re.DOTALL,
    )
    assert card_rule_match is not None, (
        "No ``.prism-source-card { ... }`` rule found in the <style> block. "
        "The implementer must add this CSS rule for the individual source cards."
    )

    # Combine the two rule bodies for the hex-color scan.
    combined_rules = carousel_rule_match.group(1) + card_rule_match.group(1)

    # Detect raw hex color literals.  Exclude CSS ID selectors (rare but possible)
    # and data URIs by requiring the # to follow a colon, space, or semicolon
    # (i.e. it's a property value, not a selector or comment fragment).
    hex_color_pattern = re.compile(r"(?<=[:,\s])#[0-9a-fA-F]{3,8}\b")
    hex_hits = hex_color_pattern.findall(combined_rules)
    assert not hex_hits, (
        f"Raw hex color literals found in carousel/card CSS rules: {hex_hits}. "
        "Use ``var(--studio-...)`` design tokens instead (e.g. ``var(--studio-border)``, "
        "``var(--studio-ink-dim)``).  No new raw colors — reuse existing token vars."
    )

    # Assert at least one var(-- token reference appears in the combined rules
    # (the implementer must actually use the design system, not just avoid hex).
    assert "var(--" in combined_rules, (
        "The carousel/card CSS rules must reference at least one CSS custom property "
        "via ``var(--...)``.  Reuse the existing design tokens already in the template "
        "(e.g. ``var(--studio-border)``, ``var(--studio-accent)``)."
    )


# ===========================================================================
# AC-8: data-testid="prism-sources" preserved on carousel container
# ===========================================================================


def test_prism_sources_data_testid_preserved(client, monkeypatch):
    """When sources are present, ``data-testid="prism-sources"`` must appear
    exactly once in the rendered HTML.

    This is the regression guard: the refactor must not rename or remove the
    test hook that existing tests (and Playwright specs) rely on.

    RED intent: the current template places ``data-testid="prism-sources"`` on
    the outer ``<div>`` wrapping the ``<ul>``.  The carousel refactor must keep
    this attribute on the new carousel container (or its outer wrapper).
    """
    _mock_route_helpers(monkeypatch, _SUMMARY_WITH_CORPUS)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    occurrences = html.count('data-testid="prism-sources"')
    assert occurrences == 1, (
        f'data-testid="prism-sources" must appear exactly once in the rendered HTML '
        f"when sources are present.  Found {occurrences} occurrence(s). "
        "The carousel refactor must preserve this hook on the container element."
    )
