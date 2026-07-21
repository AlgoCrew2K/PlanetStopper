"""
RED tests — F-020: History per-day drill-down, client (static/history.js) side.

Source: docs/audit/confidence-program/running-register.md lines 126-131 (quoted
in full in tests/analytics/test_history_daily_drilldown.py, which pins the
backend daily_dates/daily_exits contract this file's client behavior consumes).

Contract (test-writer designed, backend-first): the backend now emits
`daily_dates` (parallel to `daily_alpha`) and `daily_exits` ({date: [exit,...]});
the client must (a) thread dates into the bar chart as a per-bar tooltip +
a stable `data-date` hook (clickability), and (b) expose SOME re-render path
(name-flexible — the impl owns naming, mirroring this project's
tests/advisors/test_builder_scheduler.py leniency pattern for the scheduler
entrypoint name) that shows a selected day's `daily_exits` in the existing
triggers table.

Test strategy follows this repo's established JS-testing convention (NO
jsdom/Playwright execution harness exists here) — source-text extraction +
brace-matching to inspect function bodies, exactly like
tests/ui/test_cycle_5_history.py's test_history_js_render_hero_* tests.
-n0 only.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
_JS_PATH = PROJECT_ROOT / "static" / "history.js"

# Candidate names for the day-drilldown re-render entry point — the impl
# owns the exact name; these tests pin BEHAVIOR (references daily_exits,
# updates the triggers surface), not one specific identifier.
_DRILLDOWN_FN_NAME_PATTERN = (
    r"function\s+(renderDayDrilldown|renderDayExits|selectHistoryDay|"
    r"historySelectDay|renderDailyExits|showDayExits)\s*\("
)


def _read_js() -> str:
    if not _JS_PATH.exists():
        pytest.skip("history.js not found")
    content = _JS_PATH.read_text(encoding="utf-8")
    stripped = re.sub(r"//[^\n]*", "", content)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    return stripped


def _extract_function_body(stripped: str, fn_name_pattern: str) -> str:
    """Brace-matching extraction, mirroring test_cycle_5_history.py's inline
    technique — factored into a shared helper since this file needs it
    repeatedly.
    """
    start = re.search(fn_name_pattern, stripped)
    assert start is not None, f"pattern {fn_name_pattern!r} not found in history.js"
    brace_start = stripped.index("{", start.end())
    depth = 0
    i = brace_start
    while i < len(stripped):
        if stripped[i] == "{":
            depth += 1
        elif stripped[i] == "}":
            depth -= 1
            if depth == 0:
                return stripped[brace_start + 1 : i]
        i += 1
    pytest.fail(f"could not find matching closing brace for {fn_name_pattern!r}")


# ---------------------------------------------------------------------------
# AC-3: renderDailyChart threads dates through as a tooltip + stable hook
# ---------------------------------------------------------------------------


def test_render_daily_chart_accepts_a_second_dates_parameter():
    stripped = _read_js()
    match = re.search(r"function\s+renderDailyChart\s*\(([^)]*)\)", stripped)
    assert match is not None, "renderDailyChart function not found in history.js"
    params = match.group(1)
    assert params.count(",") >= 1, (
        f"renderDailyChart must accept a second parameter (the parallel dates array) "
        f"to thread daily_dates through for tooltips/drill-down — got params={params!r}"
    )


def test_render_daily_chart_body_references_a_dates_parameter():
    stripped = _read_js()
    body = _extract_function_body(stripped, r"function\s+renderDailyChart\s*\(")
    # Loose name check: whatever the second param is called, its identifier
    # must appear inside the body (proving it's actually used, not just
    # accepted-and-ignored).
    match = re.search(r"function\s+renderDailyChart\s*\(([^)]*)\)", stripped)
    params = [p.strip() for p in match.group(1).split(",") if p.strip()]
    assert len(params) >= 2, "renderDailyChart must declare a second (dates) parameter"
    dates_param = params[1]
    assert dates_param in body, (
        f"renderDailyChart's second parameter {dates_param!r} must be referenced inside "
        f"the function body (used for tooltips/data-date hooks), not merely accepted"
    )


def test_render_daily_chart_bar_markup_carries_a_per_bar_date_hook():
    """A stable per-bar hook (data-date attribute or an SVG <title> tooltip)
    must exist so the date is surfaced/attachable to interaction — either
    satisfies the plan's 'date-paired tooltips / a selectable historical day'
    remediation line.
    """
    stripped = _read_js()
    body = _extract_function_body(stripped, r"function\s+renderDailyChart\s*\(")
    has_data_date_attr = "data-date" in body
    has_title_tooltip = "<title>" in body or "'title'" in body or '"title"' in body
    assert has_data_date_attr or has_title_tooltip, (
        "renderDailyChart must attach a per-bar date hook — either a 'data-date' "
        "attribute (drill-down clickability) or an SVG <title> tooltip (hover date) — "
        "found neither in the function body"
    )


def test_load_history_passes_daily_dates_into_render_daily_chart():
    stripped = _read_js()
    body = _extract_function_body(stripped, r"loadHistory\s*=\s*function\s+loadHistory\s*\(")
    assert "renderDailyChart(" in body, "loadHistory must still call renderDailyChart"
    call_match = re.search(r"renderDailyChart\(([^;]*)\)", body)
    assert call_match is not None
    call_args = call_match.group(1)
    assert "daily_dates" in call_args, (
        f"loadHistory must pass payload.daily_dates as renderDailyChart's second "
        f"argument — got call args: {call_args!r}"
    )


# ---------------------------------------------------------------------------
# AC-3: SOME drill-down re-render path exists and consumes daily_exits
# ---------------------------------------------------------------------------


def test_a_day_drilldown_render_function_exists():
    stripped = _read_js()
    match = re.search(_DRILLDOWN_FN_NAME_PATTERN, stripped)
    assert match is not None, (
        "no day-drilldown render function found in history.js (looked for one of: "
        "renderDayDrilldown, renderDayExits, selectHistoryDay, historySelectDay, "
        "renderDailyExits, showDayExits — impl may use a different name; if so this "
        "test's candidate list needs updating, not the requirement)"
    )


def test_day_drilldown_function_references_daily_exits():
    stripped = _read_js()
    fn_match = re.search(_DRILLDOWN_FN_NAME_PATTERN, stripped)
    assert fn_match is not None, "no day-drilldown render function found (see prior test)"
    fn_start_pattern = re.escape(fn_match.group(0))
    body = _extract_function_body(stripped, fn_start_pattern)
    assert "daily_exits" in body or "dailyExits" in body, (
        f"the day-drilldown function must reference daily_exits (the per-day payload "
        f"field) to actually show that day's exits — body did not reference it"
    )


def test_day_drilldown_function_touches_the_triggers_surface():
    """The drill-down must reuse/update the existing triggers table surface
    (triggers-tbody) rather than inventing an entirely parallel, untestable
    render target — matches the plan's 'showing that day's exits' remediation
    against the existing Recent-Triggers surface.
    """
    stripped = _read_js()
    fn_match = re.search(_DRILLDOWN_FN_NAME_PATTERN, stripped)
    assert fn_match is not None, "no day-drilldown render function found (see prior test)"
    fn_start_pattern = re.escape(fn_match.group(0))
    body = _extract_function_body(stripped, fn_start_pattern)
    assert "triggers-tbody" in body or "renderTriggers" in body or "tbody" in body, (
        f"the day-drilldown function must touch the existing triggers table surface "
        f"(triggers-tbody / renderTriggers) to display the selected day's exits"
    )
