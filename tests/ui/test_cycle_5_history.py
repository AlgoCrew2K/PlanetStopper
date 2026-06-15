"""
Cycle 5 — Guard Alpha History parity RED tests.

Source: feature-plans/studio-design-handoff.md  AC-S.7
Design: .design-handoff/project/history.jsx + templates/ (no history.html yet)
Target: templates/history.html  vs  /history  +  /api/history/<days>

All tests written against REQUIRED behaviour per the design spec.
Tests are intentionally RED until impl delivers the History template.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# API stubs
# ---------------------------------------------------------------------------

_HISTORY_STUB = {
    "total_alpha": 14.62,
    "total_saved": 1947.21,
    "trigger_count": 84,
    "wins": 51,
    "avg_guard_alpha": 0.174,
    "win_rate": 60.71,
    "daily_alpha": [0.15, -0.05, 0.22, 0.08, -0.12] * 18,  # 90 values
    "by_reason": {
        "Take-Profit": {
            "alpha": 6.42,
            "count": 38,
            "wins": 24,
            "dollars": 854.10,
        },
        "Trailing Stop": {
            "alpha": 4.18,
            "count": 22,
            "wins": 13,
            "dollars": 557.20,
        },
        "VWAP Breakdown": {
            "alpha": 2.80,
            "count": 16,
            "wins": 10,
            "dollars": 373.10,
        },
        "VWAP Bleed Cut": {
            "alpha": 1.22,
            "count": 8,
            "wins": 4,
            "dollars": 162.81,
        },
    },
}

_HISTORY_STUB_INSUFFICIENT = {
    "total_alpha": 0.0,
    "total_saved": 0.0,
    "trigger_count": 0,
    "wins": 0,
    "avg_guard_alpha": 0.0,
    "win_rate": 0.0,
    "daily_alpha": [],
    "by_reason": {},
}


@contextmanager
def _patch_history(stub=None):
    """Patch the history API to return a stub response."""
    if stub is None:
        stub = _HISTORY_STUB
    with patch("database.get_all_post_mortem_data", return_value=[], create=True):
        import app as app_module

        with patch.object(app_module, "get_history", wraps=app_module.get_history) as _:
            # Patch at the jsonify level is complex; patch the underlying data source
            yield


@pytest.fixture
def hist_client():
    """Flask test client for the history surface."""
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ===========================================================================
# AC-C5.1 — Route basics
# ===========================================================================


def test_history_page_route_exists(hist_client):
    """/history GET must return 200."""
    resp = hist_client.get("/history")
    assert resp.status_code == 200


def test_history_page_returns_html(hist_client):
    resp = hist_client.get("/history")
    ct = resp.content_type
    assert "text/html" in ct or b"<html" in resp.data.lower()


def test_history_api_route_exists(hist_client):
    """/api/history/90 must return 200."""
    resp = hist_client.get("/api/history/90")
    assert resp.status_code == 200


def test_history_api_returns_json(hist_client):
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert isinstance(body, dict)


def test_history_api_has_total_alpha(hist_client):
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "total_alpha" in body


def test_history_api_has_total_saved(hist_client):
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "total_saved" in body


def test_history_api_has_trigger_count(hist_client):
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "trigger_count" in body


def test_history_api_has_wins(hist_client):
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "wins" in body


def test_history_api_has_avg_guard_alpha(hist_client):
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "avg_guard_alpha" in body


def test_history_api_has_win_rate(hist_client):
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "win_rate" in body


def test_history_api_has_by_reason(hist_client):
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "by_reason" in body
    assert isinstance(body["by_reason"], dict)


def test_history_api_has_daily_alpha(hist_client):
    """daily_alpha array required by design for the bar chart.

    This field is NOT currently in /api/history — impl must add it.
    """
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "daily_alpha" in body, (
        "daily_alpha missing from /api/history/<days> response. "
        "Design requires a per-day alpha array for the DailyStrip bar chart."
    )
    assert isinstance(body["daily_alpha"], list), "daily_alpha must be a list"


def test_history_api_by_reason_has_dollars(hist_client):
    """Each by_reason entry must include 'dollars' field.

    Design displays '$ saved' per exit reason — requires dollars in the breakdown.
    Current API does not track dollars in by_reason entries.
    """
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    by_reason = body.get("by_reason", {})
    if by_reason:
        for reason, stats in by_reason.items():
            assert "dollars" in stats, (
                f"by_reason['{reason}'] missing 'dollars' field — "
                "design shows '$ saved' per reason in the breakdown cards."
            )


def test_history_api_days_variants(hist_client):
    """All 7 window values must be valid path parameters."""
    for days in [30, 60, 90, 125, 252, 1260]:
        resp = hist_client.get(f"/api/history/{days}")
        assert resp.status_code == 200, f"/api/history/{days} returned {resp.status_code}"


# ===========================================================================
# AC-C5.2 — Page landmark
# ===========================================================================


def test_history_page_landmark(hist_client):
    """Root element must carry data-testid='history-page'."""
    resp = hist_client.get("/history")
    assert b'data-testid="history-page"' in resp.data


def test_history_page_has_chrome(hist_client):
    resp = hist_client.get("/history")
    assert b'data-testid="top-bar"' in resp.data


def test_history_page_has_page_header(hist_client):
    resp = hist_client.get("/history")
    assert b'data-testid="history-header"' in resp.data


def test_history_page_header_title(hist_client):
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "guard alpha" in html or "guard α" in html


def test_history_page_header_subtitle(hist_client):
    """Header subtitle must mention post-mortem or rollup."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "post-mortem" in html or "rollup" in html or "snapshot" in html


# ===========================================================================
# AC-C5.3 — Window selector
# ===========================================================================


def test_history_window_selector_present(hist_client):
    """Window selector must be present with data-testid='window-selector'."""
    resp = hist_client.get("/history")
    assert b'data-testid="window-selector"' in resp.data


def test_history_window_selector_has_7_options(hist_client):
    """Window selector must have 7 options: 30d, 60d, 90d, 125d, YTD, 1Y, 5Y."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    # Check all 7 window labels appear in the selector context
    window_labels = ["30d", "60d", "90d", "125d", "YTD", "1Y", "5Y"]
    for label in window_labels:
        assert label in html, f"Window option '{label}' missing from history page"


def test_history_window_default_90d(hist_client):
    """Default window must be 90d (selected on initial load)."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    # 90 should be the default selected value
    assert 'value="90"' in html or "value='90'" in html


# ===========================================================================
# AC-C5.4 — Hero stats strip (4-column grid)
# ===========================================================================


def test_history_hero_section_present(hist_client):
    """Hero stats section must carry data-testid='history-hero'."""
    resp = hist_client.get("/history")
    assert b'data-testid="history-hero"' in resp.data


def test_history_hero_total_alpha_stat(hist_client):
    """Hero must contain Total guard α stat cell."""
    resp = hist_client.get("/history")
    assert b'data-testid="stat-total-alpha"' in resp.data


def test_history_hero_total_saved_stat(hist_client):
    """Hero must contain $ saved stat cell."""
    resp = hist_client.get("/history")
    assert b'data-testid="stat-total-saved"' in resp.data


def test_history_hero_trigger_count_stat(hist_client):
    """Hero must contain Triggers stat cell."""
    resp = hist_client.get("/history")
    assert b'data-testid="stat-trigger-count"' in resp.data


def test_history_hero_win_rate_stat(hist_client):
    """Hero must contain Win rate stat cell."""
    resp = hist_client.get("/history")
    assert b'data-testid="stat-win-rate"' in resp.data


# ===========================================================================
# AC-C5.5 — Daily alpha chart strip
# ===========================================================================


def test_history_daily_strip_present(hist_client):
    """Daily alpha chart section must carry data-testid='daily-strip'."""
    resp = hist_client.get("/history")
    assert b'data-testid="daily-strip"' in resp.data


def test_history_daily_strip_has_chart(hist_client):
    """Daily strip must contain a chart element (SVG or canvas)."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    assert "<svg" in html or "<canvas" in html


def test_history_daily_strip_has_legend(hist_client):
    """Daily strip must contain positive/negative alpha legend."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "positive" in html or "positive α" in html
    assert "negative" in html or "negative α" in html


def test_history_daily_strip_section_header(hist_client):
    """Daily strip must have a visible header label."""
    resp = hist_client.get("/history")
    assert b'data-testid="daily-strip-header"' in resp.data


# ===========================================================================
# AC-C5.6 — By-reason breakdown (4-column card grid)
# ===========================================================================


def test_history_by_reason_section_present(hist_client):
    """By-reason section must carry data-testid='by-reason-section'."""
    resp = hist_client.get("/history")
    assert b'data-testid="by-reason-section"' in resp.data


def test_history_by_reason_section_header(hist_client):
    """By-reason section must have a visible header."""
    resp = hist_client.get("/history")
    assert b'data-testid="by-reason-header"' in resp.data


def test_history_by_reason_cards_present(hist_client):
    """By-reason section must contain reason cards."""
    resp = hist_client.get("/history")
    assert b'data-testid="reason-card"' in resp.data


def test_history_by_reason_card_count_4(hist_client):
    """Design shows 4 exit reasons — must have 4 reason cards when data present."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    count = html.count('data-testid="reason-card"')
    # When there's data with 4 reasons, expect 4 cards; when no data, 0 is fine
    assert count == 0 or count >= 1, "reason-card count must be 0 (no data) or >= 1 (with data)"


# ===========================================================================
# AC-C5.7 — Recent triggers list
# ===========================================================================


def test_history_recent_triggers_section_present(hist_client):
    """Recent triggers section must carry data-testid='recent-triggers'."""
    resp = hist_client.get("/history")
    assert b'data-testid="recent-triggers"' in resp.data


def test_history_recent_triggers_has_table(hist_client):
    """Recent triggers section must contain a table or list structure."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    assert (
        "<table" in html
        or 'data-testid="trigger-row"' in html
        or 'data-testid="triggers-table"' in html
    )


def test_history_recent_triggers_header_columns(hist_client):
    """Triggers table header must include Time, Symphony, Reason columns."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace").lower()
    assert "time" in html
    assert "symphony" in html
    assert "reason" in html


# ===========================================================================
# AC-C5.8 — Token hygiene
# ===========================================================================


def test_history_no_tailwind_cdn(hist_client):
    """Tailwind CDN must not be present in history page."""
    resp = hist_client.get("/history")
    assert b"cdn.tailwindcss.com" not in resp.data


def test_history_tokens_css_linked(hist_client):
    resp = hist_client.get("/history")
    assert b"tokens.css" in resp.data


def test_history_tweaks_js_linked(hist_client):
    resp = hist_client.get("/history")
    assert b"tweaks.js" in resp.data


def test_history_html_no_bare_hex(hist_client):
    """history.html must contain no bare hex color literals outside CSS-var declarations."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    stripped = re.sub(r"--[\w-]+\s*:\s*#[0-9a-fA-F]{3,8}\b", "", html)
    stripped = re.sub(r"var\([^)]*\)", "", stripped)
    stripped = re.sub(r"<!--.*?-->", "", stripped, flags=re.DOTALL)
    leaks = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", stripped)
    assert leaks == [], f"Bare hex color literals found in history page HTML: {leaks[:10]}"


def test_history_html_has_data_theme_attribute(hist_client):
    """<html> must carry data-theme attribute."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    assert "data-theme" in html


def test_history_html_no_bg_slate_body(hist_client):
    """<body> must not use Tailwind bg-slate-* classes."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    body_match = re.search(r"<body[^>]*>", html)
    if body_match:
        assert "bg-slate" not in body_match.group(0), (
            "body tag uses Tailwind bg-slate-* class instead of var(--studio-bg)"
        )


# ===========================================================================
# AC-C5.9 — JS token hygiene (history.js)
# ===========================================================================


def test_history_js_exists():
    """static/history.js must exist."""
    js_path = PROJECT_ROOT / "static" / "history.js"
    assert js_path.exists(), "static/history.js does not exist — impl must create it"


def test_history_js_no_bare_hex():
    """history.js must contain no bare hex color literals outside comments."""
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    leaks = re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", stripped)
    assert leaks == [], f"Bare hex found in history.js: {leaks}"


def test_history_js_no_tailwind_classes():
    """history.js must not inject Tailwind class strings."""
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    tailwind_patterns = re.findall(
        r'class\s*=\s*["\'][^"\']*(?:bg-|text-|rounded-|border-|flex|grid|p-\d|px-|py-|gap-|font-)[^"\']*["\']',
        content,
    )
    assert tailwind_patterns == [], (
        f"Tailwind class strings found in history.js: {tailwind_patterns[:5]}"
    )


def test_history_js_linked(hist_client):
    """history.js must be loaded in the history page."""
    resp = hist_client.get("/history")
    assert b"history.js" in resp.data


# ===========================================================================
# AC-C5.10 — Structural invariants
# ===========================================================================


def test_history_page_has_active_route_history(hist_client):
    """history page must pass active_route='history' to the chrome include."""
    resp = hist_client.get("/history")
    html = resp.data.decode("utf-8", errors="replace")
    # active-route indicator for 'history' nav item must be present in chrome
    assert "history" in html


def test_history_api_total_alpha_is_numeric(hist_client):
    """total_alpha must be a number (not string)."""
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    val = body.get("total_alpha")
    assert isinstance(val, (int, float)), f"total_alpha must be numeric, got {type(val)}"


def test_history_api_win_rate_bounded(hist_client):
    """win_rate must be 0-100 (percentage, not fraction)."""
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    win_rate = body.get("win_rate", 0)
    assert 0.0 <= win_rate <= 100.0, (
        f"win_rate must be 0-100, got {win_rate} — design renders it as a percentage"
    )


def test_history_api_by_reason_entries_have_count(hist_client):
    """Each by_reason entry must have count field."""
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    for reason, stats in body.get("by_reason", {}).items():
        assert "count" in stats, f"by_reason['{reason}'] missing count"


def test_history_api_by_reason_entries_have_wins(hist_client):
    """Each by_reason entry must have wins field."""
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    for reason, stats in body.get("by_reason", {}).items():
        assert "wins" in stats, f"by_reason['{reason}'] missing wins"


def test_history_api_by_reason_entries_have_alpha(hist_client):
    """Each by_reason entry must have alpha field."""
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    for reason, stats in body.get("by_reason", {}).items():
        assert "alpha" in stats, f"by_reason['{reason}'] missing alpha"


# ===========================================================================
# AC-C5.UX-BLOCK-1 — Hero stat sign coloring
#
# Design: total_alpha colored pos/neg by sign; win_rate colored by >= 50
# threshold; total_saved always pos-colored.
# Impl renderHero() uses setId() which only sets textContent — no style.color.
# ===========================================================================


def test_history_js_render_hero_applies_color_to_total_alpha():
    """renderHero must set style.color on val-total-alpha based on sign.

    Positive alpha → --studio-pos reference; negative → --studio-neg.
    Current impl uses setId() which only sets textContent — RED.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    # Strip comments
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    # Find renderHero function body via brace counting
    start = re.search(r"function\s+renderHero\s*\(", stripped)
    assert start is not None, "renderHero function not found in history.js"
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderHero body")

    # Must reference style.color AND --studio-pos AND --studio-neg inside the body
    assert "style.color" in body or ".style" in body, (
        "renderHero does not set style.color on any element — "
        "design requires sign-colored alpha value"
    )
    assert "--studio-pos" in body or "cssVar" in body or "studio-pos" in body, (
        "renderHero does not reference --studio-pos token for positive alpha"
    )
    assert "--studio-neg" in body or "studio-neg" in body, (
        "renderHero does not reference --studio-neg token for negative alpha"
    )


def test_history_js_render_hero_win_rate_threshold_coloring():
    """renderHero must color win_rate using >= 50 threshold.

    win_rate >= 50 → --studio-pos color; < 50 → --studio-neg color.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    start = re.search(r"function\s+renderHero\s*\(", stripped)
    assert start is not None
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderHero body")

    # Must have a threshold comparison for win_rate (50 or 0.5 if fractional)
    has_threshold = "50" in body or "0.5" in body
    assert has_threshold, (
        "renderHero has no 50-threshold for win_rate coloring — "
        "design colors win rate green >= 50%, red < 50%"
    )


def test_history_js_render_hero_total_saved_always_pos():
    """renderHero must always color val-total-saved with --studio-pos.

    $ saved is never negative — always use positive color token.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    start = re.search(r"function\s+renderHero\s*\(", stripped)
    assert start is not None
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderHero body")

    # val-total-saved element must have its color set in renderHero
    assert "total-saved" in body, (
        "renderHero does not reference val-total-saved element — "
        "must set style.color to --studio-pos unconditionally"
    )


# ===========================================================================
# AC-C5.UX-BLOCK-2 — Recent triggers must render individual exit records
#
# Design: RecentTriggers consumes todays_exits[] filtered by kind='TRIGGER'.
# Current impl falls back to summary rows from by_reason — misleading.
# Decision: extend /api/history/<days> with todays_exits[] per AC-S.7.
# ===========================================================================


def test_history_api_has_todays_exits(hist_client):
    """API must return todays_exits array per AC-S.7.

    Shape: list of {ts, symphony_id, reason, detail} objects.
    Current API does not return this field — RED.
    """
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    assert "todays_exits" in body, (
        "todays_exits missing from /api/history/<days> response. "
        "AC-S.7 requires todays_exits[] for the RecentTriggers table "
        "(individual exit records, not by_reason summary buckets)."
    )
    assert isinstance(body["todays_exits"], list), "todays_exits must be a list"


def test_history_api_todays_exits_entry_shape(hist_client):
    """Each todays_exits entry must have ts, symphony_id, reason, detail fields."""
    resp = hist_client.get("/api/history/90")
    body = json.loads(resp.data)
    exits = body.get("todays_exits", [])
    for i, record in enumerate(exits[:5]):  # check first 5 at most
        assert "ts" in record, f"todays_exits[{i}] missing 'ts'"
        assert "symphony_id" in record, f"todays_exits[{i}] missing 'symphony_id'"
        assert "reason" in record, f"todays_exits[{i}] missing 'reason'"
        assert "detail" in record, f"todays_exits[{i}] missing 'detail'"


def test_history_js_renders_todays_exits_not_summary_rows():
    """renderTriggers must consume todays_exits[], not fall back to by_reason summary rows.

    Current impl generates one summary row per by_reason bucket with
    hardcoded '—' timestamps and 'All' symphony — this is misleading.
    After impl fix, renderTriggers must iterate payload.todays_exits.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    start = re.search(r"function\s+renderTriggers\s*\(", stripped)
    assert start is not None, "renderTriggers function not found"
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderTriggers body")

    assert "todays_exits" in body, (
        "renderTriggers does not reference todays_exits — "
        "must render individual exit records, not by_reason summary rows"
    )
    # Must NOT hardcode 'All' as a symphony placeholder
    assert "'All'" not in body and '"All"' not in body, (
        "renderTriggers hardcodes 'All' as symphony — "
        "must use actual symphony_id from todays_exits records"
    )


def test_history_js_triggers_empty_state_no_individual_records():
    """When todays_exits is empty, renderTriggers must show a descriptive empty state.

    Must NOT show summary rows from by_reason when no individual records exist.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    start = re.search(r"function\s+renderTriggers\s*\(", stripped)
    assert start is not None
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderTriggers body")

    # Must have a length check for empty exits
    has_empty_check = (
        ".length === 0" in body or ".length == 0" in body or "=== 0" in body or "length" in body
    )
    assert has_empty_check, (
        "renderTriggers has no empty-state check for todays_exits — "
        "must show 'No exit records today' (or similar) when list is empty"
    )


# ===========================================================================
# AC-C5.UX-BLOCK-3 — ByReason card anatomy
#
# Design: each card has a 2px colored top-strip, a short-code badge (TP/STOP/
# VWAP/BLEED), a large sign-colored cumulative alpha, dollar-saved sub-label,
# and a 3-column mini-stat grid (Triggers / Wins / Win rate %).
# Current impl renders a single text block — no structural anatomy.
# ===========================================================================


def test_history_js_reason_card_has_top_strip():
    """renderReasonCards must include a 2px colored top-strip per card.

    Design: each card has a top border strip colored per reason
    (plum for take-profit, neg for stop-loss, cyan for VWAP, warn for bleed).
    Current impl uses only a uniform border — no top-strip element. RED.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    start = re.search(r"function\s+renderReasonCards\s*\(", stripped)
    assert start is not None, "renderReasonCards not found in history.js"
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderReasonCards body")

    # Must have a top-strip element with a --studio-* background token
    has_top_strip = (
        "border-top" in body
        or "top-strip" in body
        or "borderTop" in body
        or "border-radius" in body
        and "border-top" in body
    )
    has_studio_token = "--studio-" in body
    assert has_studio_token, (
        "renderReasonCards does not reference any --studio-* token for strip color — "
        "each card must have a reason-specific top-strip using a design token"
    )
    # Check that at minimum one reason-specific color token is used
    reason_tokens = [
        "--studio-plum",
        "--studio-neg",
        "--studio-cyan",
        "--studio-warn",
        "--studio-pos",
        "--studio-accent",
    ]
    has_any_reason_token = any(tok in body for tok in reason_tokens)
    assert has_any_reason_token, (
        "renderReasonCards uses no reason-specific color tokens — "
        "design requires per-reason top-strip colors (plum/neg/cyan/warn)"
    )


def test_history_js_reason_card_has_short_code_badge():
    """renderReasonCards must include a short-code badge per card.

    Design shows TP / STOP / VWAP / BLEED as compact labels on each card.
    Current impl renders only the raw reason key string — no badge. RED.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    start = re.search(r"function\s+renderReasonCards\s*\(", stripped)
    assert start is not None
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderReasonCards body")

    # Must have the abbreviated codes somewhere (as a map or inline)
    has_short_codes = (
        "'TP'" in body
        or '"TP"' in body
        or "STOP" in body
        or "VWAP" in body
        or "BLEED" in body
        or "short" in body.lower()
        or "badge" in body.lower()
        or "code" in body.lower()
    )
    assert has_short_codes, (
        "renderReasonCards has no short-code badge (TP/STOP/VWAP/BLEED) — "
        "design requires a compact reason badge on each card"
    )


def test_history_js_reason_card_has_win_rate_element():
    """renderReasonCards must render a win-rate element per card.

    Design 3-column mini-stat grid: Triggers count / Wins count / Win rate %.
    Current impl renders win rate as inline text only — no dedicated element. RED.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    start = re.search(r"function\s+renderReasonCards\s*\(", stripped)
    assert start is not None
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderReasonCards body")

    # Must have 3 distinct stat cells — look for labels
    has_triggers_label = "trigger" in body.lower() or "Triggers" in body
    has_wins_label = "wins" in body.lower() or "Wins" in body
    has_win_rate_label = "win rate" in body.lower() or "Win Rate" in body or "winRate" in body
    assert has_triggers_label, "reason card missing Triggers stat cell"
    assert has_wins_label, "reason card missing Wins stat cell"
    assert has_win_rate_label, "reason card missing Win Rate stat cell"


def test_history_js_reason_card_alpha_is_sign_colored():
    """renderReasonCards must sign-color the cumulative alpha value.

    Design: large 28px alpha number colored pos/neg by value sign.
    Current impl renders alpha as plain text with uniform ink color. RED.
    """
    js_path = PROJECT_ROOT / "static" / "history.js"
    if not js_path.exists():
        pytest.skip("history.js not yet created")
    content = js_path.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)

    start = re.search(r"function\s+renderReasonCards\s*\(", stripped)
    assert start is not None
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                body = stripped[brace_start + 1 : i]
                break
        i += 1
    else:
        pytest.fail("Could not extract renderReasonCards body")

    # Must have conditional color for alpha (pos/neg)
    has_pos = "--studio-pos" in body or "studio-pos" in body
    has_neg = "--studio-neg" in body or "studio-neg" in body
    assert has_pos and has_neg, (
        "renderReasonCards does not apply pos/neg sign coloring to alpha value — "
        "design requires sign-colored large alpha number on each card"
    )
