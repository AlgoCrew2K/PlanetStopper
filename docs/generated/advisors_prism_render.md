# advisors/prism_render

> Render-layer prose guard for the Market Prism per-lens digest: detects and humanizes structured JSON summaries so the AI Advisor Overview never shows raw JSON to the operator.

**Source:** `advisors/prism_render.py`
**Last updated:** 2026-06-18

## Overview

`advisors/prism_render.py` is a pure render-prep helper with no I/O and no Flask dependency. It sits between the `advisor_observations` DB row and the Jinja2 template — it does not touch the nightly data-production path (`advisors/lens_pipeline.py` and the Prism council remain unmodified).

The Market Prism row's `per_lens_digest[lens]["summary"]` can arrive in two forms:

1. **Prose** — written by the council synthesizer (e.g. "BULLISH. Breadth 0.70 (7/10 above 50d SMA)…"). Passes through unchanged.
2. **Structured JSON string** — written by `lens_pipeline.py`'s per-lens `_build_<lens>_section()` helpers (e.g. `{"ma_posture": {...}, "breadth": 0.7, "momentum": {...}}`). Humanized to readable text.

Detection is `json.loads(summary)` — if the result is a `dict` or `list` the summary is structured and gets humanized; a bare scalar or a JSON-parse failure is treated as prose/text (never misclassified). This means prose containing a brace or a value like `$416B` is always a passthrough.

**Never raises.** Every code path degrades gracefully: missing keys, `None` values, non-dict entries, and malformed shapes all resolve to an honest empty-state string. The nightly producer path is not modified.

## API Reference

### `humanize_lens_summary(lens_name: str, lens_entry: dict | None) -> str`

Converts one per-lens digest entry to a human-readable string safe for direct template rendering.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `lens_name` | `str` | One of `"technicals"`, `"sentiment"`, `"derivatives"`, `"macro"`, `"fundamentals"`. Controls lens-aware key extraction. |
| `lens_entry` | `dict \| None` | The full lens dict from `per_lens_digest[lens_name]`. May be `None`, `{}`, or a dict with an absent `"summary"` key. |

**Returns:** `str` — a human-readable summary. Guaranteed non-empty; the empty-state string `"limited inputs — no lens data"` is the floor. Never raises.

**Detection logic:**

1. If `lens_entry` is `None` or missing → return empty-state string.
2. Extract `summary = lens_entry.get("summary")`.
3. If `summary` is `None`, `""`, `"null"`, or `"None"` → return honest empty-state (`"limited inputs — tone unavailable"` for sentiment; generic `"limited inputs — no lens data"` otherwise).
4. Attempt `json.loads(summary)`:
   - If result is a `dict` or `list` → **structured path**: extract readable text using lens-aware key extraction (see below).
   - Otherwise (bare scalar, or parse error) → **prose passthrough**: return `summary` unchanged.

**Lens-aware humanization (structured path only):**

| Lens | Key extraction | Example output |
|------|---------------|----------------|
| `technicals` | `breadth` fraction + `momentum` direction; falls back to generic key-value collapse | `"Breadth 0.70 (70% above SMA-50). Momentum: positive."` |
| `sentiment` | article count + `tone_summary` or `tone` scalar; degrades to `"tone unavailable"` when both absent | `"3 articles. Tone: slightly positive (1.2)."` |
| `derivatives` | `vix_level` + `term_structure_regime` + `risk_read`; degrades per missing key | `"VIX 16.4. Term structure: normal. Risk read: low."` |
| `macro` | key series values (DGS10, UNRATE, CPIAUCSL, FEDFUNDS); max 4 bullet items | `"DGS10: 4.3%. UNRATE: 3.8%."` |
| `fundamentals` | coverage count + a highlight ticker when available; avoids full nested dump | `"5 tickers with data. AAPL: Revenue $391B."` |

For any lens, when key extraction yields nothing useful, a generic key-value collapse iterates the top-level dict keys and joins them as `key: value` pairs (max 5 keys), stripping nested dicts/lists to avoid raw JSON leakage.

---

### `prepare_prism_block(raw_response: dict | None) -> dict`

Prepares the full Market Prism context dict for Jinja2 template rendering. Calls `humanize_lens_summary` for every lens and returns a dict the template can consume directly without any further JSON parsing.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `raw_response` | `dict \| None` | The deserialized `raw_response` field from the latest MARKET_PRISM `advisor_observations` row (as returned by `database.get_latest_market_prism_summary()`). `None` when no row exists. |

**Returns:** `dict` with the following keys:

| Key | Type | Description |
|-----|------|-------------|
| `available` | `bool` | `False` when `raw_response` is `None`. `True` otherwise (even with limited-inputs verdict). |
| `verdict` | `str` | `overall_sentiment` value or `"limited-inputs"` when absent. |
| `rationale` | `str` | `sentiment_rationale` string or `""`. |
| `run_ts` | `str` | ISO 8601 run timestamp or `""`. |
| `sources` | `list[dict]` | Validated citations list or `[]`. |
| `lens_texts` | `dict[str, str]` | Keyed by lens name; value is the humanized text from `humanize_lens_summary`. Always contains all 5 lens keys. |
| `lens_available` | `dict[str, bool]` | Per-lens availability boolean from `per_lens_digest[lens]["available"]`. |

**Never raises.**

---

## Design Invariants

| Code | Invariant |
|------|-----------|
| D-1 | Never raises on any input shape — missing keys, non-dict, malformed JSON, `None` all degrade to honest empty-state. |
| XSS | Humanized text is returned as plain `str`; the template renders it with Jinja2 autoescaping (`{{ ... \| e }}`). The helper never produces HTML. The `\| safe` filter is never applied to humanized output. |
| No I/O | No DB calls, no network calls, no file reads. Pure function. Safe to import on any path. |
| Producer-agnostic | Detection is `json.loads` result shape, not a producer identifier or a naive `startswith("{")` check. Prose containing braces is always a passthrough. |
| No production-path change | `advisors/lens_pipeline.py` and the Prism council are not modified. The helper is called only at render time (Jinja2 template context assembly in `app.py`). |

## Internal Dependencies

None — pure stdlib (`json`). No imports from `advisors/`, `ai_advisor`, or `database`.

## Callers

- `app.py` — `ai_advisor_tab()` calls `prepare_prism_block(market_prism_summary)` before passing context to `render_template("ai_advisor.html", ...)`. The template receives `prism` (the prepared dict) and accesses `prism.lens_texts[lens_name]` in the per-lens loop instead of `_lens.get('summary')` directly.
- `templates/ai_advisor.html` — per-lens text rendered from `prism.lens_texts[_lens_name]` in the `.prism-lenses` loop (line ~999). The `obs-raw-preview` cell is humanized or removed from the operator-facing view.
