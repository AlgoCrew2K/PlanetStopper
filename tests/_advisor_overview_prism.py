"""Helpers for the Advisor Overview order tests: the Market Prism row and the block's source.

TEST-ONLY.

* ``store_prism_row`` writes a MARKET_PRISM observation through the real writer. Its
  ``raw_response`` is the producer's schema (``advisors/lens_pipeline.py``, the dict named
  ``raw_response``) around the captured digest of ``tests/fixtures/ai_advisor/
  rf1_lens_pipeline_row77.json``; the sentiment pair is the producer's own
  ``_synthesize_via_claude`` answer for a run with no available lens, so no sentence is typed
  here (see ``PROVENANCE.md``, Prism block records).
* ``prism_block_source`` cuts the Prism block out of the template's text and normalizes it, so
  the block can be compared line for line with the copy captured before it moved.
"""

from __future__ import annotations

import json
import pathlib

import database

REPO = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "templates" / "ai_advisor.html"
BASELINE = (
    REPO
    / "tests"
    / "fixtures"
    / "ai_advisor"
    / "ux_overhaul_02"
    / "prism_block_source_baseline.txt"
)
_ROW77 = REPO / "tests" / "fixtures" / "ai_advisor" / "rf1_lens_pipeline_row77.json"
_START = 'data-testid="market-prism-block"'
_END = "{# /market-prism-block #}"


def prism_raw_response() -> dict:
    from advisors import lens_pipeline

    row = json.loads(_ROW77.read_text(encoding="utf-8"))
    digest = row["per_lens_digest"]
    sentiment, rationale = lens_pipeline._synthesize_via_claude({}, digest, 0)
    return {
        "run_ts": row["created_at"].replace(" ", "T"),
        "run_id": row["created_at"].replace(" ", "T"),
        "per_lens_digest": digest,
        "overall_sentiment": sentiment,
        "sentiment_rationale": rationale,
        "available_lens_count": sum(1 for e in digest.values() if e.get("available")),
        "total_lens_count": len(digest),
        "sources": [],
    }


def store_prism_row() -> int:
    raw = prism_raw_response()
    return database.insert_advisor_observation(
        advisor_role="MARKET_PRISM",
        subject_type="portfolio",
        subject_id="global",
        verdict=raw["overall_sentiment"],
        raw_response=raw,
    )


def prism_block_source(template_text: str) -> str:
    """The Prism block's template text, from its opening ``<div>`` line to the closing comment
    line, each line stripped and empty lines dropped (indentation moves with the block)."""
    lines = template_text.replace("\r\n", "\n").split("\n")
    start = next(i for i, line in enumerate(lines) if _START in line)
    end = next(i for i, line in enumerate(lines) if _END in line and i > start)
    kept = [line.strip() for line in lines[start : end + 1]]
    return "\n".join(line for line in kept if line) + "\n"
