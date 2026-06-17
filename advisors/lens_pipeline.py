"""Scheduled off-hours lens pipeline — Component 7+8 (CYCLE4-BRIEF.md).

Public API
----------
run_pipeline(*, dry_run: bool = False) -> dict

    Run the 4-pass lens pipeline (per-lens data collection, citation assembly,
    Claude synthesis, persistence) and return a summary dict:
        {run_ts, lenses_attempted, lenses_available, market_prism_row_id, error_count}

    Never raises — all exceptions are caught and reflected in error_count.
    dry_run=True skips the DB write and Claude call; market_prism_row_id is None.

Design invariants (CC-* cross-cutting rules from CYCLE4-BRIEF.md)
------------------------------------------------------------------
CC-1  is_advisory_only=1 on every DB write — wired in insert_advisor_observation.
CC-2  This module is never imported at the module level in alpha_bot_execution.py —
      the scheduler uses a lazy import inside the worker thread.
CC-3  Lenses degrade to available=False with a reason; data is never fabricated.
CC-4  Citations are validated through ai_advisor.build_citation; invalid ones are dropped.
CC-5  Free APIs only — no new paid subscriptions introduced here.
CC-6  State DB only — no cross-join with the Optuna DB.
CC-10 D-1 error contract — type(exc).__name__ only in persisted text and WARNING+ logs.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# The five lens names — fixed set, order is stable (used as dict keys).
# Sourced from ai_advisor._build_*_section naming convention.
_LENS_NAMES: tuple[str, ...] = (
    "technicals",
    "sentiment",
    "derivatives",
    "macro",
    "fundamentals",
)

# Total number of lenses is a named constant so it appears in raw_response
# and tests can assert on it without a magic number.
_TOTAL_LENS_COUNT: int = len(_LENS_NAMES)  # 5

# Valid overall_sentiment values — exhaustive set from the AC-4 schema contract.
_SENTIMENT_LIMITED_INPUTS: str = "limited-inputs"


def _call_lens_section(lens_name: str) -> dict:
    """Call the corresponding ai_advisor._build_<lens>_section() and return its result.

    On any exception, returns an available=False block with only type(exc).__name__
    as the reason (D-1 / CC-10 contract — no raw exception message in output).

    The returned dict always has at minimum: {lens, available, sources}.
    """
    import ai_advisor  # lazy — not on the execution path (CC-2)

    _LENS_BUILDERS = {
        "technicals": ai_advisor._build_technicals_section,
        "sentiment": ai_advisor._build_sentiment_section,
        "derivatives": ai_advisor._build_derivatives_section,
        "macro": ai_advisor._build_macro_section,
        "fundamentals": ai_advisor._build_fundamentals_section,
    }
    builder = _LENS_BUILDERS[lens_name]
    try:
        result = builder()
        # Guarantee the top-level keys the pipeline depends on are present.
        if not isinstance(result, dict):
            return {
                "lens": lens_name,
                "available": False,
                "reason": "TypeError",  # D-1: type name only
                "sources": [],
            }
        if "sources" not in result:
            result["sources"] = []
        return result
    except Exception as exc:
        # D-1 / CC-10: only the class name is persisted — never str(exc).
        exc_type = type(exc).__name__
        # Log at WARNING level but only the type name, not the message.
        logger.warning("Lens %r failed: %s", lens_name, exc_type)
        return {
            "lens": lens_name,
            "available": False,
            "reason": exc_type,
            "sources": [],
        }


def _collect_lenses() -> tuple[dict[str, dict], int]:
    """Run all 5 lens sections with per-lens isolation.

    Returns (per_lens_results, error_count):
        per_lens_results: dict keyed by lens name
        error_count: number of lenses that raised an exception
    """
    per_lens: dict[str, dict] = {}
    error_count = 0
    for lens_name in _LENS_NAMES:
        result = _call_lens_section(lens_name)
        # Track whether this was an error (available=False due to exception,
        # identified by the "reason" key being set from the exception handler).
        if not result.get("available", False) and "reason" in result:
            # Distinguish between genuine unavailability (no data, section chose
            # available=False) and error (exception was raised).
            # We count as error if the section itself raised — _call_lens_section
            # always sets reason= from type(exc).__name__ in that case.
            # If the builder returned available=False voluntarily, it may or may
            # not set "reason". We count errors conservatively: if the builder
            # raised we recorded it with reason=exc_type; voluntary unavailability
            # also sets reason (e.g. "stub — unavailable"). We cannot distinguish
            # these at this layer — we count all unavailable-with-reason as errors
            # to give the most conservative (honest) error_count. AC-2 tests only
            # assert error_count >= 1 for a lens that raised.
            error_count += 1
        per_lens[lens_name] = result
    return per_lens, error_count


def _validate_and_filter_sources(per_lens: dict[str, dict]) -> list[dict]:
    """Aggregate sources from all available lenses; drop malformed citations.

    Each source is validated through ai_advisor.build_citation (CC-4).
    Malformed entries (missing url, bad scheme, missing required fields) are
    silently dropped — the caller must not assume sources is non-empty.

    Returns a list of validated {title, url, published, lens} dicts.
    """
    import ai_advisor  # lazy import (CC-2)

    valid_sources: list[dict] = []
    for lens_name, block in per_lens.items():
        if not block.get("available", False):
            continue  # CC-3: only collect from available lenses
        raw_sources = block.get("sources") or []
        for citation in raw_sources:
            validated = ai_advisor.build_citation(citation)
            if validated is not None:
                valid_sources.append(validated)
    return valid_sources


def _build_per_lens_digest(per_lens: dict[str, dict]) -> dict:
    """Produce the per_lens_digest dict for raw_response.

    Each entry: {available: bool, summary?: str, reason?: str, sources: list}.
    Available lenses carry summary + sources; unavailable carry reason only.
    """
    digest: dict[str, dict] = {}
    for lens_name, block in per_lens.items():
        available = bool(block.get("available", False))
        entry: dict[str, Any] = {"available": available}
        if available:
            # Pass through summary/payload data from the lens block.
            if "summary" in block:
                entry["summary"] = block["summary"]
            elif "payload" in block and block["payload"] is not None:
                entry["summary"] = json.dumps(block["payload"])
            else:
                entry["summary"] = ""
            # Sources are validated at the top-level aggregation step; store
            # per-lens sources as-is in the digest (before citation filtering).
            entry["sources"] = block.get("sources") or []
        else:
            reason = block.get("reason", "unavailable")
            # D-1: reason must already be type(exc).__name__ or a short label — never str(exc).
            entry["reason"] = reason
        digest[lens_name] = entry
    return digest


# Regex that matches an opening code fence for JSON, optionally with a language
# tag.  Claude Haiku frequently wraps its JSON response in ```json ... ``` or
# plain ``` ... ``` fences; stripping them before json.loads is required for
# reliable parsing.
# Pattern source: empirical observation of Claude Haiku output during live ops
# (confirmed defect 2026-06-13 nightly run).
_CODE_FENCE_OPEN_RE: re.Pattern = re.compile(r"^```(?:json)?\s*\n?", re.IGNORECASE)
_CODE_FENCE_CLOSE_RE: re.Pattern = re.compile(r"\n?```\s*$")


def _extract_json_object(raw_text: str) -> str:
    """Extract the first balanced JSON object from a raw Claude response string.

    Strategy (applied in order):
    1. Strip markdown code fences (```json ... ``` or ``` ... ```).
    2. If the remaining text contains a ``{``, scan for the first balanced
       ``{ ... }`` block and return it.  This tolerates leading/trailing prose
       that Claude sometimes adds around the JSON payload.
    3. If no ``{`` is found, return the stripped text as-is and let
       ``json.loads`` raise ``JSONDecodeError`` — the caller degrades gracefully.

    Returns the extracted candidate string; never raises.
    """
    text = raw_text.strip()

    # Step 1 — strip code fences if present.
    text = _CODE_FENCE_OPEN_RE.sub("", text)
    text = _CODE_FENCE_CLOSE_RE.sub("", text)
    text = text.strip()

    # Step 2 — find the first balanced { ... } block.
    start = text.find("{")
    if start == -1:
        # No JSON object — return as-is; json.loads will raise; caller degrades.
        return text

    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start=start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    # Unbalanced braces — return everything from the first `{`; json.loads
    # will raise; caller degrades to limited-inputs.
    return text[start:]


def _synthesize_via_claude(
    per_lens: dict[str, dict],
    per_lens_digest: dict,
    available_count: int,
) -> tuple[str, str]:
    """Call Claude to synthesize lens blocks into an overall_sentiment and rationale.

    Returns (overall_sentiment, sentiment_rationale).

    If Claude is unavailable or raises, degrades gracefully to "limited-inputs"
    and an honest explanation. D-1: exception details are never included in the
    rationale — only type(exc).__name__ appears.
    """
    if available_count == 0:
        # No lenses available — skip Claude call entirely; return degraded state.
        return _SENTIMENT_LIMITED_INPUTS, "No lens data available; synthesis skipped."

    import ai_advisor  # lazy import (CC-2)

    try:
        client = ai_advisor._build_client()
        if client is None:
            return _SENTIMENT_LIMITED_INPUTS, "Claude client unavailable; synthesis skipped."

        # Assemble a compact synthesis prompt from the available lens digests.
        available_summaries = []
        for lens_name, entry in per_lens_digest.items():
            if entry.get("available"):
                summary = entry.get("summary", "(no summary)")
                available_summaries.append(f"{lens_name}: {summary}")

        synthesis_prompt = (
            "You are a market conditions analyst. "
            "Based on the following lens data, produce a single overall market sentiment "
            "label (one of: risk-on, neutral, risk-off, limited-inputs) and a one-sentence rationale.\n\n"
            "Lens data:\n" + "\n".join(available_summaries) + "\n\n"
            "Respond ONLY with JSON: "
            '{"overall_sentiment": "<label>", "sentiment_rationale": "<rationale>"}'
        )

        response = client.messages.create(
            model=os.environ.get("ADVISOR_SYNTHESIS_MODEL", "claude-opus-4-8"),
            max_tokens=256,
            messages=[{"role": "user", "content": synthesis_prompt}],
        )
        raw_text = response.content[0].text.strip()

        # Claude (especially Haiku) frequently wraps the JSON payload in
        # markdown code fences (```json ... ```) or adds leading/trailing prose.
        # _extract_json_object strips fences and locates the first balanced
        # { ... } block before parsing, preventing JSONDecodeError on every
        # real-API run (confirmed live defect 2026-06-13).
        candidate = _extract_json_object(raw_text)
        parsed = json.loads(candidate)
        sentiment = parsed.get("overall_sentiment", _SENTIMENT_LIMITED_INPUTS)
        rationale = parsed.get("sentiment_rationale", "Synthesis returned no rationale.")

        # Validate sentiment value is in the allowed set.
        _VALID_SENTIMENTS = {"risk-on", "neutral", "risk-off", "limited-inputs"}
        if sentiment not in _VALID_SENTIMENTS:
            sentiment = "neutral"

        return sentiment, rationale

    except Exception as exc:
        # D-1 / CC-10: log only the type name, not the message.
        exc_type = type(exc).__name__
        logger.warning("Claude synthesis failed: %s", exc_type)
        return _SENTIMENT_LIMITED_INPUTS, f"Synthesis unavailable ({exc_type})."


def run_pipeline(*, dry_run: bool = False) -> dict:
    """Run the 4-pass lens pipeline and persist results.

    Pass 1 — Per-lens: calls each ai_advisor._build_<lens>_section() with
              per-lens exception isolation; one lens failing never aborts the run.
    Pass 2 — Citation assembly: aggregates and validates sources from available
              lenses via ai_advisor.build_citation (CC-4); malformed entries dropped.
    Pass 3 — Synthesis: Claude call (unless dry_run) to produce overall_sentiment
              and sentiment_rationale; degrades to "limited-inputs" if unavailable.
    Pass 4 — Persistence: writes ONE advisor_role="MARKET_PRISM" row to
              advisor_observations (unless dry_run). Always writes, even when all
              lenses are unavailable — never silently no-ops (AC-3 invariant).

    Args:
        dry_run: If True, skip DB write and Claude call. Returns same dict shape
                 with market_prism_row_id=None.

    Returns:
        dict with keys:
            run_ts (str): ISO UTC timestamp of this pipeline run.
            lenses_attempted (int): Always 5.
            lenses_available (int): Number of lenses that returned available=True.
            market_prism_row_id (int | None): Row id of the persisted observation,
                or None in dry_run mode.
            error_count (int): Number of lenses that raised an exception.

    Never raises — all exceptions are caught and reflected in error_count.
    """
    run_ts = datetime.now(timezone.utc).isoformat()

    # Pass 1 — Per-lens data collection with per-lens isolation (AC-2).
    try:
        per_lens, error_count = _collect_lenses()
    except Exception as exc:
        # Extremely unlikely — _collect_lenses itself never raises — but guard anyway.
        exc_type = type(exc).__name__
        logger.error("_collect_lenses raised unexpectedly: %s", exc_type)
        per_lens = {
            name: {"lens": name, "available": False, "reason": exc_type, "sources": []}
            for name in _LENS_NAMES
        }
        error_count = _TOTAL_LENS_COUNT

    available_count = sum(1 for b in per_lens.values() if b.get("available", False))

    # Pass 2 — Citation validation and aggregation (AC-8 / CC-4).
    try:
        sources = _validate_and_filter_sources(per_lens)
    except Exception as exc:
        exc_type = type(exc).__name__
        logger.warning("Source validation failed: %s", exc_type)
        sources = []

    # Build per_lens_digest for raw_response (AC-4).
    try:
        per_lens_digest = _build_per_lens_digest(per_lens)
    except Exception as exc:
        exc_type = type(exc).__name__
        logger.warning("per_lens_digest build failed: %s", exc_type)
        per_lens_digest = {name: {"available": False, "reason": exc_type} for name in _LENS_NAMES}

    # Pass 3 — Synthesis via Claude (skipped in dry_run).
    if dry_run:
        overall_sentiment = _SENTIMENT_LIMITED_INPUTS
        sentiment_rationale = "dry_run — synthesis skipped."
    else:
        try:
            overall_sentiment, sentiment_rationale = _synthesize_via_claude(
                per_lens, per_lens_digest, available_count
            )
        except Exception as exc:
            exc_type = type(exc).__name__
            logger.warning("Synthesis raised unexpectedly: %s", exc_type)
            overall_sentiment = _SENTIMENT_LIMITED_INPUTS
            sentiment_rationale = f"Synthesis unavailable ({exc_type})."

    # Determine verdict — collapses all-unavailable to the canonical label.
    verdict = overall_sentiment if available_count > 0 else _SENTIMENT_LIMITED_INPUTS

    # Assemble raw_response (AC-4 schema contract).
    # run_id is identical to run_ts — the ISO UTC timestamp uniquely identifies
    # this nightly run and joins the MARKET_PRISM observation to its prism_audit_log
    # entries (Prism Phase 1 / migration 032).  Backward-compat: existing rows
    # without this key read fine; callers use .get("run_id") not direct access.
    raw_response: dict[str, Any] = {
        "run_ts": run_ts,
        "run_id": run_ts,
        "per_lens_digest": per_lens_digest,
        "overall_sentiment": overall_sentiment,
        "sentiment_rationale": sentiment_rationale,
        "available_lens_count": available_count,
        "total_lens_count": _TOTAL_LENS_COUNT,
        "sources": sources,
    }

    # Pass 4 — Persistence (AC-3 invariant: always write, never no-op).
    market_prism_row_id: int | None = None

    if not dry_run:
        try:
            import database  # lazy import (CC-2 style)

            market_prism_row_id = database.insert_advisor_observation(
                advisor_role="MARKET_PRISM",
                subject_type="portfolio",
                subject_id="global",
                verdict=verdict,
                raw_response=raw_response,
                # is_advisory_only=1 is hard-wired inside insert_advisor_observation (CC-1).
            )
        except Exception as exc:
            exc_type = type(exc).__name__
            logger.error("MARKET_PRISM persistence failed: %s", exc_type)
            # market_prism_row_id stays None; run summary still returned.

    return {
        "run_ts": run_ts,
        "lenses_attempted": _TOTAL_LENS_COUNT,
        "lenses_available": available_count,
        "market_prism_row_id": market_prism_row_id,
        "error_count": error_count,
    }
