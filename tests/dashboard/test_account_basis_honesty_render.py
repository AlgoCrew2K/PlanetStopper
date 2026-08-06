"""
RED -- BL-4 account-basis honesty render (feature-plans/audit-bl4-account-basis-honesty-render.md).

The backend already computes + serializes portfolio_strip["basis"] (Tier-2
value-weighted floor), portfolio_strip["account_basis_stale"] +
["account_basis_as_of"] (Tier-1 stale-last-good stamp) -- but static/index.js's
updateComparisonRows has ZERO consumers of these fields, so an operator sees a
stale/floor account figure with no visible indicator. This is purely a
FRONTEND render fix; AC-5 forbids any backend computation change.

AC-1: a chip renders on the Today + Cumulative comparison rows ONLY (never
Max DD, which is not account-basis-derived) when account_basis_stale is
truthy OR basis === "value_weighted".
AC-2: two distinct, never-conflated labels -- STALE (discloses
account_basis_as_of) vs VALUE-WEIGHTED FLOOR (no timestamp fabricated).
Priority rule (approved by team-lead in the RED-plan review): when both
flags are true simultaneously, STALE wins -- it is the more actionable/
informative disclosure and the only way to guarantee the two labels can
never render simultaneously on one chip.
AC-3: a NEW dedicated account-fetch freshness element (id="account-basis-as-of"),
distinct from #hero-data-as-of (whose engine-cycle contract is untouched).
AC-4: the "Bot" label gains a simulated/dry-run qualifier (title= tooltip) on
the two cited spans (comp-today-bot-text / comp-cumulative-bot-text) --
text/attribute only, the displayed value format is unchanged.
AC-5: zero backend computation change -- pinned as source-text presence of
the exact known assignment lines (a source-text pin, not a git-diff pin --
approved in the RED-plan review as more robust across worktree/CI checkout
differences and consistent with this codebase's established idiom).
AC-6 (optional/operator-gated): _ACCOUNT_TOTALS_HTTP_TIMEOUT_S bumped 10->30,
stays a named constant, call site still references it by name.

Pinned DOM contract (ids/testids introduced by THIS cycle, since the plan
itself leaves exact anchors to the implementer -- mirrors the BL-3 precedent
of pinning new DOM ids in the RED test file):
  - data-testid="comp-today-basis-chip" / data-testid="comp-cumulative-basis-chip"
    -- new <span hidden> elements in each row's .vs-row-top, no data-row
    disambiguator needed (matches the existing per-row-distinct-testid
    pattern the delta spans already use).
  - NO data-testid="comp-mdd-basis-chip" anywhere (negative pin).
  - id="account-basis-as-of" -- new freshness element, hidden by default,
    placed outside the hero-chart .chart-legend block (NOT #hero-data-as-of).
  - title= attribute (regex /simulat|dry-run/i) added to the existing
    comp-today-bot-text / comp-cumulative-bot-text spans (index.html:921/947,
    the exact two lines the plan cites).
  - Any new chip-render helper function, if factored out, must be defined
    between the existing setPosNeg and updateComparisonRows (or nested
    inside updateComparisonRows) -- keeps the Node-harness extraction
    deterministic without dictating exact code structure.

Follows the established source-window regex/substring-extraction pattern
from tests/app/test_dollar_saved_panel_sign_coherence.py's `_js_block`, and
the growth-safe brace-counted extraction technique from
tests/app/test_dollar_saved_panel_friction_disclosure.py's
`_extract_function_with_signature` (NOT a fixed-span slice -- BL-3's own
span-overflow lesson) for the real Node-execution non-vacuity harness below.
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
_APP_PY = _PROJECT_ROOT / "app.py"


def _html() -> str:
    return _INDEX_HTML.read_text(encoding="utf-8")


def _js() -> str:
    return _INDEX_JS.read_text(encoding="utf-8")


def _app_py() -> str:
    return _APP_PY.read_text(encoding="utf-8")


def _strip_block(html: str) -> str:
    """Scope assertions to the comparison-rows block (Today/Cumulative/Max DD)
    -- the vs-rows div through the annualized-vol-row landmark comment that
    immediately follows it in the template."""
    start = html.find('id="portfolio-strip"')
    assert start != -1, "index.html no longer contains #portfolio-strip"
    end = html.find("Phase 2b: annualized volatility row", start)
    assert end != -1, "could not find the annualized-vol-row landmark after portfolio-strip"
    return html[start:end]


def _comparison_rows_js_block(js: str) -> str:
    """The contiguous setPosNeg-through-updateComparisonRows block -- wide
    enough to catch a new chip-render helper the implementer places between
    the two (per the pinned contract above), not just updateComparisonRows'
    own body in isolation."""
    start = js.find("function setPosNeg")
    assert start != -1, "setPosNeg helper not found in static/index.js"
    ucr_idx = js.find("function updateComparisonRows", start)
    assert ucr_idx != -1, "updateComparisonRows function must exist"
    end_marker = js.find("\n    function ", ucr_idx + 1)
    return js[start:end_marker] if end_marker != -1 else js[start : start + 8000]


# ---------------------------------------------------------------------------
# AC-1 -- chip elements exist on Today/Cumulative, never on Max DD
# ---------------------------------------------------------------------------


class TestChipElementsExistOnTodayAndCumulativeOnly:
    def test_today_chip_element_exists_with_testid(self):
        block = _strip_block(_html())
        assert re.search(r'data-testid="comp-today-basis-chip"', block), (
            "AC-1: index.html must add a comp-today-basis-chip element to the Today comparison row"
        )

    def test_cumulative_chip_element_exists_with_testid(self):
        block = _strip_block(_html())
        assert re.search(r'data-testid="comp-cumulative-basis-chip"', block), (
            "AC-1: index.html must add a comp-cumulative-basis-chip element to "
            "the Cumulative comparison row"
        )

    def test_today_chip_hidden_by_default(self):
        block = _strip_block(_html())
        m = re.search(r'<[^>]*data-testid="comp-today-basis-chip"[^>]*>', block)
        assert m, "comp-today-basis-chip element not found"
        assert re.search(r"\bhidden\b", m.group(0)), (
            "AC-1: comp-today-basis-chip must be hidden by default (this "
            "template's existing hidden-toggle convention -- native `hidden` "
            "attribute, see fleet-correlation-banner/market-state-banner) so "
            "a healthy fetch shows zero visual change"
        )

    def test_cumulative_chip_hidden_by_default(self):
        block = _strip_block(_html())
        m = re.search(r'<[^>]*data-testid="comp-cumulative-basis-chip"[^>]*>', block)
        assert m, "comp-cumulative-basis-chip element not found"
        assert re.search(r"\bhidden\b", m.group(0)), (
            "AC-1: comp-cumulative-basis-chip must be hidden by default"
        )

    def test_mdd_row_never_gets_a_basis_chip_in_the_template(self):
        """AC-1: the Max DD row is NOT account-basis-derived and must never
        render this chip -- negative pin across the whole template, not just
        the strip block, in case the implementer misplaces it."""
        assert "comp-mdd-basis-chip" not in _html(), (
            "AC-1 FAIL: templates/index.html contains a comp-mdd-basis-chip "
            "element -- the Max DD row must never render the account-basis "
            "honesty chip; it is not account-basis-derived"
        )

    def test_mdd_row_never_gets_a_basis_chip_in_the_js(self):
        block = _comparison_rows_js_block(_js())
        assert "comp-mdd-basis-chip" not in block, (
            "AC-1 FAIL: the comparison-rows render logic references "
            "comp-mdd-basis-chip -- the Max DD row must never render this chip"
        )


# ---------------------------------------------------------------------------
# AC-3 -- dedicated account-fetch freshness indicator
# ---------------------------------------------------------------------------


class TestFreshnessIndicatorElement:
    def test_account_fetch_freshness_element_exists(self):
        assert 'id="account-basis-as-of"' in _html(), (
            "AC-3: index.html must add a dedicated id=account-basis-as-of "
            "freshness element, distinct from #hero-data-as-of"
        )

    def test_freshness_element_hidden_by_default(self):
        html = _html()
        m = re.search(r'<[^>]*id="account-basis-as-of"[^>]*>', html)
        assert m, "account-basis-as-of element not found"
        assert re.search(r"\bhidden\b", m.group(0)), (
            "AC-3: account-basis-as-of must be hidden by default -- shown "
            "only when the account basis is stale"
        )

    def test_hero_data_as_of_still_exists_untouched(self):
        """Regression pin: #hero-data-as-of's existing engine-cycle contract
        (id + surrounding legend) must remain -- this cycle is additive."""
        assert 'id="hero-data-as-of"' in _html(), (
            "the existing hero-data-as-of legend span was removed/renamed -- "
            "AC-3 requires it stay untouched, this is a NEW separate element"
        )

    def test_freshness_element_lives_outside_the_chart_legend(self):
        """AC-3's decision: a NEW element, not a repurposing of
        #hero-data-as-of or its .chart-legend container -- #hero-data-as-of's
        engine-cycle contract stays untouched."""
        html = _html()
        legend_start = html.find('data-testid="chart-legend"')
        assert legend_start != -1, "chart-legend block not found"
        legend_end = html.find("<!-- Right column: VsRows + mini-stats -->", legend_start)
        assert legend_end != -1, "could not find the end-of-chart-legend landmark"
        legend_block = html[legend_start:legend_end]
        assert 'id="account-basis-as-of"' not in legend_block, (
            "AC-3 FAIL: the new freshness element must NOT live inside the "
            "hero-chart's .chart-legend block -- that is #hero-data-as-of's "
            "existing, unrelated engine-cycle territory"
        )

    def test_js_never_writes_the_new_freshness_content_into_hero_data_as_of(self):
        block = _comparison_rows_js_block(_js())
        assert "hero-data-as-of" not in block, (
            "AC-3 FAIL: the comparison-rows render logic must never target "
            "hero-data-as-of -- that element's engine-cycle contract is a "
            "separate, untouched concern; the new freshness stamp must use "
            "its own dedicated element (account-basis-as-of)"
        )


# ---------------------------------------------------------------------------
# AC-4 -- "Bot" simulation qualifier
# ---------------------------------------------------------------------------


class TestBotSimulationQualifier:
    def test_today_bot_text_gains_title_qualifier(self):
        html = _html()
        m = re.search(r'<span data-testid="comp-today-bot-text"[^>]*>', html)
        assert m, "comp-today-bot-text span not found"
        assert re.search(r'title="[^"]*(simulat|dry-run)[^"]*"', m.group(0), re.IGNORECASE), (
            "AC-4: comp-today-bot-text must gain a title= tooltip disclosing "
            "it is a simulated/dry-run figure"
        )

    def test_cumulative_bot_text_gains_title_qualifier(self):
        html = _html()
        m = re.search(r'<span data-testid="comp-cumulative-bot-text"[^>]*>', html)
        assert m, "comp-cumulative-bot-text span not found"
        assert re.search(r'title="[^"]*(simulat|dry-run)[^"]*"', m.group(0), re.IGNORECASE), (
            "AC-4: comp-cumulative-bot-text must gain a title= tooltip "
            "disclosing it is a simulated/dry-run figure"
        )

    def test_today_bot_value_format_expression_unchanged(self):
        """AC-4 is text/attribute only -- the underlying value computation
        (dry_run, tc_bot) must stay byte-identical."""
        html = _html()
        assert re.search(
            r'Bot \{% if tc_bot >= 0 %\}\+\{% endif %\}\{\{ "%\.2f"\|format\(tc_bot\) \}\}%',
            html,
        ), (
            "AC-4 FAIL: comp-today-bot-text's value-format Jinja expression "
            "was changed -- AC-4 is text/attribute-only, the displayed value "
            "must stay byte-identical"
        )

    def test_cumulative_bot_value_format_expression_unchanged(self):
        html = _html()
        assert re.search(
            r'Bot \{% if cr_bot >= 0 %\}\+\{% endif %\}\{\{ "%\.2f"\|format\(cr_bot\) \}\}%',
            html,
        ), (
            "AC-4 FAIL: comp-cumulative-bot-text's value-format Jinja "
            "expression was changed -- AC-4 is text/attribute-only, the "
            "displayed value must stay byte-identical"
        )


# ---------------------------------------------------------------------------
# JS source-text -- updateComparisonRows references the 3 new fields
# ---------------------------------------------------------------------------


class TestUpdateComparisonRowsReferencesNewFields:
    def test_references_account_basis_stale(self):
        block = _comparison_rows_js_block(_js())
        assert "account_basis_stale" in block, (
            "AC-1: the comparison-rows render logic must read ps.account_basis_stale"
        )

    def test_references_basis_field(self):
        block = _comparison_rows_js_block(_js())
        assert re.search(r"\.basis\b", block) or '"basis"' in block or "'basis'" in block, (
            "AC-1: the comparison-rows render logic must read ps.basis"
        )

    def test_references_account_basis_as_of(self):
        block = _comparison_rows_js_block(_js())
        assert "account_basis_as_of" in block, (
            "AC-2/AC-3: the comparison-rows render logic must read "
            "ps.account_basis_as_of to disclose the stale timestamp"
        )


# ---------------------------------------------------------------------------
# AC-5 -- zero backend computation change (source-text presence pin)
# ---------------------------------------------------------------------------


class TestBackendComputationByteUnchanged:
    """These exact assignment lines (cited in the plan at app.py:1749-1758
    live / :2382-2387 frozen) must remain present verbatim. Per D2, this
    mechanism is ALREADY correct -- this cycle is render-only."""

    def test_live_basis_assignment_line_present(self):
        assert '_strip["basis"] = "value_weighted"' in _app_py(), (
            "AC-5 FAIL: the live Tier-2 VW-floor assignment line was changed "
            "or removed -- backend computation must be byte-unchanged"
        )

    def test_live_stale_assignment_line_present(self):
        assert '_strip["account_basis_stale"] = True' in _app_py(), (
            "AC-5 FAIL: the live Tier-1 stale-stamp assignment line was changed or removed"
        )

    def test_live_as_of_assignment_line_present(self):
        assert (
            '_strip["account_basis_as_of"] = _account_totals_last_success_at or datetime.now('
            in _app_py()
        ), "AC-5 FAIL: the live account_basis_as_of assignment line was changed"

    def test_frozen_basis_assignment_line_present(self):
        assert '_portfolio_strip["basis"] = "value_weighted"' in _app_py(), (
            "AC-5 FAIL: the frozen-branch Tier-2 VW-floor assignment line was changed or removed"
        )

    def test_frozen_stale_assignment_line_present(self):
        assert '_portfolio_strip["account_basis_stale"] = True' in _app_py(), (
            "AC-5 FAIL: the frozen-branch Tier-1 stale-stamp assignment line was changed or removed"
        )

    def test_frozen_as_of_assignment_line_present(self):
        assert '_portfolio_strip["account_basis_as_of"] = _snap_basis_as_of' in _app_py(), (
            "AC-5 FAIL: the frozen-branch account_basis_as_of assignment line "
            "was changed or removed"
        )


# ---------------------------------------------------------------------------
# AC-6 (optional) -- timeout constant bump
# ---------------------------------------------------------------------------


class TestTimeoutConstantBump:
    def test_timeout_constant_value_is_30(self):
        app_src = _app_py()
        m = re.search(r"^_ACCOUNT_TOTALS_HTTP_TIMEOUT_S\s*=\s*(\d+)\s*$", app_src, re.MULTILINE)
        assert m, "_ACCOUNT_TOTALS_HTTP_TIMEOUT_S constant definition not found"
        assert m.group(1) == "30", (
            f"AC-6: _ACCOUNT_TOTALS_HTTP_TIMEOUT_S must be bumped from 10 to "
            f"30 (reduce stale-flicker frequency) -- got {m.group(1)!r}"
        )

    def test_timeout_constant_stays_named_not_inlined_at_call_site(self):
        app_src = _app_py()
        assert "timeout=_ACCOUNT_TOTALS_HTTP_TIMEOUT_S" in app_src, (
            "AC-6: the requests.get call site must still reference the named "
            "constant, not an inlined literal timeout=30"
        )
        assert "_ACCOUNT_TOTALS_HTTP_TIMEOUT_S" in app_src, (
            "_ACCOUNT_TOTALS_HTTP_TIMEOUT_S constant must still exist"
        )


# ---------------------------------------------------------------------------
# Real Node-execution non-vacuity harness -- proves the render logic
# genuinely distinguishes STALE / VW-FLOOR / neither / both-flags-priority,
# not just that the field names appear somewhere in the source.
# ---------------------------------------------------------------------------


def _extract_function_with_signature(source: str, func_name: str) -> str:
    """Extract `function name(...) { ... }` via brace counting -- growth-safe,
    NOT a fixed-span slice (BL-3's own span-overflow lesson)."""
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


def _extract_contiguous_block(source: str, start_name: str, end_name: str) -> str:
    """setPosNeg (start_name) through the brace-counted close of
    updateComparisonRows (end_name) -- captures any new chip-render helper
    the implementer places between the two, per the pinned contract."""
    start_match = re.search(rf"function\s+{start_name}\s*\(", source)
    assert start_match is not None, f"{start_name} not found in static/index.js"
    end_match = re.search(rf"function\s+{end_name}\s*\([^)]*\)\s*\{{", source)
    assert end_match is not None, f"{end_name} not found in static/index.js"
    brace_start = source.index("{", end_match.start())
    depth = 0
    i = brace_start
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start_match.start() : i + 1]
        i += 1
    pytest.fail(f"Could not extract {start_name}..{end_name} -- unbalanced braces")


_CHIP_TESTIDS = ["comp-today-basis-chip", "comp-cumulative-basis-chip", "comp-mdd-basis-chip"]
_FRESHNESS_ID = "account-basis-as-of"


def _run_update_comparison_rows(portfolio_strip: dict) -> dict:
    """Executes the REAL sentinelToNull/isSentinel/fmtPct + the real
    setPosNeg..updateComparisonRows contiguous block (all extracted live from
    static/index.js, never hardcoded) against a synthetic DOM stub, and
    returns {testid_or_id: {hidden, textContent}} for the elements this test
    cares about.

    querySelector/querySelectorAll/getElementById are backed by a single
    testid/id registry -- any selector this test did NOT pre-register (all
    the pre-existing bar/text/delta/vol elements updateComparisonRows also
    touches) resolves to null/[] and the existing code's own `if (el)`
    guards no-op harmlessly, exactly as they do in the real DOM when an
    element legitimately doesn't exist.
    """
    if shutil.which("node") is None:
        pytest.skip("node not installed -- skipping updateComparisonRows execution check")

    js_src = _js()
    is_sentinel_src = _extract_function_with_signature(js_src, "isSentinel")
    sentinel_to_null_src = _extract_function_with_signature(js_src, "sentinelToNull")
    fmt_pct_src = _extract_function_with_signature(js_src, "fmtPct")
    main_block_src = _extract_contiguous_block(js_src, "setPosNeg", "updateComparisonRows")

    testids_js = json.dumps(_CHIP_TESTIDS)
    freshness_id_js = json.dumps(_FRESHNESS_ID)
    data_js = json.dumps({"portfolio_strip": portfolio_strip})

    harness = f"""
    function cs(varName) {{ return 'STUB(' + varName + ')'; }}

    var __byTestid = {{}};
    {testids_js}.forEach(function (t) {{
        __byTestid[t] = {{ hidden: true, textContent: '', title: '' }};
    }});
    var __byId = {{}};
    __byId[{freshness_id_js}] = {{ hidden: true, textContent: '', title: '' }};

    function __parseTestid(sel) {{
        var m = sel.match(/data-testid="([^"]+)"/);
        return m ? m[1] : null;
    }}
    function __parseId(sel) {{
        var m = sel.match(/^#([\\w-]+)$/);
        return m ? m[1] : null;
    }}

    var document = {{
        documentElement: {{ dataset: {{}} }},
        getElementById: function (id) {{
            return Object.prototype.hasOwnProperty.call(__byId, id) ? __byId[id] : null;
        }},
        querySelector: function (sel) {{
            var idKey = __parseId(sel);
            if (idKey) {{
                return Object.prototype.hasOwnProperty.call(__byId, idKey) ? __byId[idKey] : null;
            }}
            var t = __parseTestid(sel);
            if (t && Object.prototype.hasOwnProperty.call(__byTestid, t)) return __byTestid[t];
            return null;
        }},
        querySelectorAll: function (sel) {{
            var el = this.querySelector(sel);
            return el ? [el] : [];
        }}
    }};

    {is_sentinel_src}
    {sentinel_to_null_src}
    {fmt_pct_src}
    {main_block_src}

    var DATA = {data_js};
    updateComparisonRows(DATA);

    var out = {{}};
    Object.keys(__byTestid).forEach(function (t) {{ out[t] = __byTestid[t]; }});
    out[{freshness_id_js}] = __byId[{freshness_id_js}];
    console.log(JSON.stringify(out));
    """

    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"updateComparisonRows harness crashed (this itself proves a real "
        f"defect -- e.g. an unguarded MDD-chip write):\n{result.stderr}"
    )
    return json.loads(result.stdout.strip())


def _run_update_comparison_rows_sequence(portfolio_strip_sequence: list) -> dict:
    """Sufficiency-review addition (post-GREEN, team-lead-authorized direct
    pin -- same precedent as BL-3's legacy pin): identical harness to
    `_run_update_comparison_rows`, but drives updateComparisonRows through
    MULTIPLE sequential calls against the SAME DOM stub (never re-created
    between calls) before capturing final state. This is the real per-poll
    shape of production (loadState()/EventSource call updateComparisonRows
    repeatedly against the live DOM) -- a worse implementation that renders
    a chip on a stale poll but has no reset branch for a SUBSEQUENT healthy
    poll would pass every single-call scenario test above undetected, since
    those all start from a pristine, never-before-touched element.
    """
    if shutil.which("node") is None:
        pytest.skip("node not installed -- skipping updateComparisonRows execution check")

    js_src = _js()
    is_sentinel_src = _extract_function_with_signature(js_src, "isSentinel")
    sentinel_to_null_src = _extract_function_with_signature(js_src, "sentinelToNull")
    fmt_pct_src = _extract_function_with_signature(js_src, "fmtPct")
    main_block_src = _extract_contiguous_block(js_src, "setPosNeg", "updateComparisonRows")

    testids_js = json.dumps(_CHIP_TESTIDS)
    freshness_id_js = json.dumps(_FRESHNESS_ID)
    sequence_js = json.dumps([{"portfolio_strip": ps} for ps in portfolio_strip_sequence])

    harness = f"""
    function cs(varName) {{ return 'STUB(' + varName + ')'; }}

    var __byTestid = {{}};
    {testids_js}.forEach(function (t) {{
        __byTestid[t] = {{ hidden: true, textContent: '', title: '' }};
    }});
    var __byId = {{}};
    __byId[{freshness_id_js}] = {{ hidden: true, textContent: '', title: '' }};

    function __parseTestid(sel) {{
        var m = sel.match(/data-testid="([^"]+)"/);
        return m ? m[1] : null;
    }}
    function __parseId(sel) {{
        var m = sel.match(/^#([\\w-]+)$/);
        return m ? m[1] : null;
    }}

    var document = {{
        documentElement: {{ dataset: {{}} }},
        getElementById: function (id) {{
            return Object.prototype.hasOwnProperty.call(__byId, id) ? __byId[id] : null;
        }},
        querySelector: function (sel) {{
            var idKey = __parseId(sel);
            if (idKey) {{
                return Object.prototype.hasOwnProperty.call(__byId, idKey) ? __byId[idKey] : null;
            }}
            var t = __parseTestid(sel);
            if (t && Object.prototype.hasOwnProperty.call(__byTestid, t)) return __byTestid[t];
            return null;
        }},
        querySelectorAll: function (sel) {{
            var el = this.querySelector(sel);
            return el ? [el] : [];
        }}
    }};

    {is_sentinel_src}
    {sentinel_to_null_src}
    {fmt_pct_src}
    {main_block_src}

    // Drive the SAME DOM stub through every payload in sequence -- exactly
    // how loadState()/EventSource re-invoke updateComparisonRows against
    // one persistent live DOM on every poll.
    var SEQUENCE = {sequence_js};
    SEQUENCE.forEach(function (DATA) {{ updateComparisonRows(DATA); }});

    var out = {{}};
    Object.keys(__byTestid).forEach(function (t) {{ out[t] = __byTestid[t]; }});
    out[{freshness_id_js}] = __byId[{freshness_id_js}];
    console.log(JSON.stringify(out));
    """

    result = subprocess.run(["node", "-e", harness], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, (
        f"updateComparisonRows sequential harness crashed:\n{result.stderr}"
    )
    return json.loads(result.stdout.strip())


class TestStaleToHealthyTransitionResetsChipsAndFreshness:
    """Sufficiency-review addition (post-GREEN review, probe 1): every
    scenario in TestChipRenderNonVacuity below calls updateComparisonRows
    exactly ONCE against a pristine DOM element. Since this function runs on
    every poll cycle (loadState()/EventSource -> updateComparisonRows) against
    the SAME persistent live DOM, a worse implementation that renders the
    chip/freshness content on a stale poll but omits (or mis-orders) the
    reset branch for a later healthy poll would pass every single-call test
    undetected -- a chip left permanently STUCK after real recovery. This
    class proves the reset genuinely fires on a stale-to-healthy transition,
    and separately on a stale-to-VW-floor transition (a different non-stale
    outcome, not just the fully-clean case)."""

    def test_chip_and_freshness_reset_after_stale_then_healthy_poll(self):
        as_of = "2026-08-05 14:32:00 ET"
        out = _run_update_comparison_rows_sequence(
            [
                {
                    "today_change": {},
                    "cumulative_return": {},
                    "max_drawdown": {},
                    "account_basis_stale": True,
                    "account_basis_as_of": as_of,
                },
                {
                    "today_change": {},
                    "cumulative_return": {},
                    "max_drawdown": {},
                },
            ]
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert out[testid]["hidden"] is True, (
                f"TRANSITION FAIL: {testid} must be re-hidden once account "
                f"recovers on a subsequent poll -- a chip left visible after "
                f"real recovery is a stuck-stale false alarm. Got hidden="
                f"{out[testid]['hidden']!r}"
            )
            assert out[testid]["textContent"] == "", (
                f"TRANSITION FAIL: {testid}'s textContent must be cleared on "
                f"recovery, not left showing the prior poll's 'Stale account "
                f"data (as of {as_of})' text. Got {out[testid]['textContent']!r}"
            )
        assert out[_FRESHNESS_ID]["hidden"] is True, (
            "TRANSITION FAIL: the freshness element must be re-hidden once "
            "account recovers -- a stale timestamp must not linger after "
            "the account basis is healthy again"
        )
        assert out[_FRESHNESS_ID]["textContent"] == "", (
            "TRANSITION FAIL: the freshness element's textContent must be cleared on recovery"
        )

    def test_chip_reflects_only_the_latest_poll_when_transitioning_stale_to_vw_floor(self):
        """A second, distinct transition: stale (with a real as-of) directly
        to value-weighted-floor (no as-of at all) on the very next poll --
        proves the chip doesn't retain the prior poll's as-of timestamp text
        inside the new VW-floor wording."""
        stale_as_of = "2026-08-05 09:00:00 ET"
        out = _run_update_comparison_rows_sequence(
            [
                {
                    "today_change": {},
                    "cumulative_return": {},
                    "max_drawdown": {},
                    "account_basis_stale": True,
                    "account_basis_as_of": stale_as_of,
                    # DE-AUDIT-BL4-001 live-gate fix (6d3e8226): the chip
                    # helpers now only act on payloads that carry
                    # data_as_of -- production _compute_portfolio_strip
                    # ALWAYS sets it on every /api/state poll, so this
                    # marks the fixture as genuinely state-poll-shaped
                    # (not windowed-strip-shaped, which never carries it).
                    "data_as_of": "15:45 ET",
                },
                {
                    "today_change": {},
                    "cumulative_return": {},
                    "max_drawdown": {},
                    "basis": "value_weighted",
                    "data_as_of": "15:46 ET",
                },
            ]
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert stale_as_of not in out[testid]["textContent"], (
                f"TRANSITION FAIL: {testid} must not carry the PRIOR poll's "
                f"stale as-of timestamp ({stale_as_of!r}) once the account "
                f"basis has moved to the value-weighted floor -- got "
                f"{out[testid]['textContent']!r}"
            )
            assert re.search(r"value.?weighted", out[testid]["textContent"], re.IGNORECASE), (
                f"TRANSITION FAIL: {testid} must show the CURRENT poll's "
                f"value-weighted-floor wording -- got "
                f"{out[testid]['textContent']!r}"
            )
        # No as-of timestamp exists in the VW-floor tier -- the freshness
        # element from the prior stale poll must be cleared, not left
        # showing an as-of time that no longer describes reality.
        assert out[_FRESHNESS_ID]["hidden"] is True
        assert stale_as_of not in out[_FRESHNESS_ID]["textContent"]


class TestChipRenderNonVacuity:
    """Real execution: proves the implementation genuinely distinguishes the
    4 scenarios, not merely that the field names appear in the source."""

    def test_stale_scenario_shows_stale_wording_and_as_of_on_both_rows(self):
        as_of = "2026-08-05 14:32:00 ET"
        out = _run_update_comparison_rows(
            {
                "today_change": {},
                "cumulative_return": {},
                "max_drawdown": {},
                "account_basis_stale": True,
                "account_basis_as_of": as_of,
                # DE-AUDIT-BL4-001 live-gate fix (6d3e8226): marks this
                # fixture as genuinely state-poll-shaped -- see the
                # matching comment in TestStaleToHealthyTransitionResetsChipsAndFreshness.
                "data_as_of": "15:45 ET",
            }
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert out[testid]["hidden"] is False, (
                f"AC-1: {testid} must be shown (hidden=false) when account_basis_stale is true"
            )
            assert re.search(r"stale", out[testid]["textContent"], re.IGNORECASE), (
                f"AC-2: {testid} must carry 'stale' wording -- got {out[testid]['textContent']!r}"
            )
            assert as_of in out[testid]["textContent"], (
                f"AC-2: {testid} must disclose the account_basis_as_of "
                f"timestamp -- got {out[testid]['textContent']!r}"
            )
        assert out[_FRESHNESS_ID]["hidden"] is False, (
            "AC-3: the freshness element must be shown when stale"
        )
        assert as_of in out[_FRESHNESS_ID]["textContent"], (
            "AC-3: the freshness element must disclose account_basis_as_of"
        )

    def test_value_weighted_floor_scenario_shows_vw_wording_no_stale_no_timestamp(self):
        out = _run_update_comparison_rows(
            {
                "today_change": {},
                "cumulative_return": {},
                "max_drawdown": {},
                "basis": "value_weighted",
                # DE-AUDIT-BL4-001 live-gate fix (6d3e8226): marks this
                # fixture as genuinely state-poll-shaped.
                "data_as_of": "15:45 ET",
            }
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert out[testid]["hidden"] is False, (
                f"AC-1: {testid} must be shown when basis=='value_weighted'"
            )
            assert re.search(r"value.?weighted", out[testid]["textContent"], re.IGNORECASE), (
                f"AC-2: {testid} must carry value-weighted wording -- "
                f"got {out[testid]['textContent']!r}"
            )
            assert not re.search(r"stale", out[testid]["textContent"], re.IGNORECASE), (
                f"AC-2 FAIL: {testid} must NOT say 'stale' in the "
                f"value-weighted-floor-only scenario -- got "
                f"{out[testid]['textContent']!r}"
            )
        # Edge case (plan): no account_basis_as_of exists in this scenario --
        # must not fabricate one. The freshness element has nothing honest to
        # disclose here, so it must stay hidden.
        assert out[_FRESHNESS_ID]["hidden"] is True, (
            "AC-3 edge case FAIL: the freshness element must stay hidden in "
            "the VW-floor-only scenario -- there is no account_basis_as_of "
            "timestamp to honestly disclose, and none may be fabricated"
        )

    def test_neither_flag_scenario_is_a_zero_visual_change_empty_state(self):
        out = _run_update_comparison_rows(
            {
                "today_change": {},
                "cumulative_return": {},
                "max_drawdown": {},
            }
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert out[testid]["hidden"] is True, (
                f"AC-1 edge case FAIL: {testid} must stay hidden when neither "
                f"account_basis_stale nor basis=='value_weighted' fires -- "
                f"zero visual change from today's dashboard"
            )
        assert out[_FRESHNESS_ID]["hidden"] is True

    def test_both_flags_true_simultaneously_stale_wins_never_conflated(self):
        """AC-2's 'never conflated' requirement, defensively tested even
        though the D2-corrected backend tiers are practically mutually
        exclusive -- approved priority rule: stale wins."""
        as_of = "2026-08-05 09:15:00 ET"
        out = _run_update_comparison_rows(
            {
                "today_change": {},
                "cumulative_return": {},
                "max_drawdown": {},
                "basis": "value_weighted",
                "account_basis_stale": True,
                "account_basis_as_of": as_of,
                # DE-AUDIT-BL4-001 live-gate fix (6d3e8226): marks this
                # fixture as genuinely state-poll-shaped.
                "data_as_of": "15:45 ET",
            }
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert re.search(r"stale", out[testid]["textContent"], re.IGNORECASE), (
                f"AC-2 priority-rule FAIL: {testid} must show STALE wording "
                f"when both flags are true -- got {out[testid]['textContent']!r}"
            )
            assert as_of in out[testid]["textContent"]

    def test_mdd_chip_canary_never_touched_across_any_scenario(self):
        """Positive-execution enforcement of the AC-1 negative requirement:
        even if the implementer mistakenly wired MDD too, this canary
        element (pre-registered exactly like the two real chips) must remain
        at its untouched default across the scenario that WOULD populate a
        real chip."""
        as_of = "2026-08-05 14:32:00 ET"
        out = _run_update_comparison_rows(
            {
                "today_change": {},
                "cumulative_return": {},
                "max_drawdown": {},
                "account_basis_stale": True,
                "account_basis_as_of": as_of,
            }
        )
        assert out["comp-mdd-basis-chip"]["hidden"] is True, (
            "AC-1 FAIL: comp-mdd-basis-chip was shown -- the Max DD row must "
            "never render the account-basis honesty chip"
        )
        assert out["comp-mdd-basis-chip"]["textContent"] == "", (
            "AC-1 FAIL: comp-mdd-basis-chip's textContent was written -- the "
            "Max DD row must never render the account-basis honesty chip"
        )

    def test_scenarios_are_pairwise_distinct(self):
        """Non-vacuity: the stale/VW-floor/empty scenarios must produce
        genuinely different chip text -- proves this suite can distinguish a
        real implementation from a stub that always renders the same thing."""
        stale = _run_update_comparison_rows(
            {
                "today_change": {},
                "cumulative_return": {},
                "max_drawdown": {},
                "account_basis_stale": True,
                "account_basis_as_of": "2026-08-05 14:32:00 ET",
                # DE-AUDIT-BL4-001 live-gate fix (6d3e8226): marks this
                # fixture as genuinely state-poll-shaped.
                "data_as_of": "15:45 ET",
            }
        )
        vw_floor = _run_update_comparison_rows(
            {
                "today_change": {},
                "cumulative_return": {},
                "max_drawdown": {},
                "basis": "value_weighted",
                "data_as_of": "15:45 ET",
            }
        )
        # "empty" deliberately stays data_as_of-FREE -- it represents the
        # genuinely-neither-flag case, which must produce the same
        # no-chip outcome whether or not the payload is state-poll-shaped.
        empty = _run_update_comparison_rows(
            {"today_change": {}, "cumulative_return": {}, "max_drawdown": {}}
        )
        stale_text = stale["comp-today-basis-chip"]["textContent"]
        vw_text = vw_floor["comp-today-basis-chip"]["textContent"]
        empty_text = empty["comp-today-basis-chip"]["textContent"]
        assert stale_text != vw_text, (
            "non-vacuity FAIL: stale and value-weighted-floor chip text must differ"
        )
        assert stale_text != empty_text, (
            "non-vacuity FAIL: stale and empty-state chip text must differ"
        )
        assert vw_text != empty_text, (
            "non-vacuity FAIL: value-weighted-floor and empty-state chip text must differ"
        )


# ---------------------------------------------------------------------------
# LIVE-GATE-FOUND BUG: updateComparisonRows has TWO production callers --
# the /api/state poll (loadState()) AND fetchWindowedStrip (index.js:1457-1470),
# which wraps /api/strip/<token>'s response as {portfolio_strip: strip} and
# follows every state poll. The windowed strip's real key set (verified against
# analytics.compute_windowed_portfolio_strip's own docstring, analytics.py:1868-1876,
# and PM's live render-gate capture) is EXACTLY: today_change, cumulative_return,
# max_drawdown, vol_bot, vol_held, guard_alpha, insufficient_history, window --
# it NEVER carries basis/account_basis_stale/account_basis_as_of. Since the
# chip/freshness helpers reset-to-hidden whenever those 2 flags are absent,
# every windowed-strip poll wipes a genuinely-stale chip the very next tick.
# Live-verified on the render gate: injected stale state -> chips stayed hidden.
# ---------------------------------------------------------------------------


def _windowed_strip_shaped_payload() -> dict:
    """Exact real /api/strip/<token> response shape (analytics.py:1868-1876) --
    the 8 keys that function returns, nothing more, nothing account-basis-related.
    Deliberately does NOT reuse today_change/cumulative_return/max_drawdown's
    empty-dict shorthand from earlier fixtures -- these carry real dry_run/if_held
    numbers here, matching what the route genuinely returns."""
    return {
        "today_change": {"dry_run": 5.0, "if_held": 3.0},
        "cumulative_return": {"dry_run": 10.0, "if_held": 8.0},
        "max_drawdown": {"dry_run": -2.0, "if_held": -3.0},
        "vol_bot": 0.15,
        "vol_held": 0.18,
        "guard_alpha": 1.5,
        "insufficient_history": False,
        "window": "30d",
    }


def _genuine_state_poll_shaped_payload(*, extra: dict | None = None) -> dict:
    """The REAL /api/state portfolio_strip shape on a HEALTHY poll -- mirrors
    _compute_portfolio_strip's own `_strip = {...}` dict literal verbatim
    (app.py:1720-1736): today_change/cumulative_return/max_drawdown/account_value/
    vol_bot/vol_held/hist_dates/hist_bot/hist_held/hist_source/data_as_of are
    UNCONDITIONAL keys on every /api/state poll (state-poll and windowed-strip
    payloads never overlap on this superset). `basis`/`account_basis_stale`/
    `account_basis_as_of` are the ONLY conditionally-added keys (app.py:1749-1758)
    -- omitted here for the genuinely-healthy case; `extra` merges in any of those
    3 keys a specific test wants to add on top (e.g. an explicit `basis` value)."""
    payload = {
        "today_change": {"dry_run": 1.0, "if_held": 0.5},
        "cumulative_return": {"dry_run": 5.0, "if_held": 4.0},
        "max_drawdown": {"dry_run": -1.0, "if_held": -1.5},
        "account_value": 100000.0,
        "vol_bot": 0.12,
        "vol_held": 0.14,
        "hist_dates": ["2026-08-01", "2026-08-02"],
        "hist_bot": [0.0, 0.5],
        "hist_held": [0.0, 0.3],
        "hist_source": "shadow_history",
        "data_as_of": "15:45 ET",
    }
    if extra:
        payload.update(extra)
    return payload


class TestWindowedStripPollNeverWipesAStaleChip:
    """PRIMARY bug pin: a stale state-poll followed by a windowed-strip-shaped
    poll (the real, always-following-every-state-poll second caller) must NOT
    clear the chip/freshness the first poll set -- the windowed strip carries
    no account-basis truth at all, so it must never be treated as "healthy"."""

    def test_stale_chip_survives_a_subsequent_windowed_strip_poll(self):
        as_of = "2026-08-05 11:00:00 ET"
        out = _run_update_comparison_rows_sequence(
            [
                _genuine_state_poll_shaped_payload(
                    extra={"account_basis_stale": True, "account_basis_as_of": as_of}
                ),
                _windowed_strip_shaped_payload(),
            ]
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert out[testid]["hidden"] is False, (
                f"LIVE-GATE-BUG FAIL: {testid} was wiped by the windowed-strip "
                f"poll that structurally never carries account-basis fields -- "
                f"a genuinely stale account was silently un-flagged. "
                f"hidden={out[testid]['hidden']!r}"
            )
            assert re.search(r"stale", out[testid]["textContent"], re.IGNORECASE), (
                f"LIVE-GATE-BUG FAIL: {testid} must still show the STALE "
                f"wording set by the first (state-poll) call -- got "
                f"{out[testid]['textContent']!r}"
            )
            assert as_of in out[testid]["textContent"], (
                f"LIVE-GATE-BUG FAIL: {testid} must still disclose the original "
                f"as-of timestamp from the state poll, not be reset by the "
                f"windowed-strip poll -- got {out[testid]['textContent']!r}"
            )
        assert out[_FRESHNESS_ID]["hidden"] is False, (
            "LIVE-GATE-BUG FAIL: the freshness element must survive a "
            "subsequent windowed-strip poll"
        )
        assert as_of in out[_FRESHNESS_ID]["textContent"]

    def test_vw_floor_chip_also_survives_a_subsequent_windowed_strip_poll(self):
        """Same bug, the other honesty-tier: a VW-floor chip must also survive
        a windowed-strip poll immediately after it (not just the stale tier)."""
        out = _run_update_comparison_rows_sequence(
            [
                _genuine_state_poll_shaped_payload(extra={"basis": "value_weighted"}),
                _windowed_strip_shaped_payload(),
            ]
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert out[testid]["hidden"] is False, (
                f"LIVE-GATE-BUG FAIL: {testid} (value-weighted-floor tier) was "
                f"wiped by the subsequent windowed-strip poll"
            )
            assert re.search(r"value.?weighted", out[testid]["textContent"], re.IGNORECASE)


class TestGenuineHealthyStatePollStillResetsChip:
    """Inverse pin: whatever mechanism closes the bug above must NOT regress
    the real stale-to-healthy reset -- a genuinely healthy /api/state poll
    (the FULL real state-poll key shape, not the windowed-strip shape) must
    still clear a stale chip. Two variants: the common real case (no basis-
    related keys at all on a healthy poll -- app.py never sets them outside
    the VW-floor branch) and an explicit-value variant pinned per the PM's
    fix-design brief (a `basis` key present with a non-VW value)."""

    def test_healthy_full_shape_state_poll_with_no_basis_keys_still_resets(self):
        as_of = "2026-08-05 09:00:00 ET"
        out = _run_update_comparison_rows_sequence(
            [
                _genuine_state_poll_shaped_payload(
                    extra={"account_basis_stale": True, "account_basis_as_of": as_of}
                ),
                _genuine_state_poll_shaped_payload(),  # no basis-related keys -- real healthy shape
            ]
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert out[testid]["hidden"] is True, (
                f"REGRESSION FAIL: {testid} must still reset on a genuinely "
                f"healthy full-shape /api/state poll (the real common case: "
                f"no basis-related keys at all) -- the fix for the "
                f"windowed-strip-wipe bug must not break this"
            )
            assert out[testid]["textContent"] == ""
        assert out[_FRESHNESS_ID]["hidden"] is True

    def test_healthy_state_poll_with_explicit_basis_account_value_resets(self):
        """PM fix-design brief's literal fixture: a clean state-poll payload
        that explicitly carries `basis: "account"` (present, non-VW value)."""
        out = _run_update_comparison_rows_sequence(
            [
                _genuine_state_poll_shaped_payload(
                    extra={
                        "account_basis_stale": True,
                        "account_basis_as_of": "2026-08-05 08:00:00 ET",
                    }
                ),
                _genuine_state_poll_shaped_payload(extra={"basis": "account"}),
            ]
        )
        for testid in ("comp-today-basis-chip", "comp-cumulative-basis-chip"):
            assert out[testid]["hidden"] is True, (
                f"REGRESSION FAIL: {testid} must reset when the second poll "
                f"is a healthy state-poll payload explicitly carrying "
                f"basis='account'"
            )
        assert out[_FRESHNESS_ID]["hidden"] is True
