"""
RED tests — feature-plans/mdd-window-truth.md AC-4 (rendered coverage
disclosure) + AC-6 ("ytd" caption render bug). DE-PERF-WINDOW-TRUTH-001.

Matches this repo's established JS-render-truth pattern (source-inspection
regex on the raw file -- see tests/ui/test_performance_volatility_delta_polarity.py
and its own docstring precedent, tests/ui/test_cycle_3_performance.py::
test_performance_js_headline_stats_color_coded) rather than executing JS.

THE AC-6 BUG (static/performance.js:444-458, `renderObsCount`):
    if (typeof win === 'number') txt += ' · ' + win + 'd window';
`window_days` is the STRING "ytd" for the YTD button (app.py:4786/4907) --
`typeof 'ytd' === 'number'` is false, so the window context is SILENTLY
DROPPED for every YTD click, at any data depth (E-8 in the audit).

THE AC-4 REQUIREMENT: "the Performance tab must state its ACTUAL coverage to
the operator on screen... A response field with no render consumer does NOT
satisfy this AC (see DE-AUDIT-BL4-001)." So this file additionally asserts
`renderObsCount` (or its replacement) actually CONSUMES the AC-5 payload
fields (actual_days/coverage_days/date_range) into the caption text -- not
just that the route emits them (that's tests/app/test_mdd_window_truth_routes.py's
job; this file is the render-consumer half of the AC-4 requirement).
"""

from __future__ import annotations

import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PERF_JS = _PROJECT_ROOT / "static" / "performance.js"

_WINDOW_TOKENS_NUMERIC = ["30", "60", "90", "125", "252", "1260"]
_WINDOW_TOKEN_YTD = "ytd"


def _js_source() -> str:
    return _PERF_JS.read_text(encoding="utf-8")


def _render_obs_count_body(content: str) -> str:
    """Extract the function that populates #obs-caption. Named renderObsCount
    at RED-phase HEAD; if the implementer renames it, this locates it by its
    render target instead (the getElementById('obs-caption') call) so the
    test survives a rename."""
    match = re.search(
        r"function\s+renderObsCount\s*\([^)]*\)\s*\{(.+?)(?=\n\s*function\s|\Z)",
        content,
        re.DOTALL,
    )
    if match is not None:
        return match.group(1)

    # Fallback: locate whichever function body contains the obs-caption target.
    anchor = content.find("obs-caption")
    assert anchor != -1, "no function references 'obs-caption' anywhere in performance.js"
    fn_start = content.rfind("function ", 0, anchor)
    assert fn_start != -1, "could not locate the enclosing function for the obs-caption target"
    brace_start = content.find("{", fn_start)
    depth = 0
    i = brace_start
    while i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return content[brace_start + 1 : i]


# ===========================================================================
# AC-6 — the "ytd" caption render bug
# ===========================================================================


class TestAC6YtdCaptionRegressionGuard:
    def test_window_label_logic_is_not_gated_solely_on_typeof_number(self):
        """The exact AC-6 defect: `if (typeof win === 'number')` as the SOLE
        gate on appending window context silently drops every string token
        (ytd -- and would drop any other future string token too). Post-fix,
        either the number-only gate must be replaced by a broader check
        (existence/non-null, not type-restricted), or a SIBLING branch must
        exist that handles the string case."""
        fn_body = _render_obs_count_body(_js_source())

        number_gate = re.search(r"typeof\s+win\s*===\s*['\"]number['\"]", fn_body)
        if number_gate is None:
            # The number-only type-check was removed entirely -- acceptable IF
            # something still gates the append on win being present at all
            # (never unconditionally appending window text for a genuinely
            # absent/undefined win).
            assert re.search(
                r"win\s*!=\s*null|win\s*!==\s*undefined|if\s*\(\s*win\s*\)", fn_body
            ), (
                "the typeof-number gate was removed but no replacement "
                "existence/non-null check was found -- window context must "
                "still be conditionally appended (never unconditional), just "
                "not restricted to the 'number' type. "
                f"Function body:\n{fn_body}"
            )
        else:
            # The number-only gate still exists -- there MUST be a sibling
            # branch (else / else if) handling the non-number (string) case,
            # or a downstream generic append that already covers strings.
            tail = fn_body[number_gate.end() : number_gate.end() + 400]
            has_sibling_branch = bool(re.search(r"else", tail)) or bool(
                re.search(r"typeof\s+win\s*===\s*['\"]string['\"]", fn_body)
            )
            assert has_sibling_branch, (
                "AC-6 FAIL: `typeof win === 'number'` is still the SOLE gate on "
                "appending window context -- a string window token (e.g. "
                "'ytd', the exact reported bug) silently drops all window "
                f"context. No sibling else/else-if branch found. Function "
                f"body:\n{fn_body}"
            )

    def test_ytd_token_produces_non_dropped_window_context(self):
        """Stronger positive check: the function body must contain SOME
        codepath that stringifies an arbitrary non-number win value (not just
        a hardcoded 'ytd' special-case, which would silently regress for the
        NEXT string token added) -- evidenced by a generic string-typeof
        branch, a String(win) coercion, or an unconditional (non type-gated)
        append."""
        fn_body = _render_obs_count_body(_js_source())
        generic_string_handling = bool(
            re.search(r"typeof\s+win\s*===\s*['\"]string['\"]", fn_body)
            or re.search(r"String\s*\(\s*win\s*\)", fn_body)
            or re.search(r"win\s*\?\s*", fn_body)  # ternary truthy-check on win
        )
        assert generic_string_handling, (
            "AC-6 FAIL: no generic (non type-restricted) handling of a string "
            "`win` value found -- a hardcoded hardcoded-for-'ytd'-only fix "
            "would regress for the next string window token. Expected one of: "
            "`typeof win === 'string'`, `String(win)`, or a truthy ternary on "
            f"`win`. Function body:\n{fn_body}"
        )


# ===========================================================================
# AC-4 — renderObsCount must consume the AC-5 coverage payload, not just the
# route emitting it (see tests/app/test_mdd_window_truth_routes.py for the
# route-emission half)
# ===========================================================================


class TestAC4RenderConsumesCoverageDisclosureFields:
    def test_function_reads_actual_days_or_coverage_days_from_payload(self):
        fn_body = _render_obs_count_body(_js_source())
        reads_coverage_field = bool(
            re.search(r"payload\.actual_days", fn_body)
            or re.search(r"payload\.coverage_days", fn_body)
            or re.search(r"payload\[['\"]actual_days['\"]\]", fn_body)
            or re.search(r"payload\[['\"]coverage_days['\"]\]", fn_body)
        )
        assert reads_coverage_field, (
            "AC-4 FAIL: the render function that populates #obs-caption does "
            "not read payload.actual_days or payload.coverage_days -- a "
            "response field with no render consumer does NOT satisfy AC-4 "
            "(see DE-AUDIT-BL4-001, where honesty markers were computed "
            f"correctly and rendered nowhere for months). Function body:\n{fn_body}"
        )

    def test_function_constructs_a_shortfall_disclosure_not_just_a_bare_count(self):
        """AC-4's target copy example: '49 observations · 60d window
        requested · only 49 trading days available (history begins ...)' --
        never the bare, potentially-false '· 60d window'. This asserts the
        function's logic has a CONDITIONAL branch for the actual < requested
        case (distinct render output when coverage falls short), not merely
        a caption that always looks the same regardless of shortfall."""
        fn_body = _render_obs_count_body(_js_source())
        has_shortfall_branch = bool(
            re.search(r"actual_days\s*<", fn_body)
            or re.search(r"coverage_days\s*<", fn_body)
            or re.search(r"<\s*win\b", fn_body)
        )
        assert has_shortfall_branch, (
            "AC-4 FAIL: no conditional comparison between the actual/coverage "
            "day count and the requested window was found -- the caption must "
            "render DIFFERENTLY when coverage falls short of the request (e.g. "
            "'only N trading days available'), not just always show the same "
            f"shape of text regardless of shortfall. Function body:\n{fn_body}"
        )
