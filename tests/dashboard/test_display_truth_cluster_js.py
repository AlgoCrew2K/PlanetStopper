"""
RED tests -- display-truth cluster JS static-source pins: F-011, F-014, F-016
(locus 1 only -- locus 2 is the per-card SSR Jinja bug, covered by
tests/dashboard/test_f016_per_card_null_today_honest_empty_state.py), F-025.

This repo has no JS execution harness -- the function-slice + tight-window regex
idiom below mirrors tests/dashboard/test_f7_ac2_render_surfaces_js.py's established
rigor bar (confirmed acceptable at F7 plan approval, reused here per the fdc-tw plan
approved by main 2026-07-21).

NECESSARY-NOT-SUFFICIENT: a source-text assertion can pass while the underlying
logic is still subtly wrong -- these tests check the CONTRACT SHAPE, not full
runtime behavior. The BEHAVIORAL guarantee comes from the PM's live E2E gate
(feature-plans/fix-display-cluster.md Architecture: local Flask render harness +
Playwright) -- not from this file alone.

PM RULING (plan approval, 2026-07-21) on F-025: the Y-axis visibility is the
requirement (magnitude conflation is the defect); X-axis visibility is the
implementer's judgment call (the as-of span label already covers time) -- this
file asserts ONLY the y-axis.
"""

from __future__ import annotations

import pathlib
import re

_STATIC_DIR = pathlib.Path(__file__).parent.parent.parent / "static"
_INDEX_JS = _STATIC_DIR / "index.js"


def _read_index_js() -> str:
    assert _INDEX_JS.exists(), "static/index.js must exist"
    return _INDEX_JS.read_text(encoding="utf-8")


def _slice_function(content: str, signature: str, max_len: int = 3000) -> str:
    """Return the text from the first occurrence of `signature` up to the START
    of the NEXT top-level `    function ` declaration (this file's 4-space
    indentation convention), capped at max_len. Mirrors
    tests/dashboard/test_f7_ac2_render_surfaces_js.py's _slice_function."""
    start = content.find(signature)
    assert start != -1, f"{signature!r} not found in static/index.js"
    next_fn = content.find("\n    function ", start + len(signature))
    end = min(next_fn, start + max_len) if next_fn != -1 else start + max_len
    return content[start:end]


# ===========================================================================
# F-011 -- section-count badges must read the real field (data.state /
# data.bot_state), not the non-existent-on-the-closed-branch data.symphonies
# ===========================================================================


class TestF011SectionBadgesReadRealField:
    def test_update_section_meta_no_longer_sources_data_symphonies(self):
        """updateSectionMeta (~index.js:1192-1193) today reads
        `Array.isArray(data.symphonies) ? data.symphonies : []` -- data.symphonies
        genuinely does not exist on the closed/frozen /api/state branch (app.py
        emits `state`/`bot_state` only there), so this silently evaluates to []
        every time the market is closed, and the badges get overwritten to 0 on
        every poll. The exact broken construct must be gone."""
        content = _read_index_js()
        body = _slice_function(content, "function updateSectionMeta(")
        assert not re.search(
            r"Array\.isArray\(\s*data\.symphonies\s*\)\s*\?\s*data\.symphonies\s*:\s*\[\s*\]",
            body,
        ), (
            "F-011 FAIL: updateSectionMeta still sources its badge-count list from "
            "`Array.isArray(data.symphonies) ? data.symphonies : []` -- this field "
            "is genuinely absent on the closed/frozen /api/state branch (confirmed "
            "via app.py: that branch emits only `state`/`bot_state`), so the badges "
            "deterministically read 0 whenever the market is closed."
        )

    def test_update_section_meta_sources_state_or_bot_state_before_the_filters(self):
        """The badge-count filters (`.filter(function (s) { return s.armed || ...`)
        must be fed from `data.state` or `data.bot_state` (present on BOTH the
        open and closed/frozen /api/state branches) -- and that reference must
        appear BEFORE the filter calls, not merely exist somewhere else in the
        function (positional rigor, per the F7 precedent's "not just
        co-occurrence" standard)."""
        content = _read_index_js()
        body = _slice_function(content, "function updateSectionMeta(")

        source_match = re.search(r"data\.(bot_state|state)\b", body)
        filter_match = re.search(r"\.filter\(\s*function\s*\(\s*s\s*\)", body)
        assert filter_match is not None, (
            "the badge-count .filter(function (s) {...}) construct was not found -- "
            "this test's location assumptions may be stale; update the markers."
        )
        assert source_match is not None and source_match.start() < filter_match.start(), (
            "F-011 FAIL: updateSectionMeta has no `data.state`/`data.bot_state` "
            "reference BEFORE the badge-count .filter(...) calls -- the real field "
            "(present on both the open and closed/frozen branches, per "
            "app.py:2415-2416/2039-2040) must be what feeds the counts, not just "
            "referenced incidentally elsewhere in the function."
        )


# ===========================================================================
# F-014 -- the "Cumulative · lifetime" row must source the lifetime
# cumulative_return, never the windowed one (source fix, not a relabel --
# plan's chosen Architecture)
# ===========================================================================


class TestF014CumulativeLifetimeRowSourcesLifetimeValue:
    def test_cumulative_row_values_do_not_prefer_windowed_cumulative_return(self):
        """updateComparisonRows' row-definition array (~index.js:917-925) currently
        has: `{ id: 'cumulative', ..., values: ps.windowed_cumulative_return ||
        ps.cumulative_return || {}, ... }` -- clobbering the SSR-correct lifetime
        value (templates/index.html:920 labels this row "Cumulative · lifetime")
        with a windowed one on every 30s poll. Scoped to the 'cumulative' row
        entry specifically (not 'today' or 'mdd', which legitimately use their
        own values) via a tight window from `id: 'cumulative'` to the entry's
        closing brace.

        Asserted on the SOURCE EXPRESSION, not a numeric equality -- per the
        plan's edge case: at the current dataset age (<30 days) the windowed and
        lifetime values numerically coincide, so a value-only comparison would
        pass today and silently regress as history accumulates."""
        content = _read_index_js()
        body = _slice_function(content, "function updateComparisonRows(")

        row_anchor = body.find("id: 'cumulative'")
        assert row_anchor != -1, (
            "the `id: 'cumulative'` row entry was not found in updateComparisonRows "
            "-- this test's location assumptions may be stale; update the markers."
        )
        row_close = body.find("},", row_anchor)
        row_entry = body[row_anchor : row_close if row_close != -1 else row_anchor + 400]

        assert "windowed_cumulative_return" not in row_entry, (
            "F-014 FAIL: the 'cumulative' row entry still prefers "
            "ps.windowed_cumulative_return -- the row's SSR label ('Cumulative · "
            "lifetime', templates/index.html:920) promises the account-lifetime "
            "value, but this JS re-poll path clobbers it with a windowed one every "
            f"30s. Row entry: {row_entry!r}"
        )
        assert "ps.cumulative_return" in row_entry, (
            "F-014 FAIL: the 'cumulative' row entry no longer sources "
            f"ps.cumulative_return at all -- row entry: {row_entry!r}"
        )


# ===========================================================================
# F-016 locus 1 -- the portfolio-header Today/Cumulative/MDD rows must not
# coerce a genuinely-null bot/held value to 0 before fmtPct ever sees it
# ===========================================================================


class TestF016PortfolioHeaderNullNotCoercedToZero:
    def test_bot_and_held_extraction_no_longer_forces_null_to_zero(self):
        """updateComparisonRows' per-row bot/held extraction (~index.js:927-928)
        currently reads:
          var bot  = sentinelToNull(...) || 0;
          var held = sentinelToNull(...) || 0;
        The trailing `|| 0` discards sentinelToNull's own null result BEFORE
        fmtPct(bot)/fmtPct(held) (called later in this same function) ever gets a
        chance to render its own honest '--' empty-state for null -- a real
        missing value renders a false '+0.00%' instead. The exact coercing
        construct must be gone for BOTH bot and held."""
        content = _read_index_js()
        body = _slice_function(content, "function updateComparisonRows(")

        coercion_pattern = re.compile(r"sentinelToNull\([^;]*?\)\s*\|\|\s*0\s*;")
        matches = coercion_pattern.findall(body)
        assert not matches, (
            "F-016 FAIL: updateComparisonRows still coerces sentinelToNull(...)'s "
            "null result to 0 via a trailing `|| 0` before fmtPct ever sees it -- "
            f"found {len(matches)} occurrence(s): {matches!r}. A genuinely missing "
            "bot/held value must survive as null through to fmtPct (which already "
            "has its own honest null->'--' branch) instead of being pre-coerced."
        )

    def test_bot_and_held_extraction_has_an_explicit_null_check(self):
        """Positional/adversarial companion to the above: removing the `|| 0`
        coercion is necessary but not sufficient -- a naive removal without any
        null-handling could let a null value flow into arithmetic (e.g. `bot -
        held`) or into setPosNeg's `value >= 0` check, which evaluates TRUE for
        null in JS (null coerces to 0 in a numeric comparison) -- silently
        mis-classifying a missing value as 'positive'/green. This test requires
        SOME explicit null-check token to exist in the row-processing body,
        proving the implementer actively handled the null case rather than just
        deleting the coercion and hoping."""
        content = _read_index_js()
        body = _slice_function(content, "function updateComparisonRows(")
        assert re.search(r"(==|===)\s*null|!=\s*null|!==\s*null", body), (
            "F-016 FAIL: updateComparisonRows has no explicit null-check anywhere "
            "in its body -- removing the `|| 0` coercion without also guarding "
            "downstream null usage (setPosNeg's `value >= 0`, which is TRUE for "
            "null in JS; arithmetic deltas) can silently mis-render a missing "
            "value as a false positive/green reading instead of the honest "
            "empty-state."
        )


# ===========================================================================
# F-025 -- hero chart Y-axis must be visible (magnitude conflation is the
# defect); plotted series must stay byte-unchanged
# ===========================================================================


class TestF025HeroChartYAxisVisible:
    def test_y_axis_display_is_not_false(self):
        """renderHeroChart's Chart.js config (~index.js:117-122) currently sets
        `scales: { x: { display: false }, y: { display: false } }` -- BOTH axes
        hidden. The hero chart plots a different metric (day-by-day cumulative,
        shadow-history basis, ~-3%) directly beneath the VW-lifetime scalar
        headline (~+34%) -- opposite sign, ~12x magnitude -- with no axis to
        self-diagnose against. PM ruling: the Y-axis (magnitude) is the
        requirement; the X-axis is the implementer's judgment call (the
        hero-data-as-of legend span already covers time) -- this test asserts
        ONLY the y-axis literal `display: false` construct is gone."""
        content = _read_index_js()
        body = _slice_function(content, "function renderHeroChart(")
        assert not re.search(r"y:\s*\{\s*display:\s*false\s*\}", body), (
            "F-025 FAIL: renderHeroChart's Chart.js scales config still sets "
            "`y: { display: false }` -- the hero chart's own magnitude scale must "
            "be visible so its ~-3% day-by-day series can't be visually conflated "
            "with the adjacent ~+34% lifetime scalar headline (a ~12x, "
            "opposite-sign gap the audit flagged as reading like 'the tool "
            "contradicts itself')."
        )

    def test_plotted_series_assignment_is_byte_unchanged(self):
        """Regression pin (AC-5: presentation-only, plotted SERIES byte-unchanged):
        applyHeroWindow must still assign the raw sliced bot/held arrays straight
        into the Chart.js datasets, with no transform introduced alongside the
        axis/label fix."""
        content = _read_index_js()
        body = _slice_function(content, "function applyHeroWindow(")
        assert "_cumChart.data.datasets[0].data = sliced.bot;" in body, (
            "F-025 regression FAIL: applyHeroWindow no longer assigns "
            "sliced.bot verbatim to dataset[0].data -- the plotted Bot series "
            "must stay byte-unchanged; only axis/label presentation may change."
        )
        assert "_cumChart.data.datasets[1].data = sliced.held;" in body, (
            "F-025 regression FAIL: applyHeroWindow no longer assigns "
            "sliced.held verbatim to dataset[1].data -- the plotted Held series "
            "must stay byte-unchanged; only axis/label presentation may change."
        )
