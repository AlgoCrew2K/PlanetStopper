"""
RED tests — per-lens Market Prism sources carousels.

Feature plan: feature-plans/prism-sources-per-lens-carousels.md
Branch: feat/prism-sources-per-lens-carousels

Replaces the single flat `.prism-sources-carousel` strip with one carousel
per prism lens (technicals, sentiment, derivatives, macro, fundamentals),
labeled with the lens name, containing only that lens's sources.

Rendering approach: render the real ``ai_advisor.html`` template via the Flask
test client (``GET /ai-advisor``) seeded via monkeypatched DB helpers.
The MARKET_PRISM row has no article_corpus (mirrors real council output);
a separate MARKET_PRISM_SOURCES row carries article_corpus per lens and is
merged by the route (``ai_advisor_tab()``, app.py:3718–3754) before render.
This exercises the real route merge path — not a bypass.

Section-parsing helper ``_lens_section(html, lens_name)`` extracts the HTML
for a specific per-lens container using ``data-testid="prism-sources-lens-X"``
anchors.  At HEAD (flat carousel) these anchors do not exist, so every
section-based assertion fails with an empty-string match → genuinely RED.

All fixtures are function-scoped.  No hardcoded producer-computed values;
every assertion is structural/shape/escaping only.
"""

from __future__ import annotations

import re
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Shared fixture constants
# ---------------------------------------------------------------------------

_RUN_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"

# The URL shared between technicals and sentiment lenses (AC-3 multi-lens dup).
_SHARED_URL = "https://shared.example.com/article-shared-across-two-lenses"

# A URL that should be confined to the technicals lens only (non-bleed guard).
_TECH_ONLY_URL = "https://tech.example.com/technicals-only-article"

# The plain citation string that belongs to the macro lens.
_MACRO_CITATION = "FRED: US CPI YoY — citation-only string"

# ---------------------------------------------------------------------------
# MARKET_PRISM row — no article_corpus (mirrors real council output).
# article_corpus is supplied separately via the SOURCES row and merged by
# ai_advisor_tab() before render.
# ---------------------------------------------------------------------------

_MARKET_PRISM_ROW = {
    "id": 200,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-29T03:05:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Per-lens carousel test.",
        "per_lens_digest": {
            # technicals: available, no article_corpus yet — injected by SOURCES merge
            "technicals": {
                "available": True,
                "summary": "SMA above 50-day.",
                "sources": [],
            },
            # sentiment: available, no article_corpus yet — injected by SOURCES merge
            "sentiment": {
                "available": True,
                "summary": "Neutral tone.",
                "sources": [],
            },
            # derivatives: unavailable and no sources → no carousel expected (AC-2)
            "derivatives": {
                "available": False,
                "reason": "StubError",
                "sources": [],
            },
            # macro: available, has a plain citation (no article_corpus from SOURCES
            # for this lens) → plain-citation carousel expected
            "macro": {
                "available": True,
                "summary": "CPI inline.",
                "sources": [_MACRO_CITATION],
            },
            # fundamentals: available but zero sources (both arrays empty) → no carousel (AC-2)
            "fundamentals": {
                "available": True,
                "summary": "EPS data.",
                "sources": [],
            },
        },
    },
}

# ---------------------------------------------------------------------------
# MARKET_PRISM_SOURCES row — carries article_corpus for technicals and
# sentiment only.  The route merges this into _MARKET_PRISM_ROW's
# per_lens_digest before rendering.
#
# After merge:
#   technicals.article_corpus = [_SHARED_URL, _TECH_ONLY_URL]
#   sentiment.article_corpus  = [_SHARED_URL]
#   macro: unchanged (no entry here) — sources=[_MACRO_CITATION] stays
#   derivatives/fundamentals: unchanged — no carousels
#
# Non-empty lenses (have >=1 source after merge): technicals, sentiment, macro → 3
# Empty lenses (zero sources): derivatives, fundamentals → 0
# ---------------------------------------------------------------------------

_SOURCES_ROW = {
    "id": 201,
    "advisor_role": "MARKET_PRISM_SOURCES",
    "subject_id": "global",
    "verdict": None,
    "created_at": "2026-06-29T03:06:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "per_lens_digest": {
            "technicals": {
                "article_corpus": [
                    {
                        "url": _SHARED_URL,
                        "title": "Shared Article Across Two Lenses",
                        "published": "2026-06-29",
                    },
                    {
                        "url": _TECH_ONLY_URL,
                        "title": "Technicals Only Article",
                        "published": "2026-06-28",
                    },
                ]
            },
            "sentiment": {
                "article_corpus": [
                    {
                        "url": _SHARED_URL,
                        "title": "Shared Article Across Two Lenses",
                        "published": "2026-06-29",
                    },
                ]
            },
        },
    },
}

# ---------------------------------------------------------------------------
# SOURCES row with XSS title in technicals article_corpus.
# Used by test_10 to verify the per-lens restructure preserves | e escaping.
# ---------------------------------------------------------------------------

_SOURCES_ROW_XSS = {
    "id": 202,
    "advisor_role": "MARKET_PRISM_SOURCES",
    "subject_id": "global",
    "verdict": None,
    "created_at": "2026-06-29T03:06:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "per_lens_digest": {
            "technicals": {
                "article_corpus": [
                    {
                        "url": "https://example.com/xss-test",
                        "title": "<b>Inject</b>",
                        "published": "2026-06-29",
                    },
                ]
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Flask test client fixture (function-scoped)
# ---------------------------------------------------------------------------


@pytest.fixture()
def client():
    """Flask test client in TESTING mode.

    The ``_disable_auth_for_tests`` autouse fixture in ``tests/conftest.py``
    disables the auth gate so no session cookie is required.
    """
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Helper: patch all non-Prism route helpers to lightweight no-ops.
# ``get_latest_market_prism_sources_for_run`` is caller-supplied so each test
# can control whether the SOURCES merge path fires.
# ---------------------------------------------------------------------------


def _patch_route(monkeypatch, prism_row, sources_row):
    """Patch DB and analytics helpers so GET /ai-advisor returns quickly.

    ``prism_row`` is returned by ``get_latest_market_prism_summary``.
    ``sources_row`` is returned by ``get_latest_market_prism_sources_for_run``
    (pass ``None`` to skip the article_corpus merge path).
    """
    import app as app_module
    import database

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: prism_row)
    monkeypatch.setattr(
        database,
        "get_latest_market_prism_sources_for_run",
        lambda run_id: sources_row,
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
# Helper: extract the HTML fragment for a single per-lens carousel section.
#
# Searches for ``data-testid="prism-sources-lens-{lens_name}"`` in the HTML
# and returns everything from that anchor up to (but not including) the next
# per-lens anchor or the end of string.
#
# At HEAD (flat carousel): no per-lens anchors exist → returns "".
# After per-lens implementation: returns the HTML fragment for that lens.
# ---------------------------------------------------------------------------

_LENS_TESTID_PREFIX = 'data-testid="prism-sources-lens-'
_ALL_LENS_NAMES = ("technicals", "sentiment", "derivatives", "macro", "fundamentals")


def _lens_section(html: str, lens_name: str) -> str:
    """Return the HTML fragment for ``lens_name``'s per-lens carousel section.

    Returns an empty string when no per-lens section for ``lens_name`` exists
    (e.g. at HEAD where only a single flat carousel is rendered, or when the
    lens has no sources and is correctly suppressed).
    """
    marker = f'{_LENS_TESTID_PREFIX}{lens_name}"'
    if marker not in html:
        return ""
    start = html.index(marker)
    # Find the start of the NEXT per-lens section (any other lens), if any.
    other_markers = [
        f'{_LENS_TESTID_PREFIX}{n}"'
        for n in _ALL_LENS_NAMES
        if n != lens_name and f'{_LENS_TESTID_PREFIX}{n}"' in html[start + len(marker):]
    ]
    if other_markers:
        # Pick the nearest one.
        end = min(
            html.index(m, start + len(marker)) for m in other_markers
        )
    else:
        end = len(html)
    return html[start:end]


# ===========================================================================
# Test 1 — AC-1 / AC-4: One carousel container per non-empty lens
# ===========================================================================


def test_renders_one_carousel_container_per_non_empty_lens(client, monkeypatch):
    """GET /ai-advisor with the multi-lens fixture must render exactly 3
    ``.prism-sources-carousel`` container elements — one each for technicals,
    sentiment, and macro (the lenses that have >=1 source after the SOURCES merge).

    derivatives and fundamentals have zero sources and must NOT produce a carousel.

    RED intent: at HEAD, the template renders ONE global ``.prism-sources-carousel``
    containing all sources flat.  ``html.count('class="prism-sources-carousel"')``
    returns 1 at HEAD.  This test expects 3 → FAIL.

    Adversarial gap closed: a lazy "wrap everything in 5 lens divs" implementation
    that still uses one flat carousel inside them would return 1 → still fails.
    A correct per-lens implementation produces exactly 3.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    count = html.count('class="prism-sources-carousel"')
    assert count == 3, (
        f"Expected exactly 3 ``.prism-sources-carousel`` elements (technicals, sentiment, macro); "
        f"found {count}.  At HEAD only 1 flat carousel is rendered.  "
        "The template must emit one carousel per non-empty lens, not one global carousel."
    )


# ===========================================================================
# Test 2 — AC-3: Shared URL appears in BOTH lens sections (multi-lens dup)
# ===========================================================================


def test_shared_url_present_in_technicals_and_sentiment_sections(client, monkeypatch):
    """The shared URL (in both technicals and sentiment article_corpus) must appear
    inside BOTH the technicals per-lens section AND the sentiment per-lens section.

    This is AC-3: a source cited by N lenses appears once per lens carousel.

    The test uses ``_lens_section()`` to extract each lens's HTML fragment by its
    ``data-testid="prism-sources-lens-X"`` anchor.  At HEAD, no per-lens anchors
    exist → both sections are empty strings → assertions fail → genuinely RED.

    Note: at HEAD the flat carousel DOES include the shared URL twice (once from
    each lens in the flat aggregation), so a raw ``html.count(url)`` would be 2
    and would NOT discriminate flat vs per-lens.  Section parsing is required.

    Adversarial gap closed: a dedup-across-lenses implementation would produce the
    shared URL in only ONE section → sentiment assertion fails.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    tech_section = _lens_section(html, "technicals")
    sent_section = _lens_section(html, "sentiment")

    assert _SHARED_URL in tech_section, (
        "The shared URL must appear inside the technicals per-lens carousel section.  "
        f"At HEAD, no data-testid='prism-sources-lens-technicals' exists → section=''.  "
        "After implementation, the technicals carousel must contain this URL.  "
        f"URL: {_SHARED_URL!r}"
    )
    assert _SHARED_URL in sent_section, (
        "The shared URL must appear inside the sentiment per-lens carousel section (AC-3).  "
        f"A URL present in two lenses' article_corpus must render in each lens's carousel.  "
        f"URL: {_SHARED_URL!r}"
    )


# ===========================================================================
# Test 3 — Non-bleed guard: unique URL confined to its own lens section
# ===========================================================================


def test_unique_url_confined_to_tech_section_not_in_sentiment(client, monkeypatch):
    """The tech-only URL (in technicals article_corpus only, not in sentiment)
    must appear inside the technicals section and be ABSENT from the sentiment section.

    RED intent: at HEAD, no per-lens sections exist → tech section is empty →
    ``_TECH_ONLY_URL in ""`` is False → the technicals assertion fails → genuinely RED.

    GUARD: after correct implementation, confirms URL confinement.  A broken impl
    that copies all sources to every lens carousel would have tech-only in ALL sections
    → sentiment assertion fails.

    Adversarial gap closed: "copy all to every lens" lazy impl.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    tech_section = _lens_section(html, "technicals")
    sent_section = _lens_section(html, "sentiment")

    assert _TECH_ONLY_URL in tech_section, (
        "The tech-only URL must appear inside the technicals per-lens section.  "
        f"At HEAD, no technicals section exists → this fails (section is '').  "
        f"URL: {_TECH_ONLY_URL!r}"
    )
    assert _TECH_ONLY_URL not in sent_section, (
        "The tech-only URL must NOT appear inside the sentiment per-lens section.  "
        "A broken implementation that copies all sources to every carousel would fail here.  "
        f"URL: {_TECH_ONLY_URL!r}"
    )


# ===========================================================================
# Test 4 — AC-5: Per-card lens tag removed from rendered HTML
# ===========================================================================


def test_per_card_lens_tag_absent_from_source_cards(client, monkeypatch):
    """The ``.prism-source-lens-tag`` class must be absent from the rendered HTML.

    AC-5: the per-card lens tag (showing which lens each card belongs to) is
    redundant once each card lives inside its own lens-labeled carousel group.
    The implementer must remove the ``<span class="prism-source-lens-tag">``
    markup from BOTH card variants (anchor card and citation card).

    RED intent: at HEAD, every source card includes a
    ``<span class="prism-source-lens-tag">{{ _src.get('lens') | e }}</span>``
    → the class IS present in the rendered HTML → this assertion fails → RED.

    Adversarial gap closed: an implementer who adds per-lens groups but forgets
    to remove the redundant per-card tag.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    assert "prism-source-lens-tag" not in html, (
        "The CSS class 'prism-source-lens-tag' must be absent from the rendered HTML.  "
        "AC-5: per-card lens tags are removed — the lens is identified by the carousel "
        "group label, not a tag on each card.  "
        "Remove <span class='prism-source-lens-tag'> from BOTH card variants in the template."
    )


# ===========================================================================
# Test 5 — AC-1 / AC-2: Per-lens data-testid containers, empty lenses suppressed
# ===========================================================================


def test_each_non_empty_lens_has_testid_empty_lenses_have_none(client, monkeypatch):
    """Non-empty lenses (technicals, sentiment, macro) must have a
    ``data-testid="prism-sources-lens-{name}"`` container.  Empty lenses
    (derivatives, fundamentals) must produce NO such container.

    This ``data-testid`` is the structural anchor used by downstream tests and
    Playwright specs to locate per-lens sections.  Implementer MUST add it.

    RED intent: at HEAD, no ``data-testid="prism-sources-lens-X"`` exists for
    any lens → all three "present" assertions fail → genuinely RED.

    Adversarial gaps closed:
    - All lenses rendered even when empty: derivatives/fundamentals testids would
      appear → the "absent" assertions fail.
    - No per-lens grouping at all: present assertions fail.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # Non-empty lenses → testid MUST be present.
    for lens in ("technicals", "sentiment", "macro"):
        assert f'data-testid="prism-sources-lens-{lens}"' in html, (
            f"data-testid='prism-sources-lens-{lens}' must be present — "
            f"this lens has >=1 source and must render a per-lens carousel.  "
            f"At HEAD this testid does not exist → assertion fails."
        )

    # Empty lenses → testid must be ABSENT (AC-2: no carousel for zero-source lens).
    for lens in ("derivatives", "fundamentals"):
        assert f'data-testid="prism-sources-lens-{lens}"' not in html, (
            f"data-testid='prism-sources-lens-{lens}' must NOT appear — "
            f"this lens has zero sources and must be suppressed (AC-2).  "
            f"An impl that renders all 5 lenses regardless of source count fails here."
        )


# ===========================================================================
# Test 6 — AC-8: Canonical ordering (technicals → sentiment → macro)
# ===========================================================================


def test_lens_carousels_in_canonical_ordering(client, monkeypatch):
    """The rendered lens carousels must appear in the canonical order:
    technicals, sentiment, derivatives, macro, fundamentals (AC-8).

    With the 3-lens fixture (technicals, sentiment, macro rendered), the
    relative order of their ``data-testid`` anchors in the HTML must be:
        technicals position < sentiment position < macro position.

    RED intent: at HEAD, no per-lens testids exist → all positions are -1 →
    ordering assertions are vacuous; but the preliminary "positions must be
    non-negative" assertions catch this → genuinely RED.

    Adversarial gap closed: an implementation that iterates ``per_lens_digest``
    dict keys (insertion-order dependent, not guaranteed canonical) instead of
    the fixed ``_lens_names`` list at template line 1025.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    tech_pos = html.find('data-testid="prism-sources-lens-technicals"')
    sent_pos = html.find('data-testid="prism-sources-lens-sentiment"')
    macro_pos = html.find('data-testid="prism-sources-lens-macro"')

    # Preliminary: all three must exist before ordering can be checked.
    # At HEAD all are -1 → these assertions catch that state (RED).
    assert tech_pos >= 0, (
        "data-testid='prism-sources-lens-technicals' not found — "
        "ordering cannot be verified until the per-lens containers exist.  "
        "At HEAD this testid is absent → position is -1 → FAIL (RED)."
    )
    assert sent_pos >= 0, (
        "data-testid='prism-sources-lens-sentiment' not found."
    )
    assert macro_pos >= 0, (
        "data-testid='prism-sources-lens-macro' not found."
    )

    # Ordering check.
    assert tech_pos < sent_pos, (
        f"technicals carousel (pos={tech_pos}) must appear before sentiment "
        f"carousel (pos={sent_pos}) in the HTML (AC-8 canonical ordering)."
    )
    assert sent_pos < macro_pos, (
        f"sentiment carousel (pos={sent_pos}) must appear before macro "
        f"carousel (pos={macro_pos}) in the HTML (AC-8 canonical ordering)."
    )


# ===========================================================================
# Test 7 — AC-6 GUARD: No MARKET_PRISM row → honest empty-state, no carousels
# ===========================================================================


def test_empty_state_when_no_market_prism_row(client, monkeypatch):
    """When ``market_prism_summary`` is None (no nightly run yet), the template
    must render the informative empty-state element, NOT five empty carousels.

    This is AC-6 applied to the no-row case.  The outer
    ``{% if market_prism_summary %}`` guard keeps this working; the test pins
    that the per-lens restructure does not accidentally remove or bypass it.

    GUARD: likely GREEN at HEAD (existing guard unchanged), but a broken impl
    that unconditionally emits 5 carousel shells would fail here.
    """
    _patch_route(monkeypatch, None, None)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # Honest empty-state must be present.
    assert 'data-testid="prism-empty-state"' in html, (
        "When market_prism_summary is None, the honest empty-state element "
        "(data-testid='prism-empty-state') must render.  "
        "The per-lens restructure must not remove the outer "
        "{% if market_prism_summary %} guard."
    )

    # No carousel element must appear (not even an empty one).
    assert 'class="prism-sources-carousel"' not in html, (
        "When market_prism_summary is None, no .prism-sources-carousel element "
        "must appear in the HTML.  An impl that renders empty carousel shells "
        "for all 5 lenses when there is no row fails here."
    )


# ===========================================================================
# Test 8 — AC-1: Lens name appears as visible text label in its section
# ===========================================================================


def test_lens_name_as_visible_text_label_in_section(client, monkeypatch):
    """Each non-empty lens carousel must have a visible text label showing the
    lens name (AC-1: 'each labeled with the lens's display name').

    The label text must appear as CONTENT between HTML tags (not only as an
    attribute value like ``data-testid="..."``) so it is visible to users.
    The test matches ``>[Tt]echnicals<`` (case-insensitive first char) to accept
    both ``<div>Technicals</div>`` and ``<div>technicals</div>`` as valid.

    RED intent: at HEAD, no per-lens section exists → ``_lens_section()`` returns
    '' → regex search finds nothing → assertions fail → genuinely RED.

    Adversarial gap closed: an implementer who adds the data-testid container but
    forgets to include any visible label text for the user.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    for lens_name in ("technicals", "sentiment", "macro"):
        section = _lens_section(html, lens_name)
        # Pattern: lens name (any case for first char) between a closing and opening tag.
        pattern = re.compile(
            r">" + re.escape(lens_name[0]).upper() + r"|" + re.escape(lens_name[0]) + re.escape(lens_name[1:]) + r"<",
            re.IGNORECASE,
        )
        # Simpler: just look for the lens name as text between > and <
        # Matches ">technicals<" or ">Technicals<" or similar.
        text_label_pattern = re.compile(
            r">" + r"\s*" + re.escape(lens_name) + r"\s*<",
            re.IGNORECASE,
        )
        assert text_label_pattern.search(section) is not None, (
            f"Lens name '{lens_name}' must appear as visible text content between HTML tags "
            f"inside its per-lens carousel section (AC-1: 'labeled with the lens's display name').  "
            f"At HEAD, no per-lens section exists → section is '' → label not found → FAIL.  "
            f"After implementation, add a label element e.g. "
            f"<div class='prism-lens-carousel-label'>{lens_name}</div> inside the section.  "
            f"Pattern searched: >{lens_name}< (case-insensitive) in the section HTML."
        )


# ===========================================================================
# Test 9 — AC-4: Macro plain-citation string rendered in the macro section
# ===========================================================================


def test_citation_string_rendered_in_macro_carousel_section(client, monkeypatch):
    """The macro lens's plain-string citation (from ``sources``  array, not
    ``article_corpus``) must appear inside the macro per-lens carousel section.

    AC-4: ``.prism-source-card--citation`` variant must be preserved for plain
    citations.  Test 1 proves the carousel EXISTS; this test proves its content
    (the citation text) survived the per-lens restructure.

    RED intent: at HEAD, no per-lens macro section exists → ``_lens_section()``
    returns '' → citation text not in '' → FAIL → genuinely RED.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    macro_section = _lens_section(html, "macro")

    assert _MACRO_CITATION in macro_section, (
        f"The macro plain-citation string must appear inside the macro per-lens section.  "
        f"At HEAD, no macro section exists → section is '' → citation absent → FAIL.  "
        f"Citation text: {_MACRO_CITATION!r}"
    )


# ===========================================================================
# Test 10 — Security GUARD: HTML-special chars in title are escaped, not injected
# ===========================================================================


def test_html_special_chars_in_title_escaped_in_per_lens_carousel(client, monkeypatch):
    """An article_corpus entry whose title contains ``<b>Inject</b>`` must NOT
    render the raw ``<b>`` tag in the HTML output.  The title must be
    HTML-escaped so that ``<b>`` becomes ``&lt;b&gt;``.

    The per-lens template restructure must preserve the existing ``| e``
    (or Jinja auto-escape) on every card field.  Using ``| safe`` anywhere
    on title/citation fields would allow XSS injection.

    GUARD: the current template at HEAD already escapes correctly.  This test
    is a regression guard — it pins that the implementer does not accidentally
    introduce ``| safe`` when restructuring the card markup for per-lens grouping.
    """
    _patch_route(monkeypatch, _MARKET_PRISM_ROW, _SOURCES_ROW_XSS)
    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    html = resp.data.decode("utf-8", errors="replace")

    # The raw <b> tag from the title must NOT appear as live HTML.
    assert "<b>Inject</b>" not in html, (
        "The title '<b>Inject</b>' must be HTML-escaped before rendering.  "
        "Using ``| safe`` on the title field in any card variant would inject "
        "raw markup → XSS.  Keep ``| e`` (or Jinja auto-escape) on all card fields."
    )

    # The text 'Inject' must still appear (entity-encoded) so the card renders.
    assert "Inject" in html, (
        "The title text 'Inject' must be present in the rendered HTML (entity-encoded form).  "
        "If 'Inject' is missing entirely, the card is not rendering the title field at all."
    )
