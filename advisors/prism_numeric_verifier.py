"""
Post-council numeric verifier (DE-PRISM-NUMERIC-VERIFY-001).

Recomputes every numeric indicator the Prism council cites in its MARKET_PRISM
observation against the authoritative source payload (FRED, GDELT, Alpaca bars,
SEC EDGAR) and classifies each citation as pass / flagged / overridden /
unverifiable. This is an anti-fabrication guard: an LLM-authored synthesis can
misstate a number it read correctly (or invent one), and nothing upstream of
this module checks that the prose matches the data.

verify_cited_numbers(run_id, market_prism_row, lens_sections=None) -> dict is
the public entry point; persist_verification(run_id, result) -> int | None
writes the result as an append-only MARKET_PRISM_VERIFICATION advisor_observations
row (never mutates the MARKET_PRISM row itself).

D-1 contract: every degraded path surfaces type(exc).__name__ only — never a
raw exception message (FRED embeds the API key in a request URL; see
ai_advisor._build_macro_section's own docstring discipline). Off-execution-path:
never imported from alpha_bot_execution.py. Advisory-only.
"""

from __future__ import annotations

import re
import sys
from datetime import UTC, datetime

# ---------------------------------------------------------------------------
# Named constants — no magic numbers (AC-15)
# ---------------------------------------------------------------------------

# Tolerances are absolute point-diffs unless the registry entry's comparison_type
# is "relative" (fractional diff), in which case the tolerance is a fraction.

_VIX_TOLERANCE = 0.5  # VIX spot points; noise band for a headline vol print
_VIX_TERM_TOLERANCE = 0.5  # VIX3M/VXVCLS (3-month) points, same magnitude as spot
_RATE_TOLERANCE = 0.05  # percentage points; stable daily FRED rate series (DGS10/10Y/FEDFUNDS)
_UNRATE_TOLERANCE = 0.2  # percentage points; UNRATE is reported to 1 decimal, wider noise band
_CPI_TOLERANCE = 1.0  # CPI index points (index ~300-level, not a percent)
# Deliberately LARGER than _RATE_TOLERANCE (AC-13): GDELT tone drifts between
# council-time and verify-time as the rolling artlist window moves — a strict
# tolerance here would misclassify legitimate drift as a mismatch (the exact
# false-override failure mode DE-PRISM-SOURCES-001 already warned against).
_TONE_TOLERANCE = 0.15
_BREADTH_TOLERANCE = 0.05  # breadth is a 0-1 fraction
# Large-magnitude fundamentals figures ($ revenue, etc.) use a RELATIVE
# tolerance — a fixed absolute tolerance would either falsely flag routine
# rounding on a $391B figure or pass a real error on a $1M one.
_FUNDAMENTALS_RELATIVE_TOLERANCE = 0.05  # 5% relative

# Upper bound of the "bounded mismatch" (flagged) band: diff <= _OVERRIDE_FACTOR
# * tolerance -> flagged; beyond that -> overridden (gross mismatch, prefer the
# ground-truth value for render).
_OVERRIDE_FACTOR = 3.0

# DoS guard: an oversized cited_numbers list (malicious or buggy council output)
# is bounded rather than processed unboundedly.
_MAX_CITED_NUMBERS = 100

# Sentinel registry key for the dynamic "<TICKER>.<CONCEPT>" fundamentals indicator
# shape (e.g. "AAPL.Revenues") — not a literal indicator string, matched via regex.
_FUNDAMENTALS_WILDCARD_KEY = "<TICKER>.<CONCEPT>"

_TICKER_CONCEPT_RE = re.compile(r"^[A-Za-z0-9]+\.[A-Za-z0-9_]+$")

# indicator -> (truth_lens, payload_path, comparison_type, tolerance).
# payload_path is a dotted path into lens_sections[truth_lens]["payload"];
# comparison_type is "absolute" or "relative"; tolerance's units match
# comparison_type. The wildcard entry's payload_path is None — resolved at
# call time via _resolve_payload_path for the "<TICKER>.<CONCEPT>" shape.
_INDICATOR_REGISTRY: dict[str, tuple[str, str | None, str, float]] = {
    "VIX": ("derivatives", "vix_level", "absolute", _VIX_TOLERANCE),
    "VIXCLS": ("derivatives", "vix_level", "absolute", _VIX_TOLERANCE),
    "VXVCLS": ("derivatives", "vix_term_structure.term_3m", "absolute", _VIX_TERM_TOLERANCE),
    "VIX3M": ("derivatives", "vix_term_structure.term_3m", "absolute", _VIX_TERM_TOLERANCE),
    "DGS10": ("macro", "series.DGS10.value", "absolute", _RATE_TOLERANCE),
    "10Y": ("macro", "series.DGS10.value", "absolute", _RATE_TOLERANCE),
    "UNRATE": ("macro", "series.UNRATE.value", "absolute", _UNRATE_TOLERANCE),
    "CPIAUCSL": ("macro", "series.CPIAUCSL.value", "absolute", _CPI_TOLERANCE),
    "CPI": ("macro", "series.CPIAUCSL.value", "absolute", _CPI_TOLERANCE),
    "FEDFUNDS": ("macro", "series.FEDFUNDS.value", "absolute", _RATE_TOLERANCE),
    "tone": ("sentiment", "tone_score", "absolute", _TONE_TOLERANCE),
    "breadth": ("technicals", "breadth", "absolute", _BREADTH_TOLERANCE),
    _FUNDAMENTALS_WILDCARD_KEY: (
        "fundamentals",
        None,
        "relative",
        _FUNDAMENTALS_RELATIVE_TOLERANCE,
    ),
}

# lens -> ai_advisor builder attribute name. Looked up via getattr() at call
# time (never a pre-bound function reference) so test-time
# patch.object(ai_advisor, "_build_X_section", ...) is always honored.
_LENS_BUILDER_ATTR: dict[str, str] = {
    "derivatives": "_build_derivatives_section",
    "macro": "_build_macro_section",
    "sentiment": "_build_sentiment_section",
    "technicals": "_build_technicals_section",
    "fundamentals": "_build_fundamentals_section",
}


def _empty_summary() -> dict:
    return {"n_checks": 0, "n_pass": 0, "n_flagged": 0, "n_overridden": 0, "n_unverifiable": 0}


def _unavailable_lens_block(lens: str, reason: str) -> dict:
    return {"lens": lens, "available": False, "reason": reason, "payload": None, "sources": []}


def _lookup_registry_entry(indicator: object) -> tuple[str, str | None, str, float] | None:
    """Resolve an indicator string to its registry entry, or None if unmapped.

    A literal registry key (VIX, DGS10, ...) is matched directly. Anything
    matching the "<TICKER>.<CONCEPT>" shape (e.g. "AAPL.Revenues") that is NOT
    itself a literal key falls back to the fundamentals wildcard entry.
    Anything else (including non-string indicators) is unmapped.
    """
    if not isinstance(indicator, str):
        return None
    if indicator in _INDICATOR_REGISTRY:
        return _INDICATOR_REGISTRY[indicator]
    if _TICKER_CONCEPT_RE.match(indicator):
        return _INDICATOR_REGISTRY[_FUNDAMENTALS_WILDCARD_KEY]
    return None


def _resolve_payload_path(path_template: str | None, indicator: str) -> str:
    """Return the concrete dotted payload path for this indicator.

    Literal registry entries carry their own path_template verbatim. The
    fundamentals wildcard entry carries path_template=None — the concrete
    path is built from the indicator string itself ("AAPL.Revenues" ->
    "tickers.AAPL.key_facts.Revenues.value").
    """
    if path_template is not None:
        return path_template
    ticker, concept = indicator.split(".", 1)
    return f"tickers.{ticker}.key_facts.{concept}.value"


def _resolve_dotted_path(payload: object, path: str) -> object:
    """Walk a dotted path through nested dicts; return None on any miss."""
    cur = payload
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _safe_normalize(value: object) -> float | None:
    """Coerce a cited or ground-truth value to float; None on anything malformed.

    Handles FRED's string-typed series values ("4.35") transparently. Rejects
    bool (a subclass of int in Python but never a valid citable number), dicts,
    lists, and non-numeric strings without raising.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _classify(cited: float, truth: float, tolerance: float, comparison_type: str) -> str:
    """pass iff |diff| <= tolerance; flagged iff |diff| <= _OVERRIDE_FACTOR *
    tolerance (both boundaries inclusive); else overridden. "relative"
    comparison_type normalizes the diff by |truth| before comparing."""
    if comparison_type == "relative":
        denom = abs(truth) if truth != 0 else 1.0
        diff = abs(cited - truth) / denom
    else:
        diff = abs(cited - truth)
    if diff <= tolerance:
        return "pass"
    if diff <= _OVERRIDE_FACTOR * tolerance:
        return "flagged"
    return "overridden"


def _fetch_lens_live(lens: str) -> dict:
    """Live single-lens fetch via ai_advisor, used only when the caller did not
    supply lens_sections. D-1: any failure (including an unknown lens) degrades
    to an unavailable block with reason=type(exc).__name__ only."""
    try:
        import ai_advisor  # noqa: PLC0415

        attr = _LENS_BUILDER_ATTR.get(lens)
        if attr is None:
            return _unavailable_lens_block(lens, "NoBuilderForLens")
        builder = getattr(ai_advisor, attr)
        return builder()
    except Exception as exc:  # noqa: BLE001
        return _unavailable_lens_block(lens, type(exc).__name__)


def _build_check(entry: dict, get_lens) -> dict:
    """Build one check dict for a single cited_numbers entry.

    get_lens(lens) -> dict returns that lens's section (from the caller's
    lens_sections when supplied, else a live per-call cached fetch).
    """
    indicator = entry.get("indicator")
    citing_lens = entry.get("lens")
    cited_value = _safe_normalize(entry.get("value"))

    registry_entry = _lookup_registry_entry(indicator)
    if registry_entry is None:
        return {
            "indicator": indicator,
            "lens": citing_lens,
            "cited_value": cited_value,
            "ground_truth_value": None,
            "classification": "unverifiable",
        }

    truth_lens, path_template, comparison_type, tolerance = registry_entry
    section = get_lens(truth_lens)
    if not section or not section.get("available"):
        return {
            "indicator": indicator,
            "lens": citing_lens,
            "cited_value": cited_value,
            "ground_truth_value": None,
            "classification": "unverifiable",
        }

    path = _resolve_payload_path(path_template, indicator)
    ground_truth_value = _safe_normalize(_resolve_dotted_path(section.get("payload") or {}, path))

    if cited_value is None or ground_truth_value is None:
        return {
            "indicator": indicator,
            "lens": citing_lens,
            "cited_value": cited_value,
            "ground_truth_value": ground_truth_value,
            "classification": "unverifiable",
        }

    classification = _classify(cited_value, ground_truth_value, tolerance, comparison_type)
    return {
        "indicator": indicator,
        "lens": citing_lens,
        "cited_value": cited_value,
        "ground_truth_value": ground_truth_value,
        "classification": classification,
    }


def _summarize(checks: list[dict]) -> dict:
    summary = _empty_summary()
    summary["n_checks"] = len(checks)
    for check in checks:
        classification = check.get("classification")
        if classification in ("pass", "flagged", "overridden", "unverifiable"):
            summary[f"n_{classification}"] += 1
    return summary


def _derive_verdict(summary: dict) -> str:
    if summary["n_overridden"] > 0:
        return "overrides-detected"
    if summary["n_flagged"] > 0:
        return "flags-detected"
    if summary["n_checks"] > 0 and summary["n_pass"] == 0:
        # nvreview Finding 1: every check resolved unverifiable (malformed entries,
        # unmapped indicators, or every source unavailable) — nothing was actually
        # verified clean. "clean" implies "checked and all passed"; returning it
        # here would be a silent pass in an anti-fabrication feature.
        return "no-verifiable-claims"
    return "clean"


def verify_cited_numbers(
    run_id: str, market_prism_row: dict | None, lens_sections: dict | None = None
) -> dict:
    """Recompute every cited_numbers claim in a MARKET_PRISM row against the
    authoritative source payloads.

    lens_sections, when supplied, is a {lens: section} bundle reused verbatim
    (no builder re-invocation, AC-4) — the caller's own patch-time fetch. When
    None, ground truth is fetched live, lazily, and only for lenses actually
    referenced by a cited indicator (cached per-call so a repeated lens is
    fetched at most once).

    Returns {"checks": [...], "summary": {...}, "verdict": <str>}. A row with
    no cited_numbers key returns verdict="no-numeric-claims" and an empty
    check list (AC-11) — never treated as an error.

    D-1 never-raises: any unexpected failure degrades to an empty-checks
    result; run_id is accepted for signature symmetry with persist_verification
    but is not otherwise consulted here (the row already carries its own
    run_id via the caller's context).
    """
    try:
        raw = (market_prism_row or {}).get("raw_response") or {}
        if isinstance(raw, str):
            try:
                import json as _json  # noqa: PLC0415

                raw = _json.loads(raw)
            except Exception:  # noqa: BLE001
                raw = {}

        cited_numbers = raw.get("cited_numbers")
        if not cited_numbers:
            return {"checks": [], "summary": _empty_summary(), "verdict": "no-numeric-claims"}
        if not isinstance(cited_numbers, list):
            # nvreview Finding 1: a truthy-but-malformed container (dict, string, ...)
            # has nothing iterable as {indicator, value, lens} tuples — degrade the
            # same as "no numeric claims" rather than silently reading its dict keys
            # or string characters as entries.
            return {"checks": [], "summary": _empty_summary(), "verdict": "no-numeric-claims"}

        bounded = list(cited_numbers)[:_MAX_CITED_NUMBERS]

        _lens_cache: dict = {}

        def _get_lens(lens: str) -> dict:
            if lens_sections is not None:
                return lens_sections.get(lens)
            if lens in _lens_cache:
                return _lens_cache[lens]
            section = _fetch_lens_live(lens)
            _lens_cache[lens] = section
            return section

        # nvreview Finding 1: never silently drop a malformed entry — a non-dict
        # entry (e.g. 42, "foo") is coerced to {} so it surfaces as its own
        # "unverifiable" check instead of vanishing from the list entirely.
        checks = [
            _build_check(entry if isinstance(entry, dict) else {}, _get_lens) for entry in bounded
        ]
        summary = _summarize(checks)
        verdict = _derive_verdict(summary)
        return {"checks": checks, "summary": summary, "verdict": verdict}
    except Exception as exc:  # noqa: BLE001
        # D-1: type name only — never str(exc), which may embed a credential-bearing URL.
        print(f"[prism_numeric_verifier] VerifyError: {type(exc).__name__}", file=sys.stderr)
        return {"checks": [], "summary": _empty_summary(), "verdict": "no-numeric-claims"}


def persist_verification(run_id: str, result: dict) -> int | None:
    """Persist an append-only MARKET_PRISM_VERIFICATION advisor_observations row.

    Idempotent: skips the INSERT (returns None) when a VERIFICATION row already
    exists for this run_id (AC-8). Never touches the MARKET_PRISM row itself —
    D-2 append-only invariant. D-1 never-raises.
    """
    try:
        import database  # noqa: PLC0415

        existing = database.get_latest_market_prism_verification_for_run(run_id)
        if existing is not None:
            return None

        raw_response = {
            "run_id": run_id,
            "verified_at": datetime.now(UTC).isoformat(),
            "checks": result.get("checks", []),
            "summary": result.get("summary", _empty_summary()),
            "verdict": result.get("verdict"),
        }
        return database.insert_advisor_observation(
            advisor_role="MARKET_PRISM_VERIFICATION",
            subject_type="portfolio",
            subject_id="global",
            verdict=None,
            raw_response=raw_response,
            symphony_id="",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[prism_numeric_verifier] PersistError: {type(exc).__name__}", file=sys.stderr)
        return None
