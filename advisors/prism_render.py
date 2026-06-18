"""
advisors/prism_render.py — Render-layer prose guard for Market Prism per-lens digests.

Pure, never-raising helper that converts lens_pipeline structured-JSON summaries
into human-readable text before the Overview tab renders them.  Council-produced
prose summaries pass through unchanged.

SCOPE: render-prep only.  No I/O, no Flask imports, no database imports.
RELIABILITY: D-1 contract — all paths are wrapped; never raises, never returns
None or empty string.
"""

from __future__ import annotations

import json

# Sentinel empty-state returned for null/missing/degenerate summaries.
_EMPTY_STATE = "limited inputs — data unavailable"

# Degenerate summary values that must be treated as "no data".
_DEGENERATE_VALUES: frozenset[str] = frozenset({"null", "None", "{}", ""})


def _is_structured(summary: object) -> bool:
    """Return True if summary is a JSON-encoded dict or list.

    Uses json.loads() — NOT a naive startswith('{') check — so prose that
    happens to contain a brace (e.g. 'BULLISH {high conviction}') is correctly
    treated as prose, not structured data.  A bare JSON scalar
    (json.loads('"16.41"') → '16.41') is NOT a dict/list and is treated as prose.
    """
    if not isinstance(summary, str):
        return False
    try:
        parsed = json.loads(summary)
        return isinstance(parsed, (dict, list))
    except (json.JSONDecodeError, ValueError):
        return False


def _humanize_technicals(parsed: dict) -> str:
    """Produce readable text from a technicals JSON dict.

    Extracts breadth and momentum signals; never emits raw JSON key names.
    """
    parts: list[str] = []

    breadth = parsed.get("breadth")
    if breadth is not None:
        try:
            pct = int(round(float(breadth) * 100))
            parts.append(f"Breadth {pct}% above 50d SMA")
        except (TypeError, ValueError):
            parts.append(f"Breadth {breadth}")

    # Momentum: count positive vs negative tickers
    momentum = parsed.get("momentum")
    if isinstance(momentum, dict) and momentum:
        n_pos = sum(1 for v in momentum.values() if isinstance(v, (int, float)) and v > 0)
        n_neg = len(momentum) - n_pos
        parts.append(f"{n_pos} tickers positive momentum, {n_neg} negative")

    return "; ".join(parts) if parts else _EMPTY_STATE


def _humanize_sentiment(parsed: dict) -> str:
    """Produce readable text from a sentiment JSON dict.

    Surfaces article count and acknowledges null tone.
    """
    parts: list[str] = []

    article_count = parsed.get("article_count")
    if article_count is not None:
        parts.append(f"{article_count} articles")

    tone_summary = parsed.get("tone_summary")
    if tone_summary is None:
        parts.append("tone unavailable")
    elif tone_summary:
        parts.append(str(tone_summary))

    return "; ".join(parts) if parts else _EMPTY_STATE


def _humanize_derivatives(parsed: dict) -> str:
    """Produce readable text from a derivatives JSON dict.

    Surfaces VIX level and term-structure regime.
    """
    parts: list[str] = []

    vix_level = parsed.get("vix_level")
    if vix_level is not None:
        try:
            parts.append(f"VIX {float(vix_level):.2f}")
        except (TypeError, ValueError):
            parts.append(f"VIX {vix_level}")

    vix_ts = parsed.get("vix_term_structure")
    if isinstance(vix_ts, dict):
        regime = vix_ts.get("regime")
        if regime:
            parts.append(str(regime))

    risk_read = parsed.get("risk_read")
    if risk_read:
        parts.append(f"risk read: {risk_read}")

    return ", ".join(parts) if parts else _EMPTY_STATE


def _humanize_macro(parsed: dict) -> str:
    """Produce readable text from a macro JSON dict.

    Surfaces series labels and values; never emits raw JSON key names as
    quoted strings (e.g. '"DGS10"').
    """
    series = parsed.get("series")
    if not isinstance(series, dict) or not series:
        return _EMPTY_STATE

    items: list[str] = []
    for _series_id, series_data in series.items():
        if not isinstance(series_data, dict):
            continue
        label = series_data.get("label") or _series_id
        value = series_data.get("value")
        if value is not None:
            items.append(f"{label}: {value}")

    return "; ".join(items) if items else _EMPTY_STATE


def _humanize_fundamentals(parsed: dict) -> str:
    """Produce concise text from a fundamentals JSON dict.

    Surfaces coverage count and ticker names only — no full 10-K dump.
    Output is guaranteed to be < 500 characters (AC-4).
    """
    tickers = parsed.get("tickers")
    if not isinstance(tickers, dict) or not tickers:
        return _EMPTY_STATE

    ticker_names = list(tickers.keys())
    count = len(ticker_names)

    # Concise: coverage count + up to 5 ticker names to stay well under 500 chars
    displayed = ticker_names[:5]
    ticker_str = ", ".join(displayed)
    if count > 5:
        ticker_str += f" +{count - 5} more"

    result = f"Coverage: {count} tickers ({ticker_str}). Fundamentals data available."
    return result


# Dispatch table: lens name → humanization function
_LENS_HUMANIZERS = {
    "technicals": _humanize_technicals,
    "sentiment": _humanize_sentiment,
    "derivatives": _humanize_derivatives,
    "macro": _humanize_macro,
    "fundamentals": _humanize_fundamentals,
}


def humanize_lens_summary(lens_name: str, lens_entry: dict | None) -> str:
    """Humanize a per-lens digest summary string for the Market Prism Overview block.

    Detects whether the summary is a JSON-encoded dict/list (lens_pipeline producer)
    or human-readable prose (council producer) and acts accordingly:

    - Prose (non-JSON / JSON scalar) → passthrough, returned as-is.
    - Structured JSON dict/list → humanized to readable text via a per-lens handler.
    - Null/empty/degenerate → honest empty-state string.

    Args:
        lens_name:  One of 'technicals', 'sentiment', 'derivatives', 'macro',
                    'fundamentals'.  Unknown lens names degrade gracefully.
        lens_entry: The per-lens dict (contains 'summary', 'available', etc.).
                    None or a non-dict triggers honest empty-state.

    Returns:
        A non-empty string, always.  Never None, never raises (D-1).
    """
    try:
        # --- Guard: degenerate entry ---
        if not isinstance(lens_entry, dict):
            return _EMPTY_STATE

        summary = lens_entry.get("summary")

        # --- Guard: null / None / empty / degenerate string ---
        if summary is None:
            return _EMPTY_STATE
        if not isinstance(summary, str):
            # Pre-parsed dict, int, etc. — not a string; degrade gracefully
            return _EMPTY_STATE
        stripped = summary.strip()
        if stripped in _DEGENERATE_VALUES:
            return _EMPTY_STATE

        # --- Detection: prose passthrough vs structured JSON ---
        if not _is_structured(summary):
            # Prose (includes bare scalars like '"16.41"' which parse as str, not dict/list)
            return stripped

        # --- Structured JSON path ---
        try:
            parsed = json.loads(summary)
        except (json.JSONDecodeError, ValueError):
            # Should not happen (we already checked _is_structured), but be safe
            return _EMPTY_STATE

        if not isinstance(parsed, dict):
            # A JSON list or other non-dict — degrade to honest empty-state
            return _EMPTY_STATE

        humanizer = _LENS_HUMANIZERS.get(lens_name)
        if humanizer is None:
            # Unknown lens — return a brief note, no raw JSON
            return f"lens data available ({lens_name})"

        result = humanizer(parsed)
        # Final safety: never return empty string
        return result if result and result.strip() else _EMPTY_STATE

    except Exception:  # noqa: BLE001
        # D-1: never raise; degrade to honest empty-state
        return _EMPTY_STATE


def humanize_obs_preview(raw_response: object) -> str:
    """Produce a concise, human-readable preview string for an advisor_observation row.

    Used by the obs-raw-preview table cell for non-MARKET_PRISM rows.  Prefers the
    `note` key (already prose) from raw_response; falls back to an honest empty-state.
    Never emits raw JSON object syntax.

    Args:
        raw_response: The raw_response value from an advisor_observations row.
                      database.py deserializes this to a dict before returning rows,
                      but this function also handles str (legacy) and None gracefully.

    Returns:
        A non-empty string, always.  Never raises (D-1).
    """
    try:
        # Normalize: str → dict (legacy rows stored as JSON string)
        rr = raw_response
        if isinstance(rr, str):
            try:
                rr = json.loads(rr)
            except (json.JSONDecodeError, ValueError):
                # Non-JSON string — treat as prose note
                stripped = rr.strip()
                return stripped if stripped else _EMPTY_STATE

        if not isinstance(rr, dict):
            return _EMPTY_STATE

        # Prefer the `note` field — it is already human-readable prose
        note = rr.get("note")
        if note and isinstance(note, str) and note.strip():
            return note.strip()

        # No note: return honest empty-state rather than dumping JSON
        return _EMPTY_STATE

    except Exception:  # noqa: BLE001
        return _EMPTY_STATE
