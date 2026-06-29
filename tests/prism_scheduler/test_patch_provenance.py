"""
RED tests for prism_scheduler._patch_provenance v2 (DE-PRISM-SOURCES-001).

v2 design: _patch_provenance INSERTs a new MARKET_PRISM_SOURCES advisor_observations
row — it does NOT mutate the existing MARKET_PRISM row. The MARKET_PRISM row must
be byte-unchanged after _patch_provenance runs.

Acceptance criteria covered:
  AC-1  _patch_provenance INSERTs a MARKET_PRISM_SOURCES row with validated
        article_corpus for the 4 url-bearing lenses; MARKET_PRISM row is byte-unchanged.
  AC-2  technicals lens absent from the SOURCES row (no public urls from Alpaca bars).
  AC-3  Every emitted citation passes build_citation validation (http/https url, title,
        published); malformed citations from a builder are dropped.
  AC-4  D-1 never-raises: builder raises -> no SOURCES row inserted, MARKET_PRISM row
        unchanged, no exception propagated. None row -> no-op.
  AC-6  Idempotency: re-running for the same run_id does NOT insert a second SOURCES row.
  V2-GUARD  The MARKET_PRISM row's raw_response is byte-unchanged after _patch_provenance.
  V2-ROLE   The inserted row has advisor_role="MARKET_PRISM_SOURCES", subject_id="global".
  V2-RUN_ID The inserted row's raw_response["run_id"] matches the scheduler run_id.

Fixture provenance (D-2): rows inserted via insert_advisor_observation. The MARKET_PRISM
raw_response follows the synthesizer role-file contract: run_id == run_ts (same UUID4 string,
prism-synthesizer.md:130-132). No hardcoded producer-computed values — assertions check
shape, key presence, and url scheme validity.

DB isolation: the global conftest._isolate_db autouse fixture redirects DB_PATH to a
per-test tempfile and calls init_db(). Each test starts with a clean, fully-migrated DB.
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

_RUN_ID = "test-run-id-de-prism-sources-001-v2"

# Minimal citation dict that passes ai_advisor.build_citation validation.
_VALID_CITATION_SENTIMENT = {
    "title": "GDELT Sentiment Daily",
    "url": "https://gdelt.example.com/sentiment-2026-06-24",
    "published": "2026-06-24",
    "lens": "sentiment",
}

# Raw corpus item — production shape from _build_sentiment_section (ai_advisor.py:672).
_SENTIMENT_CORPUS_ARTICLE = {
    "title": "Reuters Markets Daily",
    "url": "https://www.reuters.com/markets/2026-06-24",
    "published": "2026-06-24",
    "domain": "reuters.com",
    "score": 0.87,
    "topics": ["macro", "broad-sentiment"],
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

# Minimal per_lens_digest as the council synthesizer writes it.
# run_id == run_ts (same UUID4) per prism-synthesizer.md:130-132.
_COUNCIL_PER_LENS_DIGEST = {
    "technicals": {"available": True, "summary": "SMA50 above SMA200", "sources": []},
    "sentiment": {"available": True, "summary": "Neutral bias", "sources": []},
    "derivatives": {"available": True, "summary": "VIX 18.2", "sources": []},
    "macro": {"available": True, "summary": "10yr 4.4%", "sources": []},
    "fundamentals": {"available": True, "summary": "EPS beat Q1", "sources": []},
}

_BASE_RAW_RESPONSE = {
    "run_id": _RUN_ID,
    "run_ts": _RUN_ID,  # synthesizer sets run_ts == run_id (same UUID4)
    "overall_sentiment": "neutral",
    "sentiment_rationale": "Mixed signals.",
    "per_lens_digest": _COUNCIL_PER_LENS_DIGEST,
}


def _insert_market_prism_row() -> dict:
    """Insert a MARKET_PRISM row (production shape) and return the full row dict."""
    raw = dict(_BASE_RAW_RESPONSE)
    raw["per_lens_digest"] = {k: dict(v) for k, v in _COUNCIL_PER_LENS_DIGEST.items()}
    row_id = database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="",
        verdict="neutral",
        raw_response=raw,
        symphony_id="",
    )
    row = database.get_latest_market_prism_summary()
    assert row is not None, "MARKET_PRISM row just inserted must be retrievable"
    assert row["id"] == row_id
    return row


def _count_sources_rows() -> int:
    """Return the current count of MARKET_PRISM_SOURCES rows in advisor_observations."""
    conn = database.get_ro_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM advisor_observations WHERE advisor_role = 'MARKET_PRISM_SOURCES'"
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count


def _count_all_advisor_rows() -> int:
    """Return the total row count of advisor_observations."""
    conn = database.get_ro_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM advisor_observations")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def _count_lens_cache_rows() -> int:
    """Return the current count of MARKET_LENS_CACHE rows in advisor_observations."""
    conn = database.get_ro_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM advisor_observations WHERE advisor_role = 'MARKET_LENS_CACHE'"
    )
    count = cursor.fetchone()[0]
    conn.close()
    return count


# ---------------------------------------------------------------------------
# Builder helpers (production-shape mocks)
# ---------------------------------------------------------------------------


def _available_section_with_sources(citations: list[dict]) -> dict:
    return {"available": True, "summary": "stub summary", "sources": citations}


def _sentiment_production_shape(sources: list[dict], article_corpus: list[dict]) -> dict:
    """Production shape of _build_sentiment_section (ai_advisor.py:662-673).

    Returns BOTH sources (pre-validated citation dicts) AND article_corpus (raw corpus items).
    _patch_provenance must collect citations from BOTH paths.
    """
    return {
        "lens": "sentiment",
        "available": True,
        "payload": {
            "tone_score": 0.12,
            "corpus": article_corpus,
            "events": [],
            "article_count": len(article_corpus),
        },
        "sources": sources,
        "article_corpus": article_corpus,
    }


def _unavailable_section() -> dict:
    return {"available": False, "reason": "StubError", "sources": []}


# ---------------------------------------------------------------------------
# T1 — V2-GUARD + AC-1: MARKET_PRISM row is byte-unchanged; SOURCES row inserted
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_market_prism_row_byte_unchanged():
    """V2-GUARD / AC-1: After _patch_provenance runs, the MARKET_PRISM row's
    raw_response must be IDENTICAL to what the council originally wrote.

    v2 design: _patch_provenance INSERTS a new MARKET_PRISM_SOURCES row —
    it NEVER modifies the existing MARKET_PRISM row.
    """
    row = _insert_market_prism_row()
    original_raw_json = json.dumps(row["raw_response"], sort_keys=True)

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_sentiment_production_shape(
                sources=[_VALID_CITATION_SENTIMENT],
                article_corpus=[_SENTIMENT_CORPUS_ARTICLE],
            ),
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

    after_row = database.get_latest_market_prism_summary()
    assert after_row is not None
    after_raw_json = json.dumps(after_row["raw_response"], sort_keys=True)
    assert after_raw_json == original_raw_json, (
        "The MARKET_PRISM row's raw_response must be byte-for-byte unchanged after "
        "_patch_provenance. v2 design: _patch_provenance INSERTs a new SOURCES row, "
        "never modifies the existing MARKET_PRISM row. "
        f"Before: {original_raw_json[:300]!r} "
        f"After:  {after_raw_json[:300]!r}"
    )


def test_patch_provenance_v2_inserts_sources_row():
    """V2-GUARD / AC-1 + AC-2 (lens-cache): _patch_provenance must INSERT exactly two
    new advisor_observations rows: one MARKET_PRISM_SOURCES row and one MARKET_LENS_CACHE
    row.  The advisor_observations row count must grow by 2.
    """
    row = _insert_market_prism_row()
    count_before = _count_all_advisor_rows()
    assert count_before == 1, f"Precondition: 1 row before patch; got {count_before}"

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_sentiment_production_shape(
                sources=[_VALID_CITATION_SENTIMENT],
                article_corpus=[_SENTIMENT_CORPUS_ARTICLE],
            ),
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

    count_after = _count_all_advisor_rows()
    # _patch_provenance inserts both a MARKET_PRISM_SOURCES row and a
    # MARKET_LENS_CACHE row (AC-2 of the cache-serve PR), so the total delta is +2.
    assert count_after == count_before + 2, (
        f"_patch_provenance must INSERT exactly 2 new rows "
        f"(MARKET_PRISM_SOURCES + MARKET_LENS_CACHE); "
        f"row count before={count_before}, after={count_after}."
    )
    sources_count = _count_sources_rows()
    assert sources_count == 1, f"Expected exactly 1 MARKET_PRISM_SOURCES row; got {sources_count}"
    lens_cache_count = _count_lens_cache_rows()
    assert lens_cache_count == 1, (
        f"Expected exactly 1 MARKET_LENS_CACHE row (cache-serve bundle); got {lens_cache_count}"
    )


# ---------------------------------------------------------------------------
# T2 — V2-ROLE + V2-RUN_ID: SOURCES row shape is correct
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_sources_row_shape():
    """V2-ROLE / V2-RUN_ID / AC-1: The inserted SOURCES row must have the correct
    shape: advisor_role='MARKET_PRISM_SOURCES', subject_id='global',
    raw_response['run_id'] == the scheduler's run_id.
    """
    row = _insert_market_prism_row()

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_sentiment_production_shape(
                sources=[_VALID_CITATION_SENTIMENT],
                article_corpus=[_SENTIMENT_CORPUS_ARTICLE],
            ),
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

    sources_row = database.get_latest_market_prism_sources_for_run(_RUN_ID)
    assert sources_row is not None, (
        "get_latest_market_prism_sources_for_run must find the inserted SOURCES row"
    )
    assert sources_row["advisor_role"] == "MARKET_PRISM_SOURCES", (
        f"SOURCES row advisor_role must be 'MARKET_PRISM_SOURCES'; "
        f"got {sources_row.get('advisor_role')!r}"
    )
    assert sources_row["subject_id"] == "global", (
        f"SOURCES row subject_id must be 'global'; got {sources_row.get('subject_id')!r}"
    )
    raw = sources_row.get("raw_response") or {}
    assert isinstance(raw, dict), f"raw_response must be a dict; got {type(raw)}"
    assert raw.get("run_id") == _RUN_ID, (
        f"raw_response['run_id'] must == {_RUN_ID!r}; got {raw.get('run_id')!r}"
    )
    assert "per_lens_digest" in raw, "SOURCES raw_response must contain 'per_lens_digest'"


# ---------------------------------------------------------------------------
# T3 — AC-1/AC-3: article_corpus in SOURCES row has validated citations
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_sources_row_has_valid_article_corpus():
    """AC-1/AC-3: The SOURCES row's per_lens_digest[lens].article_corpus must contain
    validated citation dicts with url (http/https), title, and published for the
    4 url-bearing lenses (sentiment, macro, derivatives, fundamentals).
    """
    row = _insert_market_prism_row()

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_sentiment_production_shape(
                sources=[_VALID_CITATION_SENTIMENT],
                article_corpus=[_SENTIMENT_CORPUS_ARTICLE],
            ),
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

    sources_row = database.get_latest_market_prism_sources_for_run(_RUN_ID)
    assert sources_row is not None
    pld = (sources_row.get("raw_response") or {}).get("per_lens_digest", {})

    for lens in ("sentiment", "macro", "derivatives", "fundamentals"):
        corpus = pld.get(lens, {}).get("article_corpus")
        assert corpus, f"Expected article_corpus in SOURCES row for lens={lens!r}; got: {corpus!r}"
        for entry in corpus:
            url = entry.get("url", "")
            assert url.startswith("http://") or url.startswith("https://"), (
                f"Citation url must be http/https; got {url!r} for lens={lens!r}"
            )
            assert entry.get("title"), f"Citation missing title for lens={lens!r}"
            assert entry.get("published"), f"Citation missing published for lens={lens!r}"


# ---------------------------------------------------------------------------
# T4 — AC-1 (article_corpus path): sentiment article_corpus path captured
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_captures_sentiment_article_corpus_path():
    """AC-1 (fidelity): _patch_provenance must capture citations from sentiment's
    article_corpus field even when sources=[] (article_corpus-only path).
    """
    row = _insert_market_prism_row()

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_sentiment_production_shape(
                sources=[],
                article_corpus=[_SENTIMENT_CORPUS_ARTICLE],
            ),
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

    sources_row = database.get_latest_market_prism_sources_for_run(_RUN_ID)
    assert sources_row is not None
    sentiment_corpus = (sources_row.get("raw_response") or {}).get("per_lens_digest", {}).get(
        "sentiment", {}
    ).get("article_corpus") or []

    assert sentiment_corpus, (
        "sentiment article_corpus must be populated from the builder's article_corpus "
        "field (sources was empty — only article_corpus carried the citation); "
        f"got: {sentiment_corpus!r}"
    )
    expected_url = _SENTIMENT_CORPUS_ARTICLE["url"]
    found_urls = [e.get("url") for e in sentiment_corpus]
    assert expected_url in found_urls, (
        f"Expected url {expected_url!r} from article_corpus to appear in SOURCES row; "
        f"found: {found_urls!r}"
    )


# ---------------------------------------------------------------------------
# T5 — AC-2: technicals absent from SOURCES row
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_technicals_absent_from_sources_row():
    """AC-2: technicals must NOT appear in the SOURCES row, even if the technicals
    builder returns url-bearing citations. Alpaca bar data has no public provenance urls.
    """
    row = _insert_market_prism_row()

    technicals_with_url = {
        "title": "Alpaca bar stub",
        "url": "https://alpaca.markets/bars",
        "published": "2026-06-24",
        "lens": "technicals",
    }

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value=_sentiment_production_shape(
                sources=[_VALID_CITATION_SENTIMENT],
                article_corpus=[_SENTIMENT_CORPUS_ARTICLE],
            ),
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

    sources_row = database.get_latest_market_prism_sources_for_run(_RUN_ID)
    assert sources_row is not None
    pld = (sources_row.get("raw_response") or {}).get("per_lens_digest", {})
    tech_corpus = pld.get("technicals", {}).get("article_corpus")
    assert not tech_corpus, (
        f"technicals article_corpus must be absent/empty in the SOURCES row; got: {tech_corpus!r}"
    )


# ---------------------------------------------------------------------------
# T6 — AC-4 D-1: all builders raise -> MARKET_PRISM unchanged, no exception
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_d1_builder_raises_market_prism_unchanged():
    """AC-4: When ALL lens builders raise, _patch_provenance must:
    (a) not propagate any exception (D-1),
    (b) leave the MARKET_PRISM row unchanged.
    """
    row = _insert_market_prism_row()
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
        prism_scheduler._patch_provenance(_RUN_ID, row)

    after_market_prism = database.get_latest_market_prism_summary()
    assert after_market_prism is not None
    after_raw_json = json.dumps(after_market_prism["raw_response"], sort_keys=True)
    assert after_raw_json == original_raw_json, (
        "MARKET_PRISM row must be byte-unchanged when all builders raise; "
        f"before: {original_raw_json[:200]!r} "
        f"after: {after_raw_json[:200]!r}"
    )


# ---------------------------------------------------------------------------
# T7 — AC-4 D-1: None row -> silent no-op, no INSERT
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_d1_none_row_is_noop():
    """AC-4: _patch_provenance(run_id, None) must not raise AND must not insert any row."""
    count_before = _count_all_advisor_rows()
    prism_scheduler._patch_provenance(_RUN_ID, None)
    count_after = _count_all_advisor_rows()
    assert count_after == count_before, (
        "Calling _patch_provenance with row=None must be a no-op; "
        f"row count changed from {count_before} to {count_after}"
    )


# ---------------------------------------------------------------------------
# T8 — AC-4 D-1: malformed raw_response -> no-op, no exception
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_d1_malformed_raw_response_no_op():
    """AC-4: A row dict with unparseable raw_response string -> no exception + no INSERT."""
    count_before = _count_all_advisor_rows()

    malformed_row = {
        "id": 9999,
        "advisor_role": "MARKET_PRISM",
        "verdict": "limited-inputs",
        "raw_response": "{this is not valid json",
        "created_at": "2026-06-24 03:00:00",
    }

    prism_scheduler._patch_provenance(_RUN_ID, malformed_row)

    count_after = _count_all_advisor_rows()
    assert count_after == count_before, (
        "Malformed raw_response must be a no-op (no INSERT); "
        f"count changed from {count_before} to {count_after}"
    )


# ---------------------------------------------------------------------------
# T9 — AC-6 idempotency: second call does NOT insert a second SOURCES row
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_idempotent_no_duplicate_sources_row():
    """AC-6: Calling _patch_provenance twice for the same run_id must not insert
    a second MARKET_PRISM_SOURCES row.
    """
    row = _insert_market_prism_row()

    sentiment_return = _sentiment_production_shape(
        sources=[_VALID_CITATION_SENTIMENT],
        article_corpus=[_SENTIMENT_CORPUS_ARTICLE],
    )

    for _ in range(2):
        with (
            patch(
                "ai_advisor._build_sentiment_section",
                return_value=sentiment_return,
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

    sources_count = _count_sources_rows()
    assert sources_count == 1, (
        f"Re-running _patch_provenance with the same run_id must NOT insert a "
        f"second SOURCES row (idempotency guard). Got {sources_count} SOURCES row(s)."
    )


# ---------------------------------------------------------------------------
# T10 — AC-3: malformed citation dropped, not persisted in SOURCES row
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_malformed_citation_dropped():
    """AC-3: A builder returning a citation with a non-http/https url must result in
    that citation being DROPPED — not written to the SOURCES row.
    """
    malformed_citation = {
        "title": "Not a real URL",
        "url": "not-a-url",
        "published": "2026-06-24",
        "lens": "macro",
    }
    row = _insert_market_prism_row()

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

    sources_row = database.get_latest_market_prism_sources_for_run(_RUN_ID)
    if sources_row is None:
        return  # no SOURCES row = no bad citations persisted — passes

    macro_corpus = (sources_row.get("raw_response") or {}).get("per_lens_digest", {}).get(
        "macro", {}
    ).get("article_corpus") or []
    bad_urls = [
        e.get("url")
        for e in macro_corpus
        if not (
            (e.get("url") or "").startswith("http://")
            or (e.get("url") or "").startswith("https://")
        )
    ]
    assert not bad_urls, (
        f"Malformed citations must be dropped from SOURCES row; found: {bad_urls!r}"
    )


# ---------------------------------------------------------------------------
# T11 — DEDUP: sentiment sources+article_corpus overlap deduplicated in SOURCES row
# ---------------------------------------------------------------------------


def test_patch_provenance_v2_sentiment_deduplicates_sources_and_article_corpus_overlap():
    """INTRA-PATCH DEDUP: _patch_provenance must deduplicate by url when sentiment's
    sources and article_corpus contain the same article.

    GIVEN sentiment builder returns sources=[validated-entry] + article_corpus=[same url, raw],
    WHEN _patch_provenance is called,
    THEN per_lens_digest["sentiment"]["article_corpus"] in the SOURCES row has exactly ONE
    citation for that url.
    """
    shared_url = "https://www.reuters.com/markets/overlap-dedup-v2-test-2026-06-24"

    sources_entry = {
        "title": "Reuters Markets Overlap",
        "url": shared_url,
        "published": "2026-06-24",
        "lens": "sentiment",
    }
    corpus_entry = {
        "title": "Reuters Markets Overlap",
        "url": shared_url,
        "published": "2026-06-24",
        "domain": "reuters.com",
        "score": 0.91,
        "topics": ["macro"],
    }

    row = _insert_market_prism_row()

    with (
        patch(
            "ai_advisor._build_sentiment_section",
            return_value={
                "lens": "sentiment",
                "available": True,
                "payload": {
                    "tone_score": 0.05,
                    "corpus": [corpus_entry],
                    "events": [],
                    "article_count": 1,
                },
                "sources": [sources_entry],
                "article_corpus": [corpus_entry],
            },
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

    sources_row = database.get_latest_market_prism_sources_for_run(_RUN_ID)
    assert sources_row is not None
    sentiment_corpus = (sources_row.get("raw_response") or {}).get("per_lens_digest", {}).get(
        "sentiment", {}
    ).get("article_corpus") or []

    matching = [e.get("url") for e in sentiment_corpus if e.get("url") == shared_url]
    assert len(matching) == 1, (
        f"Expected exactly 1 citation for url={shared_url!r} after dedup; "
        f"got {len(matching)} (sources+article_corpus overlap must be deduped by url). "
        f"Full sentiment corpus: {sentiment_corpus!r}"
    )
