"""
RED test — PM sufficiency review, Probe 3 (DE-PRISM-NUMERIC-VERIFY-001).

_patch_provenance gained an optional `lens_sections=None` 3rd kwarg (AC-4) so main()'s
single shared patch-time fetch can feed BOTH the SOURCES row and the numeric verifier,
instead of each doing its own independent live builder call.

Every EXISTING test in tests/prism_scheduler/test_patch_provenance.py calls
_patch_provenance with exactly 2 positional args — that legitimately pins the
lens_sections=None DEFAULT path stays byte-identical (those tests assert the exact
SOURCES-row content, so any regression there would already fail them). But nothing
exercises the NEW lens_sections=<dict> REUSE branch at all, let alone proves it is
equivalent to the live-fetch branch — this is that missing pin.

Design: call _patch_provenance twice, for two different run_ids, with the SAME
canned lens data supplied two different ways:
  1. via the live-builder-mock path (2 positional args, lens_sections=None default —
     the builders are mocked to return the canned sections)
  2. via the lens_sections= reuse path (3rd kwarg — the SAME canned sections passed
     directly; the builders are mocked to RAISE, so if the reuse branch has a bug and
     falls through to calling them anyway, this test fails loudly rather than silently
     succeeding by coincidence)

Then assert both persisted MARKET_PRISM_SOURCES rows have byte-identical
per_lens_digest content (run_id necessarily differs; everything else must not).

DB isolation: conftest._isolate_db autouse fixture. Run protocol: targeted -n0 only.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import database
import prism_scheduler

_RUN_ID_LIVE = "equiv-test-live-fetch-path"
_RUN_ID_LENS = "equiv-test-lens-sections-path"

# Council's own per_lens_digest — required for _patch_provenance to consider a lens
# "in scope" for citation collection (a lens absent here gets no citations regardless
# of what the builder/lens_sections supplies).
_COUNCIL_PER_LENS_DIGEST = {
    "technicals": {"available": True, "summary": "SMA50 above SMA200", "sources": []},
    "sentiment": {"available": True, "summary": "Neutral bias", "sources": []},
    "derivatives": {"available": True, "summary": "VIX 18.2", "sources": []},
    "macro": {"available": True, "summary": "10yr 4.4%", "sources": []},
    "fundamentals": {"available": True, "summary": "EPS beat Q1", "sources": []},
}

_VALID_CITATION_SENTIMENT = {
    "title": "Reuters Markets Daily",
    "url": "https://www.reuters.com/markets/equiv-test",
    "published": "2026-07-02",
    "lens": "sentiment",
}
_VALID_CITATION_MACRO = {
    "title": "FRED DGS10 Release",
    "url": "https://fred.stlouisfed.org/series/DGS10",
    "published": "2026-07-02",
    "lens": "macro",
}
_VALID_CITATION_DERIVATIVES = {
    "title": "FRED VIX Release",
    "url": "https://fred.stlouisfed.org/series/VIXCLS",
    "published": "2026-07-02",
    "lens": "derivatives",
}
_VALID_CITATION_FUNDAMENTALS = {
    "title": "SEC EDGAR AAPL 10-K",
    "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=AAPL",
    "published": "2026-07-02",
    "lens": "fundamentals",
}


def _canned_sentiment_section() -> dict:
    """Production shape of _build_sentiment_section (ai_advisor.py:673-684)."""
    return {
        "lens": "sentiment",
        "available": True,
        "payload": {"tone_score": 0.12, "corpus": [], "events": [], "article_count": 0},
        "sources": [_VALID_CITATION_SENTIMENT],
        "article_corpus": [],
    }


def _canned_available_section(lens: str, citation: dict) -> dict:
    return {"lens": lens, "available": True, "payload": {"stub": True}, "sources": [citation]}


def _canned_technicals_section() -> dict:
    return {"lens": "technicals", "available": True, "payload": {"stub": True}, "sources": []}


def _make_row(run_id: str) -> dict:
    """A MARKET_PRISM-shaped row dict (not persisted — _patch_provenance takes it
    directly, mirrors the row shape _get_market_prism_row_for_run returns)."""
    return {
        "id": 1,
        "advisor_role": "MARKET_PRISM",
        "subject_id": "",
        "verdict": "neutral",
        "raw_response": {
            "run_id": run_id,
            "run_ts": run_id,
            "overall_sentiment": "neutral",
            "sentiment_rationale": "Equivalence test.",
            "per_lens_digest": {k: dict(v) for k, v in _COUNCIL_PER_LENS_DIGEST.items()},
        },
    }


def test_patch_provenance_lens_sections_reuse_matches_live_fetch_sources_row():
    """Probe 3: _patch_provenance(run_id, row, lens_sections=<canned>) must persist a
    MARKET_PRISM_SOURCES row with per_lens_digest content byte-identical to what
    _patch_provenance(run_id, row) (2-arg, live builder fetch) would persist for the
    SAME lens data — proving the AC-4 refactor changed only WHERE the data comes from,
    never WHAT gets persisted.
    """
    canned_sections = {
        "sentiment": _canned_sentiment_section(),
        "macro": _canned_available_section("macro", _VALID_CITATION_MACRO),
        "derivatives": _canned_available_section("derivatives", _VALID_CITATION_DERIVATIVES),
        "fundamentals": _canned_available_section("fundamentals", _VALID_CITATION_FUNDAMENTALS),
        "technicals": _canned_technicals_section(),
    }

    # --- Path 1: live-builder-mock (2-arg call, lens_sections=None default) ---
    with (
        patch("ai_advisor._build_sentiment_section", return_value=canned_sections["sentiment"]),
        patch("ai_advisor._build_macro_section", return_value=canned_sections["macro"]),
        patch("ai_advisor._build_derivatives_section", return_value=canned_sections["derivatives"]),
        patch(
            "ai_advisor._build_fundamentals_section", return_value=canned_sections["fundamentals"]
        ),
        patch("ai_advisor._build_technicals_section", return_value=canned_sections["technicals"]),
        patch("ai_advisor.persist_market_lens_cache", return_value=None),
    ):
        prism_scheduler._patch_provenance(_RUN_ID_LIVE, _make_row(_RUN_ID_LIVE))

    # --- Path 2: lens_sections= reuse (3rd kwarg). Builders mocked to RAISE — if the
    # reuse branch has a bug and calls them anyway, this fails loudly, not silently. ---
    def _boom(*_a, **_kw):
        raise AssertionError(
            "AC-4 violation: _patch_provenance called a live builder even though "
            "lens_sections was fully supplied — the reuse branch must never re-fetch."
        )

    with (
        patch("ai_advisor._build_sentiment_section", side_effect=_boom),
        patch("ai_advisor._build_macro_section", side_effect=_boom),
        patch("ai_advisor._build_derivatives_section", side_effect=_boom),
        patch("ai_advisor._build_fundamentals_section", side_effect=_boom),
        patch("ai_advisor._build_technicals_section", side_effect=_boom),
        patch("ai_advisor.persist_market_lens_cache", return_value=None),
    ):
        prism_scheduler._patch_provenance(
            _RUN_ID_LENS, _make_row(_RUN_ID_LENS), lens_sections=canned_sections
        )

    live_sources_row = database.get_latest_market_prism_sources_for_run(_RUN_ID_LIVE)
    lens_sources_row = database.get_latest_market_prism_sources_for_run(_RUN_ID_LENS)

    assert live_sources_row is not None, "Live-fetch path must have persisted a SOURCES row"
    assert lens_sources_row is not None, "lens_sections= path must have persisted a SOURCES row"

    live_pld = live_sources_row["raw_response"]["per_lens_digest"]
    lens_pld = lens_sources_row["raw_response"]["per_lens_digest"]

    assert json.dumps(live_pld, sort_keys=True) == json.dumps(lens_pld, sort_keys=True), (
        "The lens_sections= reuse path must persist a per_lens_digest byte-identical "
        "to the live-fetch path for the same underlying lens data — the AC-4 refactor "
        "must change only the DATA SOURCE, never the persisted SHAPE/CONTENT.\n"
        f"live (2-arg):         {live_pld!r}\n"
        f"lens_sections (3-arg): {lens_pld!r}"
    )
