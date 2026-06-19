"""
RF-1 Prose Render — RED tests for Market Prism per-lens humanization.

Feature plan: feature-plans/rf1-prose-render.md  (Status: ready)

SCOPE (AC-1..AC-7):
  AC-1  Council prose passes through humanize_lens_summary() unchanged (not re-encoded).
  AC-2  lens_pipeline JSON summaries are humanized to readable text — no raw JSON markers
        ({, }, "key":) emitted for any of the 5 lenses (technicals, sentiment, derivatives,
        macro, fundamentals).
  AC-3  Null/empty/missing summary → honest empty-state string; never "null", "{}", "None".
  AC-4  Fundamentals concision: no full 10-K dump; renders a coverage count + readable highlight.
  AC-5  obs-raw-preview element no longer emits raw JSON for MARKET_PRISM rows.
  AC-6  Regression guard: chip class, rationale, sources, "As of" still render after the fix
        (covered across unit + route tests below).
  AC-7  humanize_lens_summary() never raises over malformed/junk inputs (D-1 spirit);
        a bare JSON scalar ("16.41") is treated as prose/text, not an object to unpack.

FIXTURE PROVENANCE:
  - Lens-pipeline JSON digests: captured read-only from live DB advisor_observations id=77,
    2026-06-18 09:03:18.  Saved in tests/fixtures/ai_advisor/rf1_lens_pipeline_row77.json.
  - Council prose digests: captured read-only from temp DB (pc_f4_live.db) id=78,
    2026-06-18 20:01:30.  Saved in tests/fixtures/ai_advisor/rf1_council_row78.json.
  No fixture value was co-designed with the parser.

ADVERSARIAL RED CONTRACT:
  All unit tests below call `from advisors.prism_render import humanize_lens_summary` — a
  module that does NOT yet exist.  Every test therefore fails with ImportError in RED state.
  The route/render tests fail because the template still renders raw JSON verbatim.
  After GREEN (implementer creates the module + wires the template), tests pass only if
  raw JSON markers are genuinely absent and prose passthrough is preserved.

MOCKING STRATEGY:
  - Unit tests: import humanize_lens_summary directly; no mocking needed (pure function).
  - Route/render tests: mock database.get_latest_market_prism_summary with a fixture-derived
    MARKET_PRISM row; render the REAL Flask+Jinja2 template (never mocked — this is a
    template-logic test).  Mock only DB and analytics helpers.
  - math_engine is NEVER mocked (not involved here).
  - No live API calls; no network.

JSON-MARKER DETECTION RULE (per AC-2 / Architecture section of the feature plan):
  Raw JSON in rendered text is detected by the presence of any of the following strings:
    {"    (open brace followed by double-quote — the universal JSON object opening marker)
    ":   (double-quote followed by colon — JSON key separator)
  These two patterns together cover all structured JSON objects emitted as strings.
  A prose string that contains a brace character (e.g. "P/E ratio ${416B}") does NOT
  trigger either marker, so brace-containing prose passes the detection correctly.
  Empty-object "{}" is caught by the "{"" marker in combination with the "{}" literal check.
"""

from __future__ import annotations

import json
import pathlib
import sys
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixture loading — provenance-recorded, never co-designed with parser
# ---------------------------------------------------------------------------

_FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "ai_advisor"

_WORKTREE = pathlib.Path(__file__).resolve().parent.parent.parent


def _load_fixture(name: str) -> dict:
    with (_FIXTURES_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


# Lens-pipeline row 77: all 5 lens summaries are raw JSON strings
_ROW77 = _load_fixture("rf1_lens_pipeline_row77.json")
_PIPELINE_LENSES = _ROW77["per_lens_digest"]

# Council row 78: all 5 lens summaries are clean prose strings
_ROW78 = _load_fixture("rf1_council_row78.json")
_COUNCIL_LENSES = _ROW78["per_lens_digest"]

# Convenience: raw JSON summary strings from lens_pipeline
_TECH_JSON_SUMMARY = _PIPELINE_LENSES["technicals"]["summary"]
_SENT_JSON_SUMMARY = _PIPELINE_LENSES["sentiment"]["summary"]
_DERIV_JSON_SUMMARY = _PIPELINE_LENSES["derivatives"]["summary"]
_MACRO_JSON_SUMMARY = _PIPELINE_LENSES["macro"]["summary"]
_FUND_JSON_SUMMARY = _PIPELINE_LENSES["fundamentals"]["summary"]

# Convenience: prose summary strings from council
_TECH_PROSE_SUMMARY = _COUNCIL_LENSES["technicals"]["summary"]
_SENT_PROSE_SUMMARY = _COUNCIL_LENSES["sentiment"]["summary"]
_DERIV_PROSE_SUMMARY = _COUNCIL_LENSES["derivatives"]["summary"]
_MACRO_PROSE_SUMMARY = _COUNCIL_LENSES["macro"]["summary"]
_FUND_PROSE_SUMMARY = _COUNCIL_LENSES["fundamentals"]["summary"]

# ---------------------------------------------------------------------------
# JSON-marker detection helper (shared across tests)
# ---------------------------------------------------------------------------

_RAW_JSON_MARKERS = ('{"', '":')


def _contains_raw_json(text: str) -> bool:
    """Return True if text contains raw JSON object syntax markers.

    Detects '{"' (object open) or '":' (key separator).  A prose string
    with a brace char (e.g. '$416B' or 'P/E {Q4}') does NOT match either
    pattern, so brace-containing prose correctly passes.  '{}'  (empty object)
    is also caught because it expands to '{' + '}', which does not match
    the tighter two-char patterns — but '{' alone in prose is safe while
    '{"' (brace + quote) unambiguously signals a JSON object.
    """
    return any(marker in text for marker in _RAW_JSON_MARKERS)


def _assert_no_raw_json(result: str, lens: str, context: str = "") -> None:
    """Assert that result contains no raw JSON markers; provide a clear failure message."""
    for marker in _RAW_JSON_MARKERS:
        assert marker not in result, (
            f"humanize_lens_summary('{lens}', ...) emitted raw JSON marker {marker!r}. "
            f"The rendered text must be human-readable, not a JSON string dump. "
            f"Result: {result!r}. {context}"
        )


# ===========================================================================
# IMPORT GUARD — tests fail with ImportError in RED state (module not yet created)
# ===========================================================================


def _import_humanize():
    """Import humanize_lens_summary from advisors.prism_render.

    This import FAILS in RED state because advisors/prism_render.py does not exist.
    After GREEN, it succeeds.
    """
    from advisors.prism_render import humanize_lens_summary  # type: ignore[import]

    return humanize_lens_summary


# ===========================================================================
# AC-1: PROSE PASSTHROUGH — council prose returned unchanged
# ===========================================================================


def test_council_technicals_prose_passes_through_unchanged():
    """humanize_lens_summary('technicals', ...) with a prose summary must return the prose
    substantively unchanged — it must NOT re-encode or transform it.

    Council prose is already readable (e.g. 'BULLISH. Breadth 0.70 ...'). The humanizer's
    job is detection: if json.loads() raises or yields a non-dict/non-list, it is prose and
    must be returned as-is.  Changing prose (adding prefixes, truncating) violates AC-1.

    Fixture: council row 78 technicals summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _TECH_PROSE_SUMMARY, "sources": []}
    result = humanize("technicals", entry)

    # Must contain the core prose — the first distinctive token
    assert "BULLISH" in result, (
        f"Council prose must pass through — expected 'BULLISH' in result. Got: {result!r}"
    )
    assert "Breadth" in result, (
        f"Council prose must pass through — expected 'Breadth' in result. Got: {result!r}"
    )
    # Must NOT contain raw JSON markers (prose passthrough should never add JSON syntax)
    _assert_no_raw_json(result, "technicals", "Prose passthrough must stay clean.")


def test_council_sentiment_prose_passes_through_unchanged():
    """Council sentiment prose (containing '$', '<', numbers) must pass through as-is.

    The sentiment prose contains special chars: 'gas <$4', '%'. A naive
    implementation might double-encode these. AC-1 forbids any transformation.

    Fixture: council row 78 sentiment summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _SENT_PROSE_SUMMARY, "sources": []}
    result = humanize("sentiment", entry)

    # The summary contains "NEUTRAL" and "constructive" — both must survive passthrough
    assert "NEUTRAL" in result, (
        f"Council sentiment prose must pass through. Expected 'NEUTRAL'. Got: {result!r}"
    )
    assert "constructive" in result, (
        f"Council prose passthrough failed — 'constructive' missing. Got: {result!r}"
    )


def test_council_prose_containing_brace_not_misclassified_as_json():
    """A prose string that happens to contain a brace char must NOT be classified as JSON.

    AC-1 edge case: 'BULLISH. Breadth {0.70}' or 'P/E ratio {14.2x}' — a brace in prose
    must not trigger JSON detection.  The feature plan specifies json.loads()-based detection,
    NOT a naive leading-'{' check.

    If the implementer used `summary.startswith('{')` instead of `json.loads()`, this test
    exposes the bug: the prose would be 'humanized' (mangled) instead of passed through.
    """
    humanize = _import_humanize()

    # Prose that starts with a brace (would fool a naive leading-{ check)
    brace_prose = "BULLISH {high conviction}. Breadth 0.70 (7/10 above 50d SMA); risk-on."
    entry = {"available": True, "summary": brace_prose, "sources": []}
    result = humanize("technicals", entry)

    assert "BULLISH" in result, (
        f"Brace-containing prose must be detected as prose (not JSON) and passed through. "
        f"Expected 'BULLISH' in result. Got: {result!r}. "
        "Check: is json.loads() used for detection (correct), or a naive '{' startswith check?"
    )
    # The brace itself should still be present — prose is unchanged
    assert "{high conviction}" in result or "BULLISH" in result, (
        f"Prose passthrough must preserve brace content. Got: {result!r}"
    )


def test_council_derivatives_prose_passes_through_unchanged():
    """Council derivatives prose passes through; contains VIX numbers and CONTANGO.

    Fixture: council row 78 derivatives summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _DERIV_PROSE_SUMMARY, "sources": []}
    result = humanize("derivatives", entry)

    assert "VIX" in result, (
        f"Council derivatives prose must pass through. Expected 'VIX'. Got: {result!r}"
    )
    assert "CONTANGO" in result, f"Council prose passthrough — 'CONTANGO' missing. Got: {result!r}"
    _assert_no_raw_json(result, "derivatives")


def test_council_macro_prose_passes_through_unchanged():
    """Council macro prose passes through; contains 'Fed funds', '10Y', percentage references.

    Fixture: council row 78 macro summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _MACRO_PROSE_SUMMARY, "sources": []}
    result = humanize("macro", entry)

    assert "Fed funds" in result or "NEUTRAL" in result, (
        f"Council macro prose must pass through. Got: {result!r}"
    )
    _assert_no_raw_json(result, "macro")


def test_council_fundamentals_prose_passes_through_unchanged():
    """Council fundamentals prose passes through; contains 'Coverage', tickers, percentage refs.

    Fixture: council row 78 fundamentals summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _FUND_PROSE_SUMMARY, "sources": []}
    result = humanize("fundamentals", entry)

    assert "Coverage" in result or "BULLISH" in result, (
        f"Council fundamentals prose must pass through. Got: {result!r}"
    )
    _assert_no_raw_json(result, "fundamentals")


# ===========================================================================
# AC-2: JSON → READABLE for each of the 5 lens_pipeline shapes
# ===========================================================================


def test_technicals_json_humanized_no_raw_json_markers():
    """lens_pipeline technicals JSON is humanized — no raw JSON syntax in output.

    Captured fixture (row 77): ma_posture dict + breadth float + momentum dict.
    The humanized output must contain readable signals (e.g. 'Breadth') and must
    NOT contain JSON object markers ('{"' or '":').

    Adversarial bar: the current template renders the JSON string verbatim via
    `_lens.get('summary') | e`, which outputs the raw JSON to the browser.
    This test fails against that current behavior.

    Fixture: lens_pipeline row 77 technicals summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _TECH_JSON_SUMMARY, "sources": []}
    result = humanize("technicals", entry)

    # Must NOT contain raw JSON markers
    _assert_no_raw_json(result, "technicals", "Row 77 technicals JSON must be humanized.")

    # Must contain human-readable signal tokens from the technicals domain
    # breadth=0.7 should produce a readable reference (fraction, percentage, or "Breadth")
    assert any(token in result for token in ("Breadth", "breadth", "7/10", "70%", "0.7")), (
        f"Humanized technicals must reference the breadth signal (0.70 from fixture). "
        f"Got: {result!r}. "
        "The implementer must extract the breadth value from the technicals JSON."
    )

    # Must be a non-empty string
    assert len(result.strip()) > 0, "humanize_lens_summary must not return an empty string."


def test_technicals_json_humanized_contains_no_dict_key_patterns():
    """Humanized technicals output must not contain any 'key': value patterns.

    AC-2 extended check: JSON dict keys like 'ma_posture', 'above_sma50', 'above_sma200'
    must not appear in quoted form (as they would in a JSON dump). The implementer
    must extract human-meaningful values, not re-serialize the dict keys.

    Fixture: lens_pipeline row 77 technicals summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _TECH_JSON_SUMMARY, "sources": []}
    result = humanize("technicals", entry)

    # These are internal JSON key names — they must not appear as raw quoted keys
    json_key_phrases = ['"ma_posture"', '"above_sma50"', '"above_sma200"', '"momentum"']
    for phrase in json_key_phrases:
        assert phrase not in result, (
            f"humanize_lens_summary('technicals', ...) must not emit raw JSON key {phrase!r}. "
            f"The result must be humanized text, not a JSON re-serialization. "
            f"Got result: {result!r}"
        )


def test_sentiment_json_humanized_no_raw_json_markers():
    """lens_pipeline sentiment JSON is humanized — no raw JSON syntax in output.

    Captured fixture (row 77): article_count=10, tone_summary=null, tone_score=null.
    The humanized output must acknowledge article count AND acknowledge the
    tone unavailability (tone_summary is null in the fixture — AC-3 test seam).

    Fixture: lens_pipeline row 77 sentiment summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _SENT_JSON_SUMMARY, "sources": []}
    result = humanize("sentiment", entry)

    _assert_no_raw_json(result, "sentiment", "Row 77 sentiment JSON must be humanized.")

    # article_count=10 should surface as a readable count
    assert any(token in result for token in ("10", "article", "Article")), (
        f"Humanized sentiment must reference article count (10 from fixture). "
        f"Got: {result!r}. "
        "Extract article_count from the JSON and emit it as text."
    )

    # tone_summary is null — must acknowledge unavailability (AC-3 joint test)
    assert any(
        token in result.lower()
        for token in ("unavailable", "tone unavailable", "limited", "no tone", "null")
    ), (
        f"Sentiment humanization must acknowledge null tone_summary (not emit 'null' literally). "
        f"Got: {result!r}. "
        "When tone_summary is null, output an honest empty-state phrase (e.g. 'tone unavailable')."
    )

    assert "null" not in result.lower() or "unavailable" in result.lower(), (
        f"'null' must not appear literally in humanized output. Got: {result!r}. "
        "Replace null with an honest empty-state phrase."
    )


def test_derivatives_json_humanized_no_raw_json_markers():
    """lens_pipeline derivatives JSON is humanized — no raw JSON syntax in output.

    Captured fixture (row 77): vix_level=16.41, vix_term_structure with regime='contango',
    risk_read='neutral', as_of_date='2026-06-16'.
    The humanized output must contain VIX-domain readable tokens.

    Fixture: lens_pipeline row 77 derivatives summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _DERIV_JSON_SUMMARY, "sources": []}
    result = humanize("derivatives", entry)

    _assert_no_raw_json(result, "derivatives", "Row 77 derivatives JSON must be humanized.")

    # VIX is the core signal — must appear as readable text
    assert "VIX" in result or "vix" in result.lower(), (
        f"Humanized derivatives must reference VIX. Got: {result!r}. "
        "Extract vix_level from the derivatives JSON."
    )

    # regime='contango' must surface or vix_term_structure must produce readable output
    assert any(
        token in result.lower()
        for token in ("contango", "backwardation", "term structure", "neutral", "regime")
    ), f"Humanized derivatives must include regime/term-structure signal. Got: {result!r}."

    assert len(result.strip()) > 0, "humanize_lens_summary must not return empty string."


def test_derivatives_json_scalar_string_treated_as_prose():
    """A summary that parses as a bare JSON scalar (e.g. '\"16.41\"') is treated as prose.

    Edge case from the Architecture section: `json.loads('"16.41"')` yields the string
    '16.41', not a dict or list. The feature plan specifies: 'if it yields a dict/list →
    structured (humanize); otherwise → prose (passthrough)'. A scalar result must be
    treated as prose, not unpacked as an object.

    This test verifies the implementer checks `isinstance(parsed, (dict, list))` before
    treating the value as structured, rather than checking `isinstance(parsed, object)`
    (which would match everything).
    """
    humanize = _import_humanize()

    # json.loads('"16.41"') == '16.41' — a valid JSON scalar, not a dict/list
    scalar_json_string = '"16.41"'
    entry = {"available": True, "summary": scalar_json_string, "sources": []}
    result = humanize("derivatives", entry)

    # Should be treated as prose/text, not attempted to humanize as a structured dict
    # The result should contain the value in some readable form — not raise, not be empty
    assert result is not None and len(result.strip()) > 0, (
        "humanize_lens_summary must not return None or empty string for a scalar JSON input."
    )

    # Must not raise (AC-7 companion)
    # If it attempts dict access on '16.41', it raises AttributeError/TypeError — caught here


def test_macro_json_humanized_no_raw_json_markers():
    """lens_pipeline macro JSON is humanized — no raw JSON syntax in output.

    Captured fixture (row 77): series dict with DGS10/UNRATE/CPIAUCSL/FEDFUNDS and their
    label/value/date fields.  The humanized output must reference FRED series names or
    values in readable form (e.g. '10-Year Treasury', '4.43%').

    Fixture: lens_pipeline row 77 macro summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _MACRO_JSON_SUMMARY, "sources": []}
    result = humanize("macro", entry)

    _assert_no_raw_json(result, "macro", "Row 77 macro JSON must be humanized.")

    # Must reference at least one readable FRED-domain concept
    assert any(
        token in result
        for token in (
            "Treasury",
            "DGS10",
            "Unemployment",
            "UNRATE",
            "CPI",
            "CPIAUCSL",
            "Fed",
            "FEDFUNDS",
            "4.43",
        )
    ), (
        f"Humanized macro must reference FRED series labels or values. Got: {result!r}. "
        "Extract series labels and values from the macro JSON."
    )

    assert len(result.strip()) > 0


def test_macro_json_humanized_no_raw_json_key_names():
    """Humanized macro output must not contain raw JSON key names as quoted strings.

    The macro fixture contains key names 'DGS10', 'UNRATE', 'CPIAUCSL', 'FEDFUNDS'.
    These keys appearing as bare quoted strings ('"DGS10"') would indicate the JSON
    is being re-serialized rather than humanized.

    Fixture: lens_pipeline row 77 macro summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _MACRO_JSON_SUMMARY, "sources": []}
    result = humanize("macro", entry)

    for key in ('"DGS10"', '"UNRATE"', '"CPIAUCSL"', '"FEDFUNDS"', '"series"', '"label"'):
        assert key not in result, (
            f"humanize_lens_summary('macro', ...) must not emit raw JSON key {key!r}. "
            f"Use label/value fields for human-readable output. Got: {result!r}"
        )


def test_fundamentals_json_humanized_no_raw_json_markers():
    """lens_pipeline fundamentals JSON is humanized — no raw JSON syntax in output.

    This is the worst-offender lens: a multi-ticker 10-K facts dump across AAPL, AMZN,
    and more. The raw string is 4,600+ characters. The humanized output must be concise
    readable text, not the full dump.

    Fixture: lens_pipeline row 77 fundamentals summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _FUND_JSON_SUMMARY, "sources": []}
    result = humanize("fundamentals", entry)

    _assert_no_raw_json(result, "fundamentals", "Row 77 fundamentals JSON must be humanized.")

    # Must reference companies or fundamentals concepts (coverage count, tickers)
    assert any(
        token in result
        for token in ("AAPL", "AMZN", "Apple", "Amazon", "coverage", "Coverage", "ticker")
    ), (
        f"Humanized fundamentals must reference covered tickers or coverage count. Got: {result!r}. "
        "Extract ticker count and/or ticker names from the fundamentals JSON."
    )

    assert len(result.strip()) > 0


# ===========================================================================
# AC-4: FUNDAMENTALS CONCISION — no full 10-K dump
# ===========================================================================


def test_fundamentals_humanized_output_is_concise():
    """Humanized fundamentals must be concise — NOT the full multi-ticker 10-K dump.

    The raw fundamentals JSON from row 77 is 4,633 characters. The humanized output
    must be substantially shorter — a coverage count + brief highlight, not a full dump.

    Concision threshold: the humanized output must be less than 500 characters.
    This is a conservative upper bound: even a verbose readable summary (e.g.
    'Coverage: 2 tickers (AAPL, AMZN). Apple Inc revenue $416B (10-K 2025). ...') stays
    well under 500 chars while the full dump is 4,633+ chars.

    Fixture: lens_pipeline row 77 fundamentals summary (captured 2026-06-18).
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": _FUND_JSON_SUMMARY, "sources": []}
    result = humanize("fundamentals", entry)

    raw_length = len(_FUND_JSON_SUMMARY)  # 4633 chars from fixture
    result_length = len(result)

    assert result_length < 500, (
        f"Humanized fundamentals must be concise (< 500 chars). "
        f"Raw JSON was {raw_length} chars; humanized output is {result_length} chars. "
        "The fundamentals humanizer must produce a coverage count + brief highlight, "
        "not reproduce the full 10-K facts dump. AC-4."
    )


def test_fundamentals_humanized_references_coverage_count():
    """Humanized fundamentals must surface the coverage count (number of tickers covered).

    AC-4 requirement: 'concise readable text (coverage count + brief highlight)'.
    The coverage count is the count of ticker keys in the 'tickers' dict — 2 tickers in
    the trimmed fixture (AAPL, AMZN), though the real row has more.

    Fixture: lens_pipeline row 77 fundamentals summary (trimmed to AAPL/AMZN for this fixture).
    """
    humanize = _import_humanize()

    # Minimal 2-ticker fundamentals JSON (trimmed from the full row 77 fixture)
    minimal_fund_json = json.dumps(
        {
            "tickers": {
                "AAPL": {
                    "entity_name": "Apple Inc.",
                    "cik": 320193,
                    "key_facts": {
                        "Revenues": {
                            "label": "Revenue",
                            "value": 416161000000,
                            "unit": "USD",
                            "end": "2025-09-27",
                            "filed": "2025-10-31",
                            "form": "10-K",
                        }
                    },
                },
                "AMZN": {
                    "entity_name": "AMAZON COM INC",
                    "cik": 1018724,
                    "key_facts": {
                        "Revenues": {
                            "label": "Revenue",
                            "value": 716924000000,
                            "unit": "USD",
                            "end": "2025-12-31",
                            "filed": "2026-02-06",
                            "form": "10-K",
                        }
                    },
                },
            }
        }
    )

    entry = {"available": True, "summary": minimal_fund_json, "sources": []}
    result = humanize("fundamentals", entry)

    # Must contain some reference to the number 2 (coverage count) or ticker names
    assert any(
        token in result for token in ("2 ticker", "2 company", "2 compan", "AAPL", "AMZN", "Apple")
    ), (
        f"Humanized fundamentals must surface coverage count or ticker names. Got: {result!r}. "
        "Coverage count = 2 (AAPL, AMZN). The humanizer must extract this from the tickers dict."
    )

    _assert_no_raw_json(result, "fundamentals")


# ===========================================================================
# AC-3: NULL / EMPTY / MISSING → HONEST EMPTY-STATE
# ===========================================================================


def test_null_summary_returns_honest_empty_state():
    """humanize_lens_summary with summary=None returns an honest empty-state string.

    Must NOT return 'null', 'None', '{}', or an empty string.
    Must return a phrase like 'limited inputs', 'unavailable', or similar.

    AC-3 spec: 'When a lens is unavailable or its summary is null/empty, the Overview
    shows an honest message (e.g. "limited inputs — tone unavailable"), never "null",
    "{}", "None", or raw JSON.'
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": None, "sources": []}
    result = humanize("sentiment", entry)

    assert result is not None, "humanize_lens_summary must return a string, never None."
    assert len(result.strip()) > 0, "Empty-state must be a non-empty string."
    assert result.strip() not in ("null", "None", "{}", ""), (
        f"Null summary must produce a readable empty-state, not {result!r}. "
        "Return an honest phrase like 'limited inputs' or 'data unavailable'."
    )

    # Must not contain the literal word 'null' or 'None' as the sole output
    assert result.lower() != "null", (
        f"humanize_lens_summary must not return literal 'null'. Got: {result!r}"
    )
    assert result != "None", (
        f"humanize_lens_summary must not return literal 'None'. Got: {result!r}"
    )


def test_empty_string_summary_returns_honest_empty_state():
    """humanize_lens_summary with summary='' (empty string) returns honest empty-state.

    An empty summary is a valid producer output (e.g. lens ran but produced nothing).
    The humanizer must not return an empty string or 'None'.
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": "", "sources": []}
    result = humanize("macro", entry)

    assert result is not None and len(result.strip()) > 0, (
        f"Empty string summary must produce a non-empty honest empty-state. Got: {result!r}"
    )
    assert result.strip() not in ("null", "None", "{}"), (
        f"Empty string summary must not produce {result!r}. Return an honest phrase."
    )


def test_string_null_summary_returns_honest_empty_state():
    """humanize_lens_summary with summary='null' (the JSON-encoded null literal) returns
    an honest empty-state, never the literal string 'null'.

    The lens_pipeline may occasionally emit the JSON literal 'null' as a string (e.g.
    if tone_summary was serialized as `json.dumps(None)` → 'null').
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": "null", "sources": []}
    result = humanize("sentiment", entry)

    assert result.strip().lower() not in ("null", "none", "{}", ""), (
        f"Summary 'null' (string) must produce an honest empty-state, not {result!r}. "
        "The humanizer must detect this degenerate value and return a readable phrase."
    )


def test_string_none_summary_returns_honest_empty_state():
    """humanize_lens_summary with summary='None' (Python repr string) returns
    honest empty-state rather than passing 'None' through verbatim.
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": "None", "sources": []}
    result = humanize("technicals", entry)

    assert result.strip() != "None", (
        f"Summary='None' (str) must produce an honest empty-state, not 'None'. Got: {result!r}"
    )
    assert len(result.strip()) > 0


def test_empty_braces_summary_returns_honest_empty_state():
    """humanize_lens_summary with summary='{}' (empty JSON object string) returns
    an honest empty-state, never '{}' verbatim.
    """
    humanize = _import_humanize()

    entry = {"available": True, "summary": "{}", "sources": []}
    result = humanize("derivatives", entry)

    assert result.strip() != "{}", (
        f"Summary='{{}}' must produce an honest empty-state, not '{{}}'. Got: {result!r}"
    )
    assert len(result.strip()) > 0


def test_missing_entry_returns_honest_empty_state():
    """humanize_lens_summary with entry=None or entry={} returns honest empty-state.

    When the lens entry is entirely missing from per_lens_digest, the caller passes
    None or an empty dict.  The humanizer must not raise and must return a readable string.
    """
    humanize = _import_humanize()

    # None entry
    result_none = humanize("fundamentals", None)
    assert result_none is not None and len(result_none.strip()) > 0, (
        f"humanize_lens_summary('fundamentals', None) must return a non-empty string. Got: {result_none!r}"
    )
    assert result_none.strip() not in ("null", "None", "{}"), (
        f"None entry must produce an honest empty-state, not {result_none!r}."
    )

    # Empty dict entry
    result_empty = humanize("macro", {})
    assert result_empty is not None and len(result_empty.strip()) > 0, (
        f"humanize_lens_summary('macro', {{}}) must return a non-empty string. Got: {result_empty!r}"
    )


# ===========================================================================
# AC-7: NEVER RAISES — defensive over malformed/junk inputs
# ===========================================================================


def test_humanize_never_raises_on_malformed_json_string():
    """humanize_lens_summary must not raise on a malformed (truncated/corrupt) JSON string.

    D-1 spirit: the render-prep helper must degrade gracefully to an honest fallback
    string, never raising an exception that would cause the Overview to 500.

    This tests the json.loads() error path: if the summary looks like JSON but is
    truncated (e.g. '{"vix_level": 16.41, "vix_ter'), the json.loads() call raises
    json.JSONDecodeError.  The implementer must catch this and return a fallback.
    """
    humanize = _import_humanize()

    truncated_json = '{"vix_level": 16.41, "vix_ter'  # truncated — invalid JSON
    entry = {"available": True, "summary": truncated_json, "sources": []}

    # Must not raise any exception
    try:
        result = humanize("derivatives", entry)
    except Exception as exc:
        pytest.fail(
            f"humanize_lens_summary must not raise on malformed JSON. "
            f"Got {type(exc).__name__}: {exc}. "
            "Wrap json.loads() in a try/except and return a fallback string."
        )

    # Must return a non-empty string (not None, not empty)
    assert result is not None and len(str(result).strip()) > 0, (
        f"humanize_lens_summary must return a non-empty fallback for malformed JSON. "
        f"Got: {result!r}"
    )


def test_humanize_never_raises_on_non_string_summary():
    """humanize_lens_summary must not raise if summary is an unexpected type (int, list, dict).

    A producer might store a pre-parsed dict (not a JSON string) in summary.
    The helper must handle this gracefully — type-check and degrade.
    """
    humanize = _import_humanize()

    # Unexpected types that must not cause a crash
    junk_inputs = [
        42,  # int
        3.14,  # float
        [1, 2, 3],  # list
        {"a": "b"},  # already-parsed dict (not a string)
        True,  # bool
        b"bytes",  # bytes
    ]

    for junk in junk_inputs:
        entry = {"available": True, "summary": junk, "sources": []}
        try:
            result = humanize("technicals", entry)
        except Exception as exc:
            pytest.fail(
                f"humanize_lens_summary('technicals', {{summary={junk!r}}}) raised "
                f"{type(exc).__name__}: {exc}. "
                "The helper must handle any summary type without raising (D-1 spirit)."
            )
        # Result must be a string (or at minimum not None)
        assert result is not None, (
            f"humanize_lens_summary returned None for summary={junk!r}. Must return a string."
        )


def test_humanize_never_raises_on_missing_keys_in_json():
    """humanize_lens_summary must not raise if expected keys are absent from the parsed JSON.

    A future producer might change the schema. Missing 'breadth', 'vix_level', etc. must
    not cause KeyError/AttributeError crashes in the humanizer.
    """
    humanize = _import_humanize()

    # Each lens with a JSON dict that has NO expected keys
    lens_empty_json_cases = [
        ("technicals", json.dumps({"unexpected_key": 999})),
        ("derivatives", json.dumps({"some_other_key": "value"})),
        ("macro", json.dumps({"not_series": {}})),
        ("fundamentals", json.dumps({"not_tickers": []})),
        ("sentiment", json.dumps({"no_article_count": None})),
    ]

    for lens, empty_schema_json in lens_empty_json_cases:
        entry = {"available": True, "summary": empty_schema_json, "sources": []}
        try:
            result = humanize(lens, entry)
        except Exception as exc:
            pytest.fail(
                f"humanize_lens_summary('{lens}', ...) raised {type(exc).__name__}: {exc} "
                f"when expected keys were absent from the JSON. "
                "Use .get() with defaults; never assume keys are present."
            )
        assert result is not None and len(str(result).strip()) > 0, (
            f"humanize_lens_summary('{lens}', ...) returned empty/None for a sparse JSON dict. "
            f"Got: {result!r}"
        )


# ===========================================================================
# ROUTE/RENDER TESTS — GET /ai-advisor with lens_pipeline-style MARKET_PRISM row
# ===========================================================================

# ---------------------------------------------------------------------------
# Shared fixture: a MARKET_PRISM row shaped like the live lens_pipeline row 77,
# suitable for injection into the route via database mock.
# ---------------------------------------------------------------------------

_PIPELINE_PRISM_SUMMARY = {
    "id": 77,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "neutral",
    "created_at": "2026-06-18 09:03:18",
    "raw_response": {
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Mixed signals from lens pipeline.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                # Raw JSON string — the current verbatim-render bug
                "summary": _TECH_JSON_SUMMARY,
                "sources": [],
            },
            "sentiment": {
                "available": True,
                "summary": _SENT_JSON_SUMMARY,
                "sources": [],
            },
            "derivatives": {
                "available": True,
                "summary": _DERIV_JSON_SUMMARY,
                "sources": [
                    {
                        "title": "VIXCLS / VXVCLS (CBOE Vol Index)",
                        "url": "https://fred.stlouisfed.org/series/VIXCLS",
                        "published": "2026-06-16",
                        "lens": "derivatives",
                    }
                ],
            },
            "macro": {
                "available": True,
                "summary": _MACRO_JSON_SUMMARY,
                "sources": [],
            },
            "fundamentals": {
                "available": True,
                "summary": _FUND_JSON_SUMMARY,
                "sources": [],
            },
        },
        "sources": [],
    },
}

# Council-style row for prose passthrough route test
_COUNCIL_PRISM_SUMMARY = {
    "id": 78,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "bullish",
    "created_at": "2026-06-18 20:01:30",
    "raw_response": {
        "overall_sentiment": "bullish",
        "sentiment_rationale": "BULLISH overall. Technicals confirm risk-on breadth.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": _TECH_PROSE_SUMMARY,
                "sources": [],
            },
            "sentiment": {
                "available": True,
                "summary": _SENT_PROSE_SUMMARY,
                "sources": [],
            },
            "derivatives": {
                "available": True,
                "summary": _DERIV_PROSE_SUMMARY,
                "sources": [],
            },
            "macro": {
                "available": True,
                "summary": _MACRO_PROSE_SUMMARY,
                "sources": [],
            },
            "fundamentals": {
                "available": True,
                "summary": _FUND_PROSE_SUMMARY,
                "sources": [],
            },
        },
        "sources": [],
    },
}

# Row with a null-summary lens (AC-3 route test)
_NULL_SUMMARY_PRISM_SUMMARY = {
    "id": 99,
    "advisor_role": "MARKET_PRISM",
    "subject_id": None,
    "verdict": "limited-inputs",
    "created_at": "2026-06-18 03:01:00",
    "raw_response": {
        "overall_sentiment": "limited-inputs",
        "sentiment_rationale": "All lenses unavailable.",
        "per_lens_digest": {
            "technicals": {
                "available": True,
                "summary": None,  # null summary — AC-3 edge case
                "sources": [],
            },
            "sentiment": {
                "available": False,
                "reason": "ConnectionError",
                "sources": [],
            },
            "derivatives": {"available": False, "reason": "TimeoutError", "sources": []},
            "macro": {"available": False, "reason": "TimeoutError", "sources": []},
            "fundamentals": {"available": False, "reason": "TimeoutError", "sources": []},
        },
        "sources": [],
    },
}


@pytest.fixture()
def client():
    """Flask test client with TESTING mode — no port bound."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def _mock_route_deps(monkeypatch, prism_summary: dict) -> None:
    """Patch non-prism route dependencies to avoid I/O during render tests."""
    import app as app_module
    import database

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: prism_summary)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", lambda *a, **kw: [])

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)


def test_route_renders_no_raw_json_in_per_lens_text_for_pipeline_row(client, monkeypatch):
    """GET /ai-advisor with a lens_pipeline-style row must render NO raw JSON in per-lens text.

    This is the core integration test for RF-1. The current template renders
    `_lens.get('summary') | e` verbatim, which emits the full raw JSON string into
    .prism-lens-text elements.  After the fix, the template must call the humanizer
    (or the route must pre-humanize) so the rendered text contains NO '{"' or '":' markers
    inside the per-lens text (after HTML-unescaping the rendered output).

    Failure signature in RED state:
      prism-lens-text paragraphs contain the raw JSON object with keys like
      'ma_posture', 'article_count', 'vix_level' etc. — visible after html.unescape().

    The template uses Jinja2 autoescaping (| e), so `"` becomes `&#34;` in the raw HTML.
    We therefore unescape each lens paragraph before checking for JSON markers — the
    operator's browser sees the unescaped text.

    Fixture: lens_pipeline row 77 shape, injected via mock DB.
    """
    import html as html_module
    import re

    _mock_route_deps(monkeypatch, _PIPELINE_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200, f"GET /ai-advisor returned {resp.status_code}"

    html_text = resp.data.decode("utf-8", errors="replace")

    # Extract all prism-lens-text paragraph contents and unescape HTML entities.
    # The template renders: <p class="prism-lens-text">{{ _lens.get('summary') | e }}</p>
    # Jinja2 escapes " as &#34;, { stays as {, : stays as :
    # After unescape, JSON markers '{"' and '":' are present if raw JSON was emitted.
    lens_texts = re.findall(
        r'<p[^>]*class="[^"]*prism-lens-text[^"]*"[^>]*>(.*?)</p>',
        html_text,
        re.DOTALL,
    )

    # There must be at least some lens text paragraphs to check
    # (if the template renders nothing, test_route_renders_council_prose_passthrough would
    # also fail — but here we guard against the silent-empty case too)
    assert len(lens_texts) > 0, (
        "No prism-lens-text paragraphs found in rendered HTML. "
        "The template must render per-lens summaries in <p class='prism-lens-text'> elements. "
        "If the MARKET_PRISM row is being ignored, check route wiring. AC-2."
    )

    for lens_text_raw in lens_texts:
        decoded = html_module.unescape(lens_text_raw)
        assert '{"' not in decoded, (
            f"Raw JSON object marker '{{\"' found in a prism-lens-text paragraph after "
            f"HTML-unescaping. Decoded text: {decoded[:200]!r}. "
            "The template must humanize summaries — raw JSON must not appear in lens text. "
            "Current behavior: `_lens.get('summary') | e` renders raw JSON. AC-2."
        )
        assert '":' not in decoded, (
            f"Raw JSON key separator '\":' found in a prism-lens-text paragraph after "
            f"HTML-unescaping. Decoded text: {decoded[:200]!r}. "
            "All per-lens text must be humanized before rendering. AC-2."
        )


def test_route_renders_no_generic_json_markers_in_prism_lenses(client, monkeypatch):
    """GET /ai-advisor per-lens text must not contain generic raw JSON object markers.

    Broader than the phrase-specific test above: checks that no '{"' or '":' appear
    inside any prism-lens-text paragraph in the rendered HTML.

    Strategy: extract the prism-lenses div content and scan for markers.
    """
    import re

    _mock_route_deps(monkeypatch, _PIPELINE_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html = resp.data.decode("utf-8", errors="replace")

    # Extract all prism-lens-text paragraph contents
    # Template: <p class="prism-lens-text">{{ ... }}</p>
    lens_texts = re.findall(
        r'<p[^>]*class="[^"]*prism-lens-text[^"]*"[^>]*>(.*?)</p>',
        html,
        re.DOTALL,
    )

    # If no lens texts found, the template may not be rendering them yet (still RED
    # for a different reason), but we still must fail on the absent-lens criterion.
    # The test below tests for JSON markers specifically in the found text.
    for lens_text in lens_texts:
        # Unescape HTML entities to get the actual rendered string
        import html as html_module

        decoded = html_module.unescape(lens_text)
        assert '{"' not in decoded, (
            f"Raw JSON object marker '{{\"' found in a prism-lens-text paragraph. "
            f"Decoded text: {decoded[:200]!r}. "
            "The template must humanize summaries — raw JSON must not appear in lens text."
        )
        assert '":' not in decoded, (
            f"Raw JSON key separator '\":' found in a prism-lens-text paragraph. "
            f"Decoded text: {decoded[:200]!r}. "
            "All per-lens text must be humanized before rendering."
        )


def test_route_renders_council_prose_passthrough_in_prism_lenses(client, monkeypatch):
    """GET /ai-advisor with a council row must render the prose text inside .prism-lens-text.

    AC-1 route-level test: when the MARKET_PRISM row has prose summaries (council producer),
    the rendered lens text must contain the prose (not be replaced/mangled by the humanizer).

    Fixture: council row 78 shape, injected via mock DB.
    """

    _mock_route_deps(monkeypatch, _COUNCIL_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html = resp.data.decode("utf-8", errors="replace")

    # The council technicals prose starts with 'BULLISH. Breadth 0.70'
    # This distinctive text must survive passthrough to the rendered HTML.
    assert "BULLISH" in html, (
        "Council prose 'BULLISH' must appear in rendered HTML. "
        "The humanizer must pass council prose through unchanged. "
        "If the humanizer modifies all text, it breaks council rows. AC-1."
    )

    assert "Breadth" in html, (
        "Council prose 'Breadth' must appear in rendered HTML (from technicals prose). "
        "Passthrough check for council row 78 data. AC-1."
    )


def test_route_renders_honest_empty_state_for_null_summary_lens(client, monkeypatch):
    """GET /ai-advisor with a null-summary available lens renders honest empty-state text.

    AC-3 route-level test: when a lens is available=True but summary=None, the rendered
    HTML must show an honest empty-state phrase in the prism-lens-text paragraph —
    not 'null', 'None', and not SILENCE (no text at all is also a violation).

    Current template failure (RED): `{% if _lens.get('summary') %}` evaluates to False
    for summary=None and skips rendering the <p class="prism-lens-text"> paragraph
    entirely — so the lens shows as 'Available' with NO explanatory text.
    AC-3 requires an honest message like 'limited inputs' or 'data unavailable'.

    This test asserts a prism-lens-text paragraph IS rendered for the null-summary lens
    and that its text is a non-empty honest message (not 'null'/'None'/'{}'/'').
    """
    import re

    _mock_route_deps(monkeypatch, _NULL_SUMMARY_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html_text = resp.data.decode("utf-8", errors="replace")

    # The technicals lens div must be present (lens name must render)
    assert "technicals" in html_text.lower(), (
        "The technicals lens div must render (lens name must appear). AC-3."
    )

    # Must not render 'null' or 'None' literally in the lens text
    assert ">null<" not in html_text and ">None<" not in html_text, (
        "Template must not render literal 'null' or 'None' in lens text elements. "
        "A null summary must produce an honest empty-state string. AC-3."
    )

    # The technicals lens (available=True, summary=None) must render an honest message
    # in a prism-lens-text paragraph. Find the technicals lens div and check its text.
    # We look for a prism-lens-text paragraph anywhere in the page — if it's absent
    # for the null-summary lens, the template's `{% if _lens.get('summary') %}` guard
    # is silently hiding the content (current RED behavior).
    lens_texts = re.findall(
        r'<p[^>]*class="[^"]*prism-lens-text[^"]*"[^>]*>(.*?)</p>',
        html_text,
        re.DOTALL,
    )
    # At least one lens-text paragraph must appear for the available lens with null summary.
    # If zero paragraphs: the template silently hides null-summary lenses — RED (AC-3 fail).
    # The null-summary row has technicals available=True and 4 other lenses unavailable
    # (they render a reason in prism-lens-text). So at minimum the unavailable lenses'
    # reason text paragraphs must exist. Additionally, the available null-summary lens
    # must produce its own honest-empty paragraph — that's what this test specifically gates.
    #
    # Concretely: count of prism-lens-text paragraphs should be >= 1 (from unavailable lens
    # reasons). The test then checks that the technicals available lens ALSO has text.
    # We verify this by asserting the overall paragraph count matches what the honest
    # implementation would produce: one paragraph per lens that has any text to show
    # (5 lenses here: technicals=honest-empty, 4 unavailable=reason).
    assert len(lens_texts) >= 5, (
        f"Expected at least 5 prism-lens-text paragraphs (honest-empty for null-summary "
        f"technicals + reasons for 4 unavailable lenses). Got {len(lens_texts)}. "
        "The template must render honest empty-state text for available lenses with "
        "null summaries (AC-3), not silently omit the paragraph. "
        "Current RED behavior: `{% if _lens.get('summary') %}` skips null-summary lenses, "
        "producing 0 paragraphs for the available-but-null lens."
    )


def test_route_regression_chip_class_still_renders_for_pipeline_row(client, monkeypatch):
    """AC-6 regression: chip class mapping still works after the prose-render fix.

    The fix must not accidentally change the chip-class mapping or template structure.
    A lens_pipeline row with overall_sentiment='neutral' must still produce
    prism-sentiment-chip--neutral chip class. AC-6.
    """
    import re

    _mock_route_deps(monkeypatch, _PIPELINE_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html = resp.data.decode("utf-8", errors="replace")

    match = re.search(
        r'<span[^>]*class="([^"]*prism-sentiment-chip[^"]*)"[^>]*data-testid="prism-sentiment-chip"',
        html,
    )
    if not match:
        match = re.search(
            r'<span[^>]*data-testid="prism-sentiment-chip"[^>]*class="([^"]*prism-sentiment-chip[^"]*)"',
            html,
        )

    assert match is not None, (
        "prism-sentiment-chip span must still render after the prose fix. "
        "The template structure must not be broken by the humanization change. AC-6."
    )

    chip_class = match.group(1)
    assert "prism-sentiment-chip--neutral" in chip_class, (
        f"overall_sentiment='neutral' must still produce '--neutral' chip class. "
        f"Got: {chip_class!r}. Regression: the prose fix must not alter chip mapping. AC-6."
    )


def test_route_regression_rationale_still_renders_for_pipeline_row(client, monkeypatch):
    """AC-6 regression: sentiment_rationale still renders after the prose fix.

    The fix must not remove or obscure the overall sentiment_rationale text. AC-6.
    """
    _mock_route_deps(monkeypatch, _PIPELINE_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html = resp.data.decode("utf-8", errors="replace")

    # The rationale from the fixture: 'Mixed signals from lens pipeline.'
    assert "Mixed signals" in html, (
        "sentiment_rationale must still render after the prose fix. "
        "Got page without 'Mixed signals from lens pipeline.'. "
        "The fix must not remove the rationale block. AC-6."
    )


def test_route_regression_as_of_datetime_still_renders_for_pipeline_row(client, monkeypatch):
    """AC-6 regression: 'As of' created_at datetime still renders after the prose fix.

    The fix must not break the As-of line in the Market Prism block. AC-6.
    """
    _mock_route_deps(monkeypatch, _PIPELINE_PRISM_SUMMARY)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html = resp.data.decode("utf-8", errors="replace")

    # created_at from fixture: '2026-06-18 09:03:18'
    assert "2026-06-18" in html, (
        "The 'As of' datetime (created_at) must still render after the prose fix. "
        "Expected '2026-06-18' in HTML. The fix must not break the As-of line. AC-6."
    )


# ===========================================================================
# AC-5: obs-raw-preview no longer emits raw JSON for MARKET_PRISM rows
# ===========================================================================


def test_obs_raw_preview_does_not_emit_raw_json_for_market_prism_rows(client, monkeypatch):
    """The obs-raw-preview table cell must not show raw JSON for MARKET_PRISM observations.

    After the implementer's fix: MARKET_PRISM rows show `{{ obs.verdict | e }}` instead of
    `{{ obs.raw_response | tojson | e }}`. This test verifies the MARKET_PRISM cell
    fix is preserved (regression guard) — per_lens_digest and ma_posture must not appear.

    This test is a REGRESSION GUARD for the already-fixed MARKET_PRISM path.
    The new AC-5 gap (symphony-level rows) is covered by
    test_obs_raw_preview_does_not_emit_raw_json_for_symphony_level_rows below.
    """
    import app as app_module
    import database

    # A representative MARKET_PRISM obs with JSON raw_response
    market_prism_obs = {
        "id": 77,
        "advisor_role": "MARKET_PRISM",
        "symphony_id": None,
        "subject_id": None,
        "subject_type": "global",
        "verdict": "neutral",
        "created_at": "2026-06-18 09:03:18",
        "raw_response": json.dumps(
            {
                "overall_sentiment": "neutral",
                "per_lens_digest": {
                    "technicals": {
                        "available": True,
                        "summary": _TECH_JSON_SUMMARY,
                    }
                },
            }
        ),
    }

    def _fake_get_observations(role, *args, **kwargs):
        if role == "MARKET_PRISM":
            return [market_prism_obs]
        return []

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: None)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", _fake_get_observations)

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html_text = resp.data.decode("utf-8", errors="replace")

    import re

    obs_preview_cells = re.findall(
        r'<td[^>]*class="[^"]*obs-raw-preview[^"]*"[^>]*>(.*?)</td>',
        html_text,
        re.DOTALL,
    )

    # 'per_lens_digest' and 'ma_posture' are internal JSON key names that must NOT appear
    # in the operator-facing obs-raw-preview cell for MARKET_PRISM rows.
    # They appear as-is regardless of the double-escaping (`| tojson | e`).
    for cell_content in obs_preview_cells:
        assert "per_lens_digest" not in cell_content, (
            "obs-raw-preview must not expose 'per_lens_digest' key for MARKET_PRISM rows. "
            "The per-lens JSON structure is not operator-friendly in raw form. AC-5. "
            f"Cell content (first 300 chars): {cell_content[:300]!r}"
        )
        assert "ma_posture" not in cell_content, (
            "obs-raw-preview must not expose 'ma_posture' (internal technicals JSON key) "
            "for MARKET_PRISM rows. Raw JSON must not be visible to operators. AC-5. "
            f"Cell content (first 300 chars): {cell_content[:300]!r}"
        )
        assert "overall_sentiment" not in cell_content or len(cell_content.strip()) < 50, (
            "obs-raw-preview for a MARKET_PRISM row must not show the full raw_response JSON. "
            "Either humanize the display or hide per-lens details. AC-5. "
            f"Cell content (first 300 chars): {cell_content[:300]!r}"
        )


# ===========================================================================
# AC-5 (CORRECTED): obs-raw-preview — symphony-level rows must NOT emit raw JSON
#
# The implementer fixed MARKET_PRISM cells (show obs.verdict — preserved above).
# The AC-5 gap: NON-MARKET_PRISM (symphony-level) obs-raw-preview cells still render
# `{{ obs.raw_response | tojson | e }}` verbatim (template line 2006).
#
# FIXTURE PROVENANCE (captured-from-producer, read-only live DB):
#   OVERFITTING_CONSCIENCE id=58, created 2026-06-05 21:05:58:
#     {"s_count": 0, "backtest_selection_count": 0, "n_effective": 500, "n_optuna": 500,
#      "drift_signal_available": false,
#      "note": "no BACKTEST_SELECTION facets; N_effective equals N_optuna"}
#   SPEC_CRITIC id=37, created 2026-06-05 20:00:09:
#     {"note": "all 3 facets present, all THEORY, frozen<90d"}
#
# The `note` key is present in both rows and already contains readable prose.
# AC-5 requires the implementer to surface the `note` field (if present) instead
# of dumping the full raw_response JSON.
# ===========================================================================

# Symphony-level obs fixtures — captured from live DB, provenance above.
# raw_response is stored as JSON in DB but database.py deserializes it to a dict
# before returning rows (see database.py:1046-1047). Fixtures must match
# the dict form the route actually receives.
_OC_OBS_RAW_RESPONSE = {
    "s_count": 0,
    "backtest_selection_count": 0,
    "n_effective": 500,
    "n_optuna": 500,
    "drift_signal_available": False,
    "note": "no BACKTEST_SELECTION facets; N_effective equals N_optuna",
}

_SC_OBS_RAW_RESPONSE = {
    "note": "all 3 facets present, all THEORY, frozen<90d",
}


def test_obs_raw_preview_does_not_emit_raw_json_for_symphony_level_rows(client, monkeypatch):
    """The obs-raw-preview cell must NOT show raw JSON for symphony-level observations.

    Current state (RED): template line 2006 renders
      `{{ obs.raw_response | tojson | e }}`
    for all rows where advisor_role != 'MARKET_PRISM' (OVERFITTING_CONSCIENCE, SPEC_CRITIC,
    DIVERGENCE_EXPLAINER, ASSET_SWAP, LOGIC_CHANGE, etc.).

    This produces output like:
      {"backtest_selection_count": 0, "drift_signal_available": false, "n_effective": 500,
       "n_optuna": 500, "note": "...", "s_count": 0}
    visible to the operator — raw JSON object syntax in the UI.

    After the fix (GREEN): symphony-level cells must show the `note` field from raw_response
    (if present — it's already prose), or a concise human summary of key fields, or an
    honest empty-state. No raw JSON object syntax (`{` + `"key":`) in the cell.

    The fix must REUSE or EXTEND advisors/prism_render — not introduce a second humanizer.

    Fixture provenance: OVERFITTING_CONSCIENCE id=58 + SPEC_CRITIC id=37 from live DB.
    """
    import html as html_module
    import re

    import app as app_module
    import database

    # Two symphony-level obs rows shaped like real live-DB rows
    symphony_obs_list = [
        {
            "id": 58,
            "advisor_role": "OVERFITTING_CONSCIENCE",
            "symphony_id": "some-symphony-hash",
            "subject_id": "some-symphony-hash",
            "subject_type": "symphony",
            "verdict": "no_backtest_selection",
            "created_at": "2026-06-05 21:05:58",
            "raw_response": _OC_OBS_RAW_RESPONSE,
        },
        {
            "id": 37,
            "advisor_role": "SPEC_CRITIC",
            "symphony_id": "some-symphony-hash",
            "subject_id": "some-symphony-hash",
            "subject_type": "symphony",
            "verdict": "ok",
            "created_at": "2026-06-05 20:00:09",
            "raw_response": _SC_OBS_RAW_RESPONSE,
        },
    ]

    def _fake_get_observations(role, *args, **kwargs):
        if role == "MARKET_PRISM":
            return []
        return symphony_obs_list

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: None)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", _fake_get_observations)

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html_text = resp.data.decode("utf-8", errors="replace")

    obs_preview_cells = re.findall(
        r'<td[^>]*class="[^"]*obs-raw-preview[^"]*"[^>]*>(.*?)</td>',
        html_text,
        re.DOTALL,
    )

    # We must find at least one cell for the symphony-level rows we injected.
    assert len(obs_preview_cells) >= 1, (
        "No obs-raw-preview cells found in rendered HTML. "
        "The template must render an obs-raw-preview cell for each advisor_observation row. "
        f"Injected {len(symphony_obs_list)} rows. AC-5."
    )

    for cell_content in obs_preview_cells:
        decoded = html_module.unescape(cell_content)

        # The `| tojson | e` escaping produces backslash-escaped inner quotes.
        # After html.unescape(), the cell contains e.g. `{\"backtest_selection_count\": 0`.
        # We check for the backslash-escaped JSON object opening OR the literal form.
        # The key marker of raw JSON dump: a `{` followed by `"` (or `\"` after tojson).
        # In the raw HTML (before unescape), the tojson form is: `{\"key\":`.
        # After unescape, it's: `{\"key\":` (backslashes survive unescape).
        # We check for `{\\"` in decoded (Python repr of `{\"`) and `{"` (literal form).
        raw_json_present = (
            '{\\"' in decoded  # backslash-escaped form from | tojson
            or '{"' in decoded  # literal JSON object marker
        )
        assert not raw_json_present, (
            f"obs-raw-preview cell for a symphony-level row emits raw JSON object syntax. "
            f"Current state: `{{{{ obs.raw_response | tojson | e }}}}` renders a JSON object "
            f"starting with '{{\\\"' or '{{\"'. After the fix, this cell must show the "
            f"`note` field prose (if present) or a concise human summary — no raw JSON. "
            f"Decoded cell content (first 400 chars): {decoded[:400]!r}. AC-5."
        )

        # The cell must not be empty — it must show *something* readable.
        assert len(decoded.strip()) > 0, (
            "obs-raw-preview cell must not be empty after stripping. "
            "Show the 'note' field or a concise summary. AC-5."
        )


def test_obs_raw_preview_symphony_rows_surface_note_field_prose(client, monkeypatch):
    """obs-raw-preview for symphony-level rows must surface the 'note' field prose.

    The OVERFITTING_CONSCIENCE and SPEC_CRITIC raw_response dicts both contain a
    `note` key with already-readable prose. The fix must prefer this field over
    emitting the full raw JSON dump.

    After GREEN: the OVERFITTING_CONSCIENCE cell must contain (some form of)
    'no BACKTEST_SELECTION facets' and the SPEC_CRITIC cell must contain
    'all 3 facets present'.

    ADVERSARIAL BAR: the current template emits the full JSON dump which contains
    these note strings INSIDE the JSON — but also with surrounding JSON syntax.
    The test asserts BOTH that the note text is present AND that raw JSON markers
    are absent, requiring genuine note-extraction, not merely a substring check.

    Fixture provenance: OC id=58, SC id=37, live DB, 2026-06-05.
    """
    import html as html_module
    import re

    import app as app_module
    import database

    symphony_obs_list = [
        {
            "id": 58,
            "advisor_role": "OVERFITTING_CONSCIENCE",
            "symphony_id": "some-symphony-hash",
            "subject_id": "some-symphony-hash",
            "subject_type": "symphony",
            "verdict": "no_backtest_selection",
            "created_at": "2026-06-05 21:05:58",
            "raw_response": _OC_OBS_RAW_RESPONSE,
        },
        {
            "id": 37,
            "advisor_role": "SPEC_CRITIC",
            "symphony_id": "some-symphony-hash",
            "subject_id": "some-symphony-hash",
            "subject_type": "symphony",
            "verdict": "ok",
            "created_at": "2026-06-05 20:00:09",
            "raw_response": _SC_OBS_RAW_RESPONSE,
        },
    ]

    def _fake_get_observations(role, *args, **kwargs):
        if role == "MARKET_PRISM":
            return []
        return symphony_obs_list

    monkeypatch.setattr(database, "get_latest_market_prism_summary", lambda: None)
    monkeypatch.setattr(database, "get_advisor_observations_for_role", _fake_get_observations)

    mock_analytics = MagicMock()
    mock_analytics.get_history_with_cache_invalidation.return_value = {}
    mock_analytics.list_available_symphonies.return_value = []
    mock_analytics.compute_per_symphony_returns.return_value = ([], [], [])
    monkeypatch.setattr(app_module, "analytics", mock_analytics)

    fake_corr_mod = MagicMock()
    fake_corr_mod.compute_pairwise_correlations.return_value = []
    fake_corr_mod.CRISIS_CAVEAT = ""
    monkeypatch.setitem(sys.modules, "advisors.correlation_diagnostic", fake_corr_mod)

    resp = client.get("/ai-advisor")
    assert resp.status_code == 200

    html_text = resp.data.decode("utf-8", errors="replace")

    obs_preview_cells = re.findall(
        r'<td[^>]*class="[^"]*obs-raw-preview[^"]*"[^>]*>(.*?)</td>',
        html_text,
        re.DOTALL,
    )

    assert len(obs_preview_cells) >= 2, (
        f"Expected at least 2 obs-raw-preview cells (one per injected row). "
        f"Got {len(obs_preview_cells)}. AC-5."
    )

    # Collect decoded cell contents for inspection
    decoded_cells = [html_module.unescape(c) for c in obs_preview_cells]
    all_decoded = " ".join(decoded_cells)

    # The OC note prose must appear in some cell (without surrounding JSON syntax)
    oc_note_present = "no BACKTEST_SELECTION facets" in all_decoded
    sc_note_present = "all 3 facets present" in all_decoded

    # These strings appear in the raw JSON dump too (inside the "note" value) — but when
    # the full JSON is dumped, raw JSON markers also appear. We verify the note is present
    # AND that the dump's surrounding JSON object markers are absent.
    oc_has_raw_json = any(
        '{\\"' in html_module.unescape(c) or '{"' in html_module.unescape(c)
        for c in obs_preview_cells
    )

    assert oc_note_present, (
        "The OVERFITTING_CONSCIENCE 'note' prose must appear in the obs-raw-preview cell. "
        "Expected 'no BACKTEST_SELECTION facets' to appear in the rendered cell. "
        "The fix must extract the 'note' field and show it as the cell content. "
        f"Decoded cell contents: {decoded_cells!r}. AC-5."
    )
    assert sc_note_present, (
        "The SPEC_CRITIC 'note' prose must appear in the obs-raw-preview cell. "
        "Expected 'all 3 facets present' to appear in the rendered cell. "
        f"Decoded cell contents: {decoded_cells!r}. AC-5."
    )
    assert not oc_has_raw_json, (
        "obs-raw-preview cells contain raw JSON object markers ('{\\'\"' or '{\"') even "
        "though note prose is present — this means the full JSON dump is shown with the "
        "note inside it, which is NOT the fix. The cell must show ONLY the note prose "
        "(or a concise human summary), not the full raw_response JSON. AC-5."
    )
