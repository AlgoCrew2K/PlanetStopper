"""
RED tests — dashboard $-saved panel sign coherence (static/index.js + templates/index.html).

THE BUG (corrected from the original kickoff brief -- this surface is NOT
already-compliant): fetchGuardAlphaSummary's snapshot headline
(static/index.js:1432-1435) and its realized-basis sibling (:1454-1457) both
do `(value < 0 ? '-$' : '$') + Math.abs(value).toFixed(2)` -- a NAKED MINUS --
paired with a STATIC caption that always reads "saved"/"realized"
(templates/index.html:1060, :1072) regardless of sign. A losing window
renders "-$50.00 saved across 3 exits" -- exactly the "naked minus under a
saved label" pattern the operator ruling forbids.

THE FIX (operator-locked convention): render the ABS magnitude with NO sign
character; a dedicated verb element (new ids introduced by this fix,
`dollar-saved-verb` / `dollar-saved-realized-verb`) carries "saved"/"lost"
driven by sign -- mirrors analytics.format_dollar_saved's contract on the
Python side, reimplemented locally in JS (no shared module between
index.js/history.js exists in this codebase).

AC-2's windowing wiring is ALSO pinned here: the existing hero-strip window
picker (data-testid="window-30d" etc, index.html:838-844) already tracks
`_heroWindow` (index.js:7) and re-fetches /api/hero-chart + /api/strip on
click (index.js:1503-1512) -- fetchGuardAlphaSummary must join that same
click handler and read the SAME active window token, so the $-saved panel
re-windows in lockstep with the strip/chart instead of always showing
all-time.

Follows the established source-window regex-extraction pattern from
tests/app/test_dollar_saved_display_contract.py (`_js_block`) -- this
codebase asserts JS behavior via targeted source-text assertions, not a
jsdom/execution harness, EXCEPT for one deliberate exception at the bottom of
this file (TestApplyHeroWindowHandlesTokenStrings) -- see that class's
docstring for why a real `node -e` execution harness was justified there
instead of a regex assertion.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess

import pytest

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent
_INDEX_HTML = _PROJECT_ROOT / "templates" / "index.html"
_INDEX_JS = _PROJECT_ROOT / "static" / "index.js"


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return _INDEX_JS.read_text(encoding="utf-8")


def _js_block(source: str, anchor: str, span: int = 2500) -> str:
    """A source window following `anchor` -- wide enough to hold the function
    or listener body without needing a JS parser. Same helper shape as
    test_dollar_saved_display_contract.py's `_js_block`."""
    idx = source.find(anchor)
    assert idx != -1, f"static/index.js no longer contains {anchor!r}"
    return source[idx : idx + span]


# ---------------------------------------------------------------------------
# Template: new verb-carrying ids exist (JS needs a hook to swap the word)
# ---------------------------------------------------------------------------


class TestTemplateVerbElementsExist:
    def test_snapshot_headline_has_a_verb_element(self):
        html = _html()
        assert re.search(r'id="dollar-saved-verb"', html), (
            "index.html must add a dedicated element (id=dollar-saved-verb) so "
            "fetchGuardAlphaSummary can swap the word 'saved'/'lost' independently "
            "of the static 'across'/'exits' text -- today the caption is one static "
            "span that never changes"
        )

    def test_realized_headline_has_a_verb_element(self):
        html = _html()
        assert re.search(r'id="dollar-saved-realized-verb"', html), (
            "index.html must add a dedicated element (id=dollar-saved-realized-verb) "
            "for the realized-basis headline's sign-driven word -- same requirement "
            "as the snapshot headline, currently missing entirely (today's caption "
            "is the static word 'realized', which carries no direction at all)"
        )


# ---------------------------------------------------------------------------
# JS: snapshot headline -- no naked minus, verb element is set by sign
# ---------------------------------------------------------------------------


class TestSnapshotHeadlineNoNakedMinus:
    def test_fetch_guard_alpha_summary_does_not_prefix_naked_minus(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary")
        assert "'-$'" not in block, (
            "fetchGuardAlphaSummary must not prepend a literal '-$' for a negative "
            "value -- the magnitude must be ABS with no sign character at all; "
            "direction is conveyed by color + the verb element alone"
        )

    def test_fetch_guard_alpha_summary_sets_the_verb_element_by_sign(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary")
        assert "dollar-saved-verb" in block, (
            "fetchGuardAlphaSummary must reference the new dollar-saved-verb element "
            "to swap the word by sign"
        )
        assert "'lost'" in block, (
            "fetchGuardAlphaSummary must reference the literal word 'lost' for the "
            "negative branch -- the antonym of the existing 'saved' wording"
        )

    def test_realized_headline_does_not_prefix_naked_minus(self):
        # A wider span than the other assertions in this file -- the realized
        # branch sits near the END of fetchGuardAlphaSummary's ~50-line body,
        # past the default 2500-char window.
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=5000)
        # Scope to the realized-basis branch specifically -- both branches live
        # in the same function, so re-derive the sub-block via its own anchor.
        realized_idx = block.find("realizedSaved")
        assert realized_idx != -1, "fetchGuardAlphaSummary must still handle realizedSaved"
        realized_block = block[realized_idx : realized_idx + 400]
        assert "'-$'" not in realized_block, (
            "the realized-basis headline must not prepend a literal '-$' either -- "
            "same ABS-magnitude-no-sign contract as the snapshot headline"
        )

    def test_realized_headline_sets_its_own_verb_element_by_sign(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary", span=5000)
        assert "dollar-saved-realized-verb" in block, (
            "fetchGuardAlphaSummary must reference dollar-saved-realized-verb to "
            "drive the realized headline's word by sign, independent of the "
            "snapshot headline's verb element"
        )


# ---------------------------------------------------------------------------
# JS: window-picker wiring -- $-saved panel re-fetches on the same click
# ---------------------------------------------------------------------------


class TestDollarSavedPanelJoinsWindowPicker:
    def test_hero_window_initial_value_is_a_token_string_not_a_bare_number(self):
        """_heroWindow is read by the window-picker click handler and (per this
        fix) by fetchGuardAlphaSummary -- it must be initialized to the SAME
        token shape ('30d', a string) the click handler assigns
        (`_heroWindow = token` where token is e.g. '30d'), matching the
        already-active 30d button in the markup (index.html's window-30d
        button carries class="active" by default). Today it is initialized as
        the bare number 30, a type mismatch with every post-click value.
        """
        js = _js()
        assert "var _heroWindow = '30d';" in js or 'var _heroWindow = "30d";' in js, (
            "_heroWindow must be initialized as the string token '30d' (matching the "
            "actively-highlighted 30d button), not the bare number 30 -- the "
            "window-picker click handler always assigns a string token, so the "
            "pre-click value should share that shape"
        )

    def test_fetch_guard_alpha_summary_accepts_a_window_token_argument(self):
        # The function signature itself must accept a parameter -- a bare
        # `function fetchGuardAlphaSummary()` with no params cannot receive
        # the active window token from the click handler.
        match = re.search(r"function fetchGuardAlphaSummary\(([^)]*)\)", _js())
        assert match is not None, "fetchGuardAlphaSummary must still be defined as a function"
        assert match.group(1).strip() != "", (
            "fetchGuardAlphaSummary must accept a window-token parameter -- it is "
            "currently a zero-arg function and cannot be windowed by the picker"
        )

    def test_fetch_guard_alpha_summary_includes_window_in_its_request_url(self):
        block = _js_block(_js(), "function fetchGuardAlphaSummary")
        assert "/api/guard-alpha-summary" in block
        assert "window" in block, (
            "fetchGuardAlphaSummary must include a `window=` query parameter in its "
            "fetch URL, sourced from its window-token argument -- today it always "
            "fetches the bare all-time endpoint"
        )

    def test_window_picker_click_handler_also_refetches_dollar_saved_summary(self):
        """The click handler that already re-fetches /api/hero-chart and the
        windowed strip (index.js ~1503-1512) must ALSO call
        fetchGuardAlphaSummary(token) so the $-saved panel re-windows in
        lockstep with the rest of the hero -- today it only updates the chart
        and the vs-rows."""
        source = _js()
        anchor = "var windowTokenMap = {"
        idx = source.find(anchor)
        assert idx != -1, "static/index.js no longer contains the window-picker token map"
        handler_block = source[idx : idx + 4000]
        assert "fetchGuardAlphaSummary(" in handler_block, (
            "the window-picker click handler must call fetchGuardAlphaSummary(token) "
            "alongside its existing /api/hero-chart + windowed-strip fetches -- "
            "otherwise the $-saved panel silently stays on whatever window it loaded "
            "with while every other hero metric re-windows live"
        )


# ---------------------------------------------------------------------------
# Sufficiency-review finding (Red/Green/Revise): applyHeroWindow cannot parse
# the token-shaped strings _heroWindow now holds.
# ---------------------------------------------------------------------------


def _extract_function_with_signature(source: str, func_name: str) -> str:
    """Extract `function name(...) { ... }` (signature + full body) via brace
    counting, for embedding verbatim in a standalone Node harness -- same
    technique tests/ui/test_cycle_5_history.py uses to extract renderHero's
    body, extended here to keep the signature (needed to actually CALL the
    function from the harness, not just inspect its text)."""
    match = re.search(rf"function\s+{func_name}\s*\([^)]*\)\s*\{{", source)
    assert match is not None, f"{func_name} not found in static/index.js"
    brace_start = source.index("{", match.start())
    depth = 0
    i = brace_start
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : i + 1]
        i += 1
    pytest.fail(f"Could not extract {func_name} -- unbalanced braces")


def _run_apply_hero_window(token: str, n_days: int) -> dict:
    """Executes the REAL applyHeroWindow source (extracted live from
    static/index.js, never hardcoded) against a synthetic `n_days`-entry
    history ending today, and returns the resulting labels-array length.

    Deliberate exception to this file's regex-only convention: a source-text
    assertion cannot distinguish "parses the token correctly" from "looks like
    it might" -- and this specific gap was found by empirically verifying (a
    throwaway `node -e` one-liner, outside this test) that
    `dates.slice(-'30d')` evaluates to `dates.slice(NaN)`, which
    Array.prototype.slice treats as `slice(0)` -- the FULL array, not a
    30-entry window. Given the severity (this fires on every dashboard page
    load once _heroWindow's default becomes the string '30d' -- see below),
    only a real execution proves the fix actually parses the token rather
    than merely referencing it somewhere in the function body.
    """
    if shutil.which("node") is None:
        pytest.skip("node not installed -- skipping applyHeroWindow execution check")

    fn_src = _extract_function_with_signature(_js(), "applyHeroWindow")

    harness = f"""
    var _today = new Date();
    var _heroFull = {{ dates: [], bot: [], held: [] }};
    for (var i = {n_days} - 1; i >= 0; i--) {{
        var d = new Date(_today.getFullYear(), _today.getMonth(), _today.getDate() - i);
        _heroFull.dates.push(d.toISOString().slice(0, 10));
        _heroFull.bot.push(i);
        _heroFull.held.push(i * 2);
    }}
    var _cumChart = {{
        data: {{ labels: [], datasets: [{{ data: [] }}, {{ data: [] }}] }},
        update: function () {{}}
    }};

    {fn_src}

    applyHeroWindow({json.dumps(token)});
    console.log(JSON.stringify({{ labels_len: _cumChart.data.labels.length }}));
    """

    result = subprocess.run(
        ["node", "-e", harness], capture_output=True, text=True, timeout=10
    )
    assert result.returncode == 0, (
        f"applyHeroWindow('{token}') harness crashed:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip())


class TestApplyHeroWindowHandlesTokenStrings:
    """SUFFICIENCY-REVIEW FINDING, not part of the original RED plan: gas-impl's
    GREEN implementation correctly satisfies
    test_hero_window_initial_value_is_a_token_string_not_a_bare_number (above)
    by changing `_heroWindow`'s initial value from the number 30 to the
    string '30d' -- but applyHeroWindow(days) (index.js ~49-67) was written
    for a BARE NUMBER (`dates.slice(-days)`) or the literal 'ytd', never a
    '<N>d'-shaped token. `dates.slice(-'30d')` evaluates to
    `dates.slice(NaN)`, and Array.prototype.slice treats a NaN start as 0 --
    i.e. the FULL lifetime history, not a 30-day window.

    Blast radius: applyHeroWindow(_heroWindow) fires (a) immediately after the
    very first chart render on every page load (index.js ~129) -- so the hero
    cumulative-return chart's default view is now the WHOLE history, not the
    intended 30-day default -- and (b) on every subsequent poll once
    _cumChart already exists (index.js ~83, called from every
    updateDashboard -> renderHeroChart, i.e. every ~1-30s per the SSE/poll
    cadence) for WHATEVER token is currently selected. This second path is a
    PRE-EXISTING latent bug (any token the user clicks -- '60d'/'90d'/etc --
    already breaks the same way one poll cycle after the click, since
    _heroWindow becomes that string token regardless of this cycle's fix) --
    this cycle's change is what newly exposes it on the FIRST render too, via
    the same code path. One fix (teaching applyHeroWindow to parse the token
    shapes) closes both.

    Verified empirically before writing this test (not merely asserted):
    `node -e "console.log([1,2,3].slice(-'30d').length)"` prints 3 (the
    whole array), not a slice.
    """

    def test_30d_token_slices_to_last_30_not_the_full_history(self):
        result = _run_apply_hero_window("30d", n_days=100)
        assert result["labels_len"] == 30, (
            f"applyHeroWindow('30d') must slice to the last 30 entries -- got "
            f"{result['labels_len']} entries instead (100 == the NaN-slice bug "
            f"silently returning the whole array). _heroWindow is initialized to "
            f"the string '30d' by this cycle's own fix, so this fires on EVERY "
            f"dashboard page load, not just after a window-picker click."
        )

    def test_60d_token_slices_to_last_60(self):
        result = _run_apply_hero_window("60d", n_days=100)
        assert result["labels_len"] == 60, (
            f"applyHeroWindow('60d') must slice to 60 entries, got {result['labels_len']}"
        )

    def test_125d_token_slices_to_last_125(self):
        result = _run_apply_hero_window("125d", n_days=200)
        assert result["labels_len"] == 125, (
            f"applyHeroWindow('125d') must slice to 125 entries, got {result['labels_len']}"
        )

    def test_1y_token_slices_to_365_not_full_history(self):
        """1y must resolve to 365 -- the SAME app-wide day-count this cycle's
        AC-2 fix already established server-side (analytics._WINDOW_TRAILING_DAYS
        ['1y']) -- so the hero CHART and the windowed $-saved/strip VALUES agree
        on what '1y' means, not just the two server-side routes agreeing with
        each other."""
        result = _run_apply_hero_window("1y", n_days=400)
        assert result["labels_len"] == 365, (
            f"applyHeroWindow('1y') must slice to 365 entries, got {result['labels_len']}"
        )

    def test_all_token_returns_the_full_history(self):
        """Regression guard: 'all' currently returns the full array only BY
        ACCIDENT (it also hits the NaN-slice path) -- a real fix must
        preserve this behavior deliberately, not lose it while adding
        numeric-token parsing for the other tokens."""
        result = _run_apply_hero_window("all", n_days=100)
        assert result["labels_len"] == 100, (
            f"applyHeroWindow('all') must return the full unsliced history "
            f"(100 entries), got {result['labels_len']}"
        )

    def test_ytd_token_still_filters_to_the_current_year(self):
        """Regression guard: 'ytd' already has dedicated, working handling --
        a fix for the numeric-token parsing above must not disturb it. With
        400 days of history ending today, a correct ytd slice must exclude
        at least last year's entries (i.e. strictly fewer than 400)."""
        result = _run_apply_hero_window("ytd", n_days=400)
        assert 0 < result["labels_len"] < 400, (
            f"applyHeroWindow('ytd') must still filter to entries on/after "
            f"Jan 1 of the current year -- got {result['labels_len']} out of 400 "
            f"(400 would mean the ytd filter silently stopped filtering)"
        )
