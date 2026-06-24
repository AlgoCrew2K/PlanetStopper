"""
RED tests for prism_scheduler._patch_provenance (DE-PRISM-SOURCES-001).

The post-council patch in prism_scheduler rebuilds per-lens validated
article_corpus citations (sentiment, macro, derivatives, fundamentals) and
persists them into the MARKET_PRISM row's raw_response via the new
database.update_advisor_observation_raw_response accessor.

Acceptance criteria covered:
  AC-1  _patch_provenance populates article_corpus for the 4 url-bearing lenses
        when builders return valid citations.
  AC-2  technicals never gets article_corpus populated regardless of what
        _build_technicals_section returns (no fabricated public urls).
  AC-3  Every emitted citation passes build_citation validation; malformed
        citations from a builder are dropped (not persisted).
  AC-4  D-1 never-raises: builder raises → row left unchanged + no exception;
        malformed raw_response → no-op; None row → no-op.
  AC-6  Idempotency: re-patching the same row does not duplicate citations.

Design notes:
  - Seam: patch `ai_advisor._build_<lens>_section` (impl-agnostic; works whether
    the implementer calls builders directly or via lens_pipeline._build_per_lens_digest).
  - DB isolation: the autouse `_isolate_db` fixture in tests/conftest.py redirects
    DB_PATH to a per-test tempfile and calls init_db() — each test starts clean.
  - Never assert exact float values or hardcoded producer-computed data; assert
    shape, keys, and url scheme validity per project standard (feedback_no_hardcoded_test_values).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import database
import prism_scheduler

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

_RUN_ID = "test-run-id-de-prism-sources-001"

# Minimal citation dict that passes ai_advisor.build_citation validation:
# all four required fields present, url is http/https.
_VALID_CITATION_SENTIMENT = {
    "title": "GDELT Sentiment Daily",
    "url": "https://gdelt.example.com/sentiment-2026-06-24",
    "published": "2026-06-24",
    "lens": "sentiment",
}
_VALID_CITATION_MACRO = {
    "title": "FRED T10Y2Y Release",
    "url": "https://fred.stlouisfed.org/series/T10Y2Y",
    "published": "2026-06-24",
    "lens": "macro",
}
_VALID_CITATION_DERIVATIVES = {
    "title": "FRED VIX Release",
    "url": "https://fred.stlouisfed.org/series/VIXCLS",
    "published": "2026-06-24",
    "lens": "derivatives",
}
_VALID_CITATION_FUNDAMENTALS = {
    "title": "SEC EDGAR AAPL 10-K",
    "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=AAPL",
    "published": "2026-06-24",
    "lens": "fundamentals",
}

# Minimal per_lens_digest as the council would write it — no article_corpus yet.
_COUNCIL_PER_LENS_DIGEST = {
    "technicals": {"available": True, "summary": "SMA50 above SMA200", "sources": []},
    "sentiment": {"available": True, "summary": "Neutral bias", "sources": []},
    "derivatives": {"available": True, "summary": "VIX 18.2", "sources": []},
    "macro": {"available": True, "summary": "10yr 4.4%", "sources": []},
    "fundamentals": {"available": True, "summary": "EPS beat Q1", "sources": []},
}

_BASE_RAW_RESPONSE = {
    "run_id": _RUN_ID,
    "overall_sentiment": "neutral",
    "sentiment_rationale": "Mixed signals.",
    "per_lens_digest": _COUNCIL_PER_LENS_DIGEST,
}


def _insert_market_prism_row(raw_response: dict | str) -> dict:
    """Insert a MARKET_PRISM row and return the full row dict (with id)."""
    row_id = database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="",
        verdict="neutral",
        raw_response=raw_response,
        symphony_id="",
    )
    row = database.get_latest_market_prism_summary()
    assert row is not None, "Row just inserted must be retrievable"
    assert row["id"] == row_id
    return row


# ---------------------------------------------------------------------------
# Builder return-value helpers
# ---------------------------------------------------------------------------


def _available_section_with_sources(citations: list[dict]) -> dict:
    return {"available": True, "summary": "stub summary", "sources": citations}


def _unavailable_section() -> dict:
    return {"available": False, "reason": "StubError", "sources": []}


# ---------------------------------------------------------------------------
# T1 — AC-1/AC-3: happy path populates article_corpus for the 4 url-bearing lenses
# ---------------------------------------------------------------------------


def test_patch_provenance_populates_article_corpus_for_url_bearing_lenses():
    """AC-1: _patch_provenance writes validated article_corpus for sentiment, macro,
    derivatives, fundamentals; AC-3: each emitted citation has url/title/published.

    GIVEN a MARKET_PRISM row in the DB with no article_corpus, AND builders
    patched to return one valid citation each,
    WHEN _patch_provenance is called,
    THEN each of the 4 url-bearing lenses has article_corpus with at least one
    entry; every entry has url (http/https), title, published.
    """
    raw = dict(_BASE_RAW_RESPONSE)
    raw["per_lens_digest"] = {k: dict(v) for k, v in _COUNCIL_PER_LENS_DIGEST.items()}
    row = _insert_market_prism_row(raw)

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_available_section_with_sources([_VALID_CITATION_SENTIMENT]),
        ),
        patch(
            "ai_advisor._build_macro_section",
            return_value=_available_section_with_sources([_VALID_CITATION_MACRO]),
        ),
        patch(
            "ai_advisor._build_derivatives_section",
            return_value=_available_section_with_sources([_VALID_CITATION_DERIVATIVES]),
        ),
        patch(
            "ai_advisor._build_fundamentals_section",
            return_value=_available_section_with_sources([_VALID_CITATION_FUNDAMENTALS]),
        ),
        patch(
            "ai_advisor._build_technicals_section",
            return_value=_available_section_with_sources([]),
        ),
    ):
        prism_scheduler._patch_provenance(_RUN_ID, row)

    updated_row = database.get_latest_market_prism_summary()
    assert updated_row is not None
    pld = updated_row["raw_response"]["per_lens_digest"]

    for lens in ("sentiment", "macro", "derivatives", "fundamentals"):
        corpus = pld[lens].get("article_corpus")
        assert corpus, (
            f"Expected article_corpus populated for lens={lens!r} after _patch_provenance, "
            f"got: {corpus!r}"
        )
        for entry in corpus:
            assert isinstance(entry, dict), f"citation must be a dict, got {type(entry)}"
            url = entry.get("url", "")
            assert url.startswith("http://") or url.startswith("https://"), (
                f"citation url must be http/https, got {url!r}"
            )
            assert entry.get("title"), f"citation missing title in lens={lens!r}"
            assert entry.get("published"), f"citation missing published in lens={lens!r}"


# ---------------------------------------------------------------------------
# T2 — AC-2: technicals never gets article_corpus populated
# ---------------------------------------------------------------------------


def test_patch_provenance_technicals_never_gets_article_corpus():
    """AC-2: Even if _build_technicals_section returns sources with urls (which it
    shouldn't in production), _patch_provenance must NOT write article_corpus for
    technicals — Alpaca bar data has no public provenance urls.
    """
    raw = dict(_BASE_RAW_RESPONSE)
    raw["per_lens_digest"] = {k: dict(v) for k, v in _COUNCIL_PER_LENS_DIGEST.items()}
    row = _insert_market_prism_row(raw)

    # Even if the technicals builder returns a url-bearing citation, the patch
    # must not write it — technicals is hard-excluded.
    technicals_with_url = {
        "title": "Alpaca bar stub",
        "url": "https://alpaca.markets/bars",
        "published": "2026-06-24",
        "lens": "technicals",
    }

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_available_section_with_sources([_VALID_CITATION_SENTIMENT]),
        ),
        patch(
            "ai_advisor._build_macro_section",
            return_value=_available_section_with_sources([_VALID_CITATION_MACRO]),
        ),
        patch(
            "ai_advisor._build_derivatives_section",
            return_value=_available_section_with_sources([_VALID_CITATION_DERIVATIVES]),
        ),
        patch(
            "ai_advisor._build_fundamentals_section",
            return_value=_available_section_with_sources([_VALID_CITATION_FUNDAMENTALS]),
        ),
        patch(
            "ai_advisor._build_technicals_section",
            return_value=_available_section_with_sources([technicals_with_url]),
        ),
    ):
        prism_scheduler._patch_provenance(_RUN_ID, row)

    updated_row = database.get_latest_market_prism_summary()
    assert updated_row is not None
    tech_entry = updated_row["raw_response"]["per_lens_digest"].get("technicals", {})
    corpus = tech_entry.get("article_corpus")
    # Must be absent or empty — never fabricated urls from Alpaca bar data.
    assert not corpus, (
        f"technicals article_corpus must be absent/empty after _patch_provenance, got: {corpus!r}"
    )


# ---------------------------------------------------------------------------
# T3 — AC-4 D-1: all builders raise → row left unchanged, no exception propagated
# ---------------------------------------------------------------------------


def test_patch_provenance_d1_builder_raises_row_unchanged():
    """AC-4: When ALL lens builders raise RuntimeError, _patch_provenance must
    not propagate any exception AND must leave the DB row exactly as the council
    wrote it (no partial write, no corruption).
    """
    # Pre-patch the row with a known sentinel to verify unchanged-ness.
    sentinel_corpus: list[dict] = []
    pld = {k: dict(v) for k, v in _COUNCIL_PER_LENS_DIGEST.items()}
    pld["sentiment"]["article_corpus"] = sentinel_corpus
    raw = dict(_BASE_RAW_RESPONSE)
    raw["per_lens_digest"] = pld
    row = _insert_market_prism_row(raw)

    original_raw_json = json.dumps(row["raw_response"], sort_keys=True)

    def _raise(*args, **kwargs):
        raise RuntimeError("simulated network failure")

    with (
        patch("ai_advisor._build_sentiment_section", side_effect=_raise),
        patch("ai_advisor._build_macro_section", side_effect=_raise),
        patch("ai_advisor._build_derivatives_section", side_effect=_raise),
        patch("ai_advisor._build_fundamentals_section", side_effect=_raise),
        patch("ai_advisor._build_technicals_section", side_effect=_raise),
    ):
        # Must not raise — D-1 contract.
        prism_scheduler._patch_provenance(_RUN_ID, row)

    updated_row = database.get_latest_market_prism_summary()
    assert updated_row is not None
    updated_raw_json = json.dumps(updated_row["raw_response"], sort_keys=True)
    assert updated_raw_json == original_raw_json, (
        "Row must be byte-for-byte unchanged when all builders raise; "
        f"diff: original={original_raw_json[:200]!r}, "
        f"updated={updated_raw_json[:200]!r}"
    )


# ---------------------------------------------------------------------------
# T4 — AC-4 D-1: malformed raw_response → no-op, no exception
# ---------------------------------------------------------------------------


def test_patch_provenance_d1_malformed_raw_response_no_op():
    """AC-4: MARKET_PRISM row with unparseable raw_response JSON string → _patch_provenance
    must not raise AND must not corrupt the DB row.
    """
    # Insert with a known raw_response, then manually corrupt it to simulate
    # a row whose raw_response is not valid JSON.
    row_id = database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="",
        verdict="limited-inputs",
        raw_response={"run_id": _RUN_ID},
        symphony_id="",
    )
    # Build a fake row dict with deliberately malformed raw_response as a string.
    # _patch_provenance receives the row dict from _get_market_prism_row_for_run,
    # which deserialises raw_response — but the patch may receive a pre-constructed
    # row dict; we simulate a string payload that can't be parsed.
    malformed_row = {
        "id": row_id,
        "advisor_role": "MARKET_PRISM",
        "verdict": "limited-inputs",
        "raw_response": "{this is not valid json",  # will fail json.loads
        "created_at": "2026-06-24 03:00:00",
    }

    # Must not raise.
    prism_scheduler._patch_provenance(_RUN_ID, malformed_row)
    # DB should still have the original row (unchanged).
    fetched = database.get_latest_market_prism_summary()
    assert fetched is not None
    assert fetched["id"] == row_id


# ---------------------------------------------------------------------------
# T5 — AC-4 D-1: None row → silent no-op
# ---------------------------------------------------------------------------


def test_patch_provenance_d1_none_row_no_op():
    """AC-4: _patch_provenance(run_id, None) must not raise."""
    prism_scheduler._patch_provenance(_RUN_ID, None)
    # No assertion on DB state — test passes if no exception propagates.


# ---------------------------------------------------------------------------
# T6 — AC-6 idempotency: re-patching does not duplicate citations
# ---------------------------------------------------------------------------


def test_patch_provenance_idempotent_no_duplicate_citations():
    """AC-6: Calling _patch_provenance twice on the same row with identical
    builders must not duplicate citations.
    """
    raw = dict(_BASE_RAW_RESPONSE)
    raw["per_lens_digest"] = {k: dict(v) for k, v in _COUNCIL_PER_LENS_DIGEST.items()}
    row = _insert_market_prism_row(raw)

    builder_returns = {
        "ai_advisor._build_sentiment_section": _available_section_with_sources(
            [_VALID_CITATION_SENTIMENT]
        ),
        "ai_advisor._build_macro_section": _available_section_with_sources([_VALID_CITATION_MACRO]),
        "ai_advisor._build_derivatives_section": _available_section_with_sources(
            [_VALID_CITATION_DERIVATIVES]
        ),
        "ai_advisor._build_fundamentals_section": _available_section_with_sources(
            [_VALID_CITATION_FUNDAMENTALS]
        ),
        "ai_advisor._build_technicals_section": _available_section_with_sources([]),
    }

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=builder_returns["ai_advisor._build_sentiment_section"],
        ),
        patch(
            "ai_advisor._build_macro_section",
            return_value=builder_returns["ai_advisor._build_macro_section"],
        ),
        patch(
            "ai_advisor._build_derivatives_section",
            return_value=builder_returns["ai_advisor._build_derivatives_section"],
        ),
        patch(
            "ai_advisor._build_fundamentals_section",
            return_value=builder_returns["ai_advisor._build_fundamentals_section"],
        ),
        patch(
            "ai_advisor._build_technicals_section",
            return_value=builder_returns["ai_advisor._build_technicals_section"],
        ),
    ):
        prism_scheduler._patch_provenance(_RUN_ID, row)

    # Re-fetch the row (which now has article_corpus) and patch again.
    row_after_first = database.get_latest_market_prism_summary()
    assert row_after_first is not None

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=builder_returns["ai_advisor._build_sentiment_section"],
        ),
        patch(
            "ai_advisor._build_macro_section",
            return_value=builder_returns["ai_advisor._build_macro_section"],
        ),
        patch(
            "ai_advisor._build_derivatives_section",
            return_value=builder_returns["ai_advisor._build_derivatives_section"],
        ),
        patch(
            "ai_advisor._build_fundamentals_section",
            return_value=builder_returns["ai_advisor._build_fundamentals_section"],
        ),
        patch(
            "ai_advisor._build_technicals_section",
            return_value=builder_returns["ai_advisor._build_technicals_section"],
        ),
    ):
        prism_scheduler._patch_provenance(_RUN_ID, row_after_first)

    row_after_second = database.get_latest_market_prism_summary()
    assert row_after_second is not None

    pld_first = row_after_first["raw_response"]["per_lens_digest"]
    pld_second = row_after_second["raw_response"]["per_lens_digest"]

    for lens in ("sentiment", "macro", "derivatives", "fundamentals"):
        count_first = len(pld_first[lens].get("article_corpus") or [])
        count_second = len(pld_second[lens].get("article_corpus") or [])
        assert count_second == count_first, (
            f"Re-patching must not duplicate citations for lens={lens!r}; "
            f"first patch: {count_first}, second patch: {count_second}"
        )


# ---------------------------------------------------------------------------
# T7 — AC-3: malformed citation from builder is dropped, not persisted
# ---------------------------------------------------------------------------


def test_patch_provenance_malformed_citation_dropped():
    """AC-3: A builder returning a citation with an invalid url (bad scheme) must
    result in that citation being dropped — not written to article_corpus.
    build_citation rejects non-http/https urls.
    """
    malformed_citation = {
        "title": "Not a real URL",
        "url": "not-a-url",  # fails build_citation — no http/https scheme
        "published": "2026-06-24",
        "lens": "macro",
    }

    raw = dict(_BASE_RAW_RESPONSE)
    raw["per_lens_digest"] = {k: dict(v) for k, v in _COUNCIL_PER_LENS_DIGEST.items()}
    row = _insert_market_prism_row(raw)

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_available_section_with_sources([_VALID_CITATION_SENTIMENT]),
        ),
        patch(
            "ai_advisor._build_macro_section",
            return_value=_available_section_with_sources([malformed_citation]),
        ),
        patch(
            "ai_advisor._build_derivatives_section",
            return_value=_available_section_with_sources([_VALID_CITATION_DERIVATIVES]),
        ),
        patch(
            "ai_advisor._build_fundamentals_section",
            return_value=_available_section_with_sources([_VALID_CITATION_FUNDAMENTALS]),
        ),
        patch(
            "ai_advisor._build_technicals_section",
            return_value=_available_section_with_sources([]),
        ),
    ):
        prism_scheduler._patch_provenance(_RUN_ID, row)

    updated_row = database.get_latest_market_prism_summary()
    assert updated_row is not None
    macro_corpus = (
        updated_row["raw_response"]["per_lens_digest"]["macro"].get("article_corpus") or []
    )

    # No entry should have a non-http/https url.
    for entry in macro_corpus:
        url = entry.get("url", "")
        assert url.startswith("http://") or url.startswith("https://"), (
            f"Malformed citation (bad url scheme) must be dropped; found url={url!r} "
            f"in macro article_corpus after _patch_provenance"
        )

    # Specifically: the malformed citation with url="not-a-url" must not appear.
    bad_urls = [
        e.get("url")
        for e in macro_corpus
        if not (
            (e.get("url") or "").startswith("http://")
            or (e.get("url") or "").startswith("https://")
        )
    ]
    assert not bad_urls, f"Malformed citations must be dropped, but found: {bad_urls!r}"
