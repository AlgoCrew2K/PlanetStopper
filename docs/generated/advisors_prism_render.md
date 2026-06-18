# advisors/prism_render

> Render-layer prose guard for the Market Prism per-lens digest and the advisor observations table: converts structured JSON summaries and raw_response payloads to human-readable text so the AI Advisor Overview never shows raw JSON to the operator.

**Source:** `advisors/prism_render.py`
**Last updated:** 2026-06-18

## Overview

`advisors/prism_render.py` is a pure render-prep helper with no I/O and no Flask dependency. It exposes two public functions used by `app.py:ai_advisor_tab()` at route render time:

1. **`humanize_lens_summary`** — converts one `per_lens_digest` lens entry's `"summary"` value to readable text (R1: Market Prism per-lens digest).
2. **`humanize_obs_preview`** — converts an `advisor_observations.raw_response` value to a concise preview string (R2: symphony-level obs-raw-preview table cell).

Neither function touches the nightly data-production path. `advisors/lens_pipeline.py`, the Prism council, and all lens producers remain unmodified.

**Empty-state sentinel:** `_EMPTY_STATE = "limited inputs — data unavailable"`. All degenerate-input paths return this constant rather than an empty string, `"null"`, `"{}"`, or `"None"`.

**Never raises.** Both functions are wrapped in a top-level `except Exception` that returns `_EMPTY_STATE` on any unexpected error (D-1 contract).

## API Reference

### `humanize_lens_summary(lens_name: str, lens_entry: dict | None) -> str`

Converts one per-lens digest entry to a human-readable string safe for direct template rendering.

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `lens_name` | `str` | One of `"technicals"`, `"sentiment"`, `"derivatives"`, `"macro"`, `"fundamentals"`. Unknown lens names degrade to `"lens data available (<lens_name>)"`. |
| `lens_entry` | `dict \| None` | The full per-lens dict from `per_lens_digest[lens_name]`. May be `None`, non-dict, or a dict with an absent or degenerate `"summary"` key. |

**Returns:** `str` — a non-empty human-readable string. Guaranteed non-empty; `_EMPTY_STATE` is the floor. Never `None`. Never raises.

**Detection logic (internal `_is_structured`):**

1. If `lens_entry` is not a `dict` -> return `_EMPTY_STATE`.
2. Extract `summary = lens_entry.get("summary")`.
3. If `summary` is `None`, not a `str`, or `stripped` is in `_DEGENERATE_VALUES` (`{"null", "None", "{}", ""}`) -> return `_EMPTY_STATE`.
4. Attempt `json.loads(summary)`:
   - If result is a `dict` or `list` -> **structured path**: dispatch to per-lens humanizer.
   - Otherwise (bare scalar, JSON-parse error) -> **prose passthrough**: return `summary.strip()` unchanged.

A bare JSON scalar (e.g. `'"16.41"'` -> `'16.41'` after `json.loads`) is NOT a dict/list and is treated as prose. Prose containing braces (e.g. `"BULLISH {high conviction}"`) fails `json.loads` and is also treated as prose.

**Per-lens humanization (structured path only):**

| Lens | Internal helper | Key extraction | Example output |
|------|----------------|---------------|----------------|
| `technicals` | `_humanize_technicals` | `breadth` (float -> int %) + `momentum` dict (count pos/neg tickers) | `"Breadth 70% above 50d SMA; 7 tickers positive momentum, 3 negative"` |
| `sentiment` | `_humanize_sentiment` | `article_count` + `tone_summary` (null -> `"tone unavailable"`) | `"10 articles; tone unavailable"` |
| `derivatives` | `_humanize_derivatives` | `vix_level` (`.2f`) + `vix_term_structure.regime` + `risk_read` | `"VIX 16.41, contango, risk read: neutral"` |
| `macro` | `_humanize_macro` | `series` dict: `label: value` per series entry | `"10-Year Treasury Constant Maturity Rate: 4.43; Unemployment Rate: 4.3; ..."` |
| `fundamentals` | `_humanize_fundamentals` | `tickers` dict: coverage count + up to 5 ticker names (AC-4 concision) | `"Coverage: 2 tickers (AAPL, AMZN). Fundamentals data available."` |

The `_LENS_HUMANIZERS` dispatch table maps lens name -> helper function. Unknown lens names bypass the table and return `"lens data available (<lens_name>)"`.

---

### `humanize_obs_preview(raw_response: object) -> str`

Produces a concise, human-readable preview string for an `advisor_observations` row. Used by the `obs-raw-preview` table cell for **non-MARKET_PRISM** rows; MARKET_PRISM rows display `obs.verdict` directly in the template (not via this function).

**Parameters:**

| Name | Type | Description |
|------|------|-------------|
| `raw_response` | `object` | The `raw_response` value from an `advisor_observations` row. `database.py` deserializes this to a `dict` before returning rows, but the function also handles `str` (legacy JSON string) and `None` gracefully. |

**Returns:** `str` — a non-empty string. Never raw JSON object syntax. Never raises.

**Logic:**

1. If `raw_response` is a `str`: attempt `json.loads`; on failure treat the stripped string as prose (return it directly, or `_EMPTY_STATE` if empty after strip).
2. If the result is not a `dict` -> return `_EMPTY_STATE`.
3. Check `rr.get("note")` — if present and a non-empty string, return `note.strip()` (already human-readable prose).
4. Otherwise -> return `_EMPTY_STATE` rather than dumping raw JSON.

---

## Module-Level Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_EMPTY_STATE` | `"limited inputs — data unavailable"` | Returned by all degenerate-input and error paths; the non-empty floor |
| `_DEGENERATE_VALUES` | `frozenset({"null", "None", "{}", ""})` | Stripped `summary` strings treated as "no data" |
| `_LENS_HUMANIZERS` | `dict[str, Callable]` | Dispatch table: lens name -> per-lens humanizer function |

---

## Design Invariants

| Code | Invariant |
|------|-----------|
| D-1 | Both public functions wrapped in `except Exception` -> `_EMPTY_STATE`; never raise, never return `None` or empty string. |
| XSS | Both functions return plain `str`. Template renders all output with Jinja2 autoescaping (`{{ ... \| e }}`). `\| safe` is never applied to humanized output. No HTML produced. |
| No I/O | No DB calls, no network calls, no file reads. Imports only `json` (stdlib). Safe to import on any path including the execution path. |
| Producer-agnostic | Detection uses `json.loads` result shape (dict/list -> structured), not a producer identifier or `startswith("{")` check. |
| No production-path change | `advisors/lens_pipeline.py` and the Prism council are not modified. Both functions are called only at route render time in `app.py:ai_advisor_tab()`. |

---

## Internal Dependencies

None. Imports only stdlib `json`.

---

## Callers

### `app.py:ai_advisor_tab()` — R1: per-lens digest humanization (app.py:2966-2984)

After fetching `market_prism_summary`, `ai_advisor_tab()` pre-humanizes the `per_lens_digest` summaries **in-place** on the `market_prism_summary` dict before passing it to `render_template`. The template's existing `_lens.get('summary')` reference (line 999) then reads humanized prose rather than raw JSON:

```python
if market_prism_summary:
    try:
        from advisors.prism_render import humanize_lens_summary as _humanize_lens
        _raw_resp = market_prism_summary.get("raw_response", {})
        if isinstance(_raw_resp, str):
            import json as _json
            _raw_resp = _json.loads(_raw_resp)
        _per_lens = _raw_resp.get("per_lens_digest", {}) if isinstance(_raw_resp, dict) else {}
        for _ln, _le in _per_lens.items():
            if isinstance(_le, dict):
                _le["summary"] = _humanize_lens(_ln, _le)
    except Exception:
        pass  # Never crash the route
```

The template (`templates/ai_advisor.html:999`) is unchanged — `{{ _lens.get('summary') | e }}` reads the pre-humanized value. No new template context key is added.

### `app.py:ai_advisor_tab()` — R2: obs-raw-preview humanization (app.py:2892-2902)

Before rendering, `ai_advisor_tab()` stamps `_preview_text` onto each non-MARKET_PRISM observation dict in `observations`:

```python
try:
    from advisors.prism_render import humanize_obs_preview as _humanize_obs
    for _obs in observations:
        if _obs.get("advisor_role") != "MARKET_PRISM":
            _obs["_preview_text"] = _humanize_obs(_obs.get("raw_response"))
except Exception:
    pass
```

The template (`templates/ai_advisor.html:2002-2007`) renders the `.obs-raw-preview` cell as:
- MARKET_PRISM rows: `{{ obs.verdict | e }}` (verdict label only; no raw_response dump)
- All other rows: `{{ obs.get('_preview_text', '') | e }}`
