# TDD Handoff — RF-1 Prose Render (RED → GREEN)

**From:** rf1-test-writer  
**To:** rf1-implementer  
**Branch:** feat/rf1-prose-render  
**Worktree:** C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/rf1-prose-render  
**RED SHA:** 9f67dba  
**Result:** 29 FAILED / 4 passed (4 are AC-6 regression guards, correct to pass now)

**Phase:** green  
**GREEN SHA:** (see git log — committed after 33/33 pass)

---

## Your mission: GREEN (minimum changes to pass the 29 RED tests)

You are the implementer. Read ONLY this handoff — not the feature plan or the test file.
Write the minimum production code to make the 29 failing tests pass without breaking the
4 currently-passing regression guards or the 23 pre-existing Prism tests.

Confirm RED first:

```
cd C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/rf1-prose-render
python -m pytest tests/ai_advisor/test_rf1_prose_render.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```

Expected: 29 FAILED / 4 passed on SHA 9f67dba.

---

## Files to create/modify

### 1. CREATE `advisors/prism_render.py` (new pure module)

Public surface:

```python
def humanize_lens_summary(lens_name: str, lens_entry: dict | None) -> str:
```

- **Pure function** — no I/O, no Flask imports, no database imports
- **Never raises** — wrap all logic in try/except; return a fallback on any error (D-1)
- **Returns a non-empty string always** — never None, never ""

#### Detection rule (HARD — test specifically checks this)

```python
import json

def _is_structured(summary) -> bool:
    """Return True if summary is a JSON-encoded dict or list."""
    if not isinstance(summary, str):
        return False
    try:
        parsed = json.loads(summary)
        return isinstance(parsed, (dict, list))
    except (json.JSONDecodeError, ValueError):
        return False
```

DO NOT use `summary.startswith('{')` — a test checks that prose containing a brace
(e.g. "BULLISH {high conviction}") is NOT misclassified as JSON.

Bare JSON scalar (`json.loads('"16.41"')` → string `'16.41'`) is NOT a dict/list —
treat as prose passthrough.

#### Empty/degenerate input handling

Return a readable honest-empty-state for any of these (never "null", "None", "{}", or ""):
- `lens_entry` is None or not a dict
- `summary` is None
- `summary` is "" (empty string)
- `summary` is "null" or "None" (degenerate string values)
- `summary` is "{}" (empty JSON object — parses as dict, but empty → no signals)

Example empty-state strings: `"limited inputs — data unavailable"`, `"no lens data"`.

#### Per-lens humanization (structured JSON path)

All values extracted via `.get()` with safe defaults. The JSON shapes are documented
in tests/fixtures/ai_advisor/rf1_lens_pipeline_row77.json.

**technicals** (parsed dict has keys: `breadth`, `ma_posture`, `momentum`):
- Surface `breadth` value as readable text (e.g. "Breadth 0.70")
- Do NOT emit raw JSON keys like `"ma_posture"`, `"above_sma50"` as quoted strings
- Output must contain no `{"` or `":` markers

**sentiment** (parsed dict has keys: `article_count`, `tone_summary`, `tone_score`):
- Surface `article_count` (e.g. "10 articles")
- `tone_summary=null` → acknowledge unavailability (e.g. "tone unavailable"), NOT "null"

**derivatives** (parsed dict has keys: `vix_level`, `vix_term_structure`, `risk_read`, `as_of_date`):
- Surface `vix_level` and/or `vix_term_structure.regime` (e.g. "VIX 16.41, contango")

**macro** (parsed dict has key `series` → dict of `series_id` → `{label, value, date}`):
- Surface at least one series label + value pair (e.g. "10-Year Treasury: 4.43")
- Do NOT emit raw JSON keys like `"DGS10"`, `"series"`, `"label"` as quoted strings

**fundamentals** (parsed dict has key `tickers` → dict of ticker → `{entity_name, key_facts}`):
- **Output MUST be < 500 characters** (concision test: raw fixture is 4633 chars)
- Surface coverage count (number of tickers) and/or ticker names
- Example: "Coverage: 2 tickers (AAPL, AMZN). Revenue highlights available."

#### Prose passthrough path

If `_is_structured(summary)` returns False → return `summary` as-is (stripped of leading/
trailing whitespace is fine, but no other transformation).

---

### 2. WIRE the humanizer into the rendering path

#### Option A — Route pre-humanization (recommended)

In `app.py`, find the ai-advisor route that calls `database.get_latest_market_prism_summary()`
and passes `market_prism_summary` to `render_template`. After fetching the summary, pre-process
the `per_lens_digest` entries by calling `humanize_lens_summary` on each lens summary.

Example (minimal wiring):
```python
from advisors.prism_render import humanize_lens_summary  # lazy import inside the route fn

# After fetching market_prism_summary:
if market_prism_summary:
    raw = market_prism_summary.get("raw_response", {})
    per_lens = raw.get("per_lens_digest", {})
    for lens_name, lens_entry in per_lens.items():
        if isinstance(lens_entry, dict):
            lens_entry["summary"] = humanize_lens_summary(lens_name, lens_entry)
```

#### Option B — Jinja2 filter

Register a custom Jinja2 filter on the Flask app and call it in the template.
Either option works; the tests only check the rendered output.

**Template constraint:** Do NOT add `| safe` to any lens summary rendering. Jinja2
autoescaping must stay on.

#### Null-summary path (AC-3)

The current template has `{% if _lens.get('summary') %}` which skips the `<p>` paragraph
when summary is None. After wiring the humanizer, null-summary entries will have an
honest-empty-state string (truthy), so the paragraph will render automatically.
No template change needed for this case IF you wire Option A (pre-humanize replaces None
with the honest-empty string before the template sees it).

---

### 3. FIX `obs-raw-preview` for MARKET_PRISM rows (AC-5)

In `templates/ai_advisor.html`, find line ~2002:
```html
<td class="obs-raw-preview">{{ obs.raw_response | tojson | e }}</td>
```

For MARKET_PRISM rows, this dumps the full JSON including `per_lens_digest` and `ma_posture`.
After the fix, MARKET_PRISM rows must NOT expose `per_lens_digest` or `ma_posture`.

Acceptable fix: conditionally show a short summary for MARKET_PRISM role:
```html
<td class="obs-raw-preview">
  {% if obs.advisor_role == 'MARKET_PRISM' %}
    {{ obs.verdict | e }}
  {% else %}
    {{ obs.raw_response | tojson | e }}
  {% endif %}
</td>
```

Or hide the field entirely for MARKET_PRISM rows. Either approach passes the test.

---

## Running the tests

```
cd C:/Users/paulm/Documents/Projects/POC/AlphaBotPM/.claude/worktrees/rf1-prose-render
python -m pytest tests/ai_advisor/test_rf1_prose_render.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```

Target: **33 passed / 0 failed**.

Also confirm pre-existing Prism tests still pass:
```
python -m pytest tests/ai_advisor/test_prism_chip_color_mapping.py tests/ai_advisor/test_cycle5_market_prism_surface.py -p no:xdist -o addopts= -m "not live and not slow and not perf" -q
```
Expected: 23 passed.

---

## Scope boundaries — DO NOT touch

- `advisors/lens_pipeline.py` — nightly data-production path, out of scope
- `alpha_bot_execution.py` — execution path, never touch
- `database.py` — no schema changes needed
- No new external imports in `advisors/prism_render.py` (stdlib only: `json`, `typing`)
- No `| safe` filters in templates
- **NEVER merge, NEVER git checkout main, NEVER git push**

---

## Status Log

- [2026-06-18] rf1-implementer: GREEN complete — 33/33 tests passing (29 newly green, 4 prior AC-6 guards still pass), 0 test bugs. Pre-existing Prism tests: 23/23 still pass. Typecheck N/A (pure Python stdlib). Lint: see below.

## Implementation Notes

### What was built

1. **`advisors/prism_render.py`** (new) — Pure, never-raising render-prep helper:
   - `_is_structured(summary)` detection via `json.loads()` — NOT a naive `startswith('{')` check. Passes the brace-in-prose test.
   - `humanize_lens_summary(lens_name, lens_entry) -> str` — main public entry point.
   - Per-lens humanizers: `_humanize_technicals`, `_humanize_sentiment`, `_humanize_derivatives`, `_humanize_macro`, `_humanize_fundamentals`.
   - `_DEGENERATE_VALUES` frozenset catches `"null"`, `"None"`, `"{}"`, `""`.
   - Fundamentals humanizer emits "Coverage: N tickers (A, B, ...)" — always < 500 chars (AC-4).
   - D-1: outer try/except returns `_EMPTY_STATE` on any unexpected error.

2. **`app.py`** route pre-humanization — after `database.get_latest_market_prism_summary()`, lazy-imports `humanize_lens_summary` and rewrites each `lens_entry["summary"]` in-place before `render_template`. The template's `{% if _lens.get('summary') %}` guard now sees truthy strings for null-summary lenses (AC-3 auto-solved).

3. **`templates/ai_advisor.html`** — `obs-raw-preview` cell now conditionally shows `obs.verdict` for MARKET_PRISM rows, skipping the full `raw_response | tojson | e` dump (AC-5).

### Key decisions

- **Route pre-humanization (Option A)** chosen over Jinja2 filter: keeps template logic minimal, aligns with existing pattern in app.py, and lets the test mock `get_latest_market_prism_summary` without needing Jinja filter registration.
- **AC-3 null-summary handled implicitly** by pre-humanization: route replaces `None` with `_EMPTY_STATE`, making the template `{% if %}` guard always true for available lenses.
- **No `| safe` added** — autoescaping preserved throughout.

## Test File Issues (for test-writer to fix)

None. All 29 RED tests passed on first GREEN run.

## Disputed Tests

None.

## After GREEN: commit and signal rf1-test-writer

COMPLETED — committed and signaled.
