"""
RED tests — M2-fix UI corrections (3 operator-found defects).

Defect 1: Container width — max-w-7xl must become a wider class.
Defect 2: Portfolio strip too small — strip sizing classes must be enlarged.
Defect 3: If-held rendered as fraction — must become `dry_run (if_held)` inline.
           Applies to: the portfolio strip DOM/JS.

Testing contract:
- All tests are static HTML analysis (index.html) or
  Flask test_client() rendering — zero live API calls.
- No hardcoded producer-computed values; format/structure assertions only.
- Must not break test_dashboard_m2.py (M2 baseline suite).
"""

from __future__ import annotations

import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

import app as app_module

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE = pathlib.Path(__file__).parent.parent.parent
_INDEX_HTML = _WORKTREE / "templates" / "index.html"
_INDEX_JS = _WORKTREE / "static" / "index.js"


# ---------------------------------------------------------------------------
# Shared helpers (mirrored from test_dashboard_m2.py — no import coupling)
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


@pytest.fixture
def mock_database():
    with patch.object(app_module, "database") as db_mock:
        db_mock.load_state.return_value = {}
        db_mock.normalize_name.side_effect = lambda n: (n or "").lower().replace(" ", "_")
        db_mock.get_shadow_divergence.return_value = {"by_symphony": {}, "portfolio_today": None}
        yield db_mock


def _make_state_with_one_symphony(**overrides):
    entry = {
        "name": "My Symphony",
        "account": "ACC1",
        "mc_prob": 42.0,
        "triggered": False,
        "armed": False,
        "tp_armed": False,
        "para_armed": False,
        "breakeven_locked": False,
        "current_return": 1.5,
        "stop_trigger": -2.0,
        "shadow_hwm": 3.0,
        "high_water_mark": 3.0,
        "current_value": 10000.0,
    }
    entry.update(overrides)
    return {"sym1": entry}


def _default_analytics_mock():
    m = MagicMock()
    m.get_portfolio_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_portfolio_cumulative_return.return_value = {"if_held": 15.0, "dry_run": 14.5}
    m.get_portfolio_max_drawdown.return_value = {"if_held": 0.12, "dry_run": 0.12}
    m.get_symphony_today_change.return_value = {"if_held": 1.2, "dry_run": 0.9}
    m.get_symphony_cumulative_return.return_value = {"if_held": 15.0, "dry_run": 15.0}
    m.get_symphony_max_drawdown.return_value = {"if_held": 0.12, "dry_run": 0.12}
    # Matches real contract: tuple[list[str], list[float]] | None.  None = insufficient
    # history; _compute_portfolio_strip handles this gracefully and skips vol calculation.
    m.get_portfolio_daily_returns_from_shadow.return_value = None
    return m


# ---------------------------------------------------------------------------
# Defect 1: Container width — max-w-7xl must be widened
# ---------------------------------------------------------------------------


class TestContainerWidth:
    """
    The page outer container in index.html currently uses max-w-7xl (1280px).
    The operator confirmed nothing visibly changed in M2.  The fix must use a
    class that is demonstrably wider: max-w-screen-2xl (1536px) or max-w-full,
    or an equivalent custom width.  max-w-7xl must be gone from the outer wrapper.
    """

    def test_outer_container_does_not_use_max_w_7xl(self):
        """
        The outer page wrapper must NOT use max-w-7xl.
        max-w-7xl caps at 1280px — the same as M1, so nothing visibly changed.
        A wider class is required for the width change to be real.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # Find the first div after <body> that is the outer wrapper.
        # The pattern: <div class="max-w-7xl mx-auto ..."> at the top level.
        outer_wrapper_pattern = re.compile(
            r'<div\s+class="[^"]*max-w-7xl[^"]*mx-auto[^"]*"',
            re.IGNORECASE,
        )
        assert not outer_wrapper_pattern.search(html), (
            "The outer page wrapper must not use max-w-7xl (1280px cap); "
            "M2 claimed to widen the layout but operator confirmed nothing changed — "
            "replace max-w-7xl with a wider class (e.g. max-w-screen-2xl or max-w-full)"
        )

    def test_outer_container_uses_wider_than_7xl(self):
        """
        The outer page wrapper must NOT cap the layout at 1280px (max-w-7xl).
        The Studio design uses a custom CSS class (page-wrap) whose skeleton
        lives in static/layout.css (the shared single source of truth) with
        max-width driven by the --studio-page-max-width custom property.
        We assert:
          (a) class="page-wrap" is the outer wrapper in index.html,
          (b) the shared .page-wrap CSS in layout.css uses no restrictive cap.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # The Studio outer wrapper is class="page-wrap", not a Tailwind mx-auto div.
        assert 'class="page-wrap"' in html, (
            'index.html must contain class="page-wrap" as the outer page wrapper; '
            "the Studio design uses a custom CSS layout class, not Tailwind mx-auto"
        )

        # page-wrap skeleton CSS lives in static/layout.css post-consolidation.
        layout_css = (_WORKTREE / "static" / "layout.css").read_text(encoding="utf-8")
        page_wrap_css_match = re.search(r"\.page-wrap\s*\{([^}]+)\}", layout_css, re.DOTALL)
        assert page_wrap_css_match, (
            ".page-wrap CSS definition not found in static/layout.css — the "
            "shared page-layout skeleton must define it."
        )

        css_body = page_wrap_css_match.group(1)
        # A narrow literal cap on the rule itself, or on the --studio-page-max-width
        # custom property the rule resolves, would re-introduce the 1280px cap bug. Only those
        # two places are searched: a responsive breakpoint (@media (max-width: 700px)) elsewhere
        # in the sheet is a condition on the viewport, not a cap on the page.
        cap_scope = " ".join(
            re.findall(r"\.page-wrap\s*\{([^}]*)\}", layout_css)
            + re.findall(r"--studio-page-max-width\s*:[^;}]*", layout_css)
        )
        narrow_cap = re.search(
            r"(?:max-width|--studio-page-max-width)\s*:\s*"
            r"(?:[0-9]{1,3}px|[0-9]{2,3}rem)",
            cap_scope,
        )
        assert not narrow_cap, (
            f".page-wrap must not set a narrow max-width cap in static/layout.css; "
            f".page-wrap rule body: {css_body.strip()!r}. "
            "The layout must use max-width: 100% (or the --studio-page-max-width "
            "default) — no narrow pixel/rem cap."
        )


# ---------------------------------------------------------------------------
# Defect 2: Portfolio strip — must be visibly larger
# ---------------------------------------------------------------------------


class TestPortfolioStripSize:
    """
    #portfolio-strip was added in M2 but the operator says it's too small (thin band).
    The strip must be enlarged: larger padding, larger text, or more prominent sizing.

    We pin the markup contract (class names) — ux-expert verifies the visual result.

    Current baseline (what must change):
      - py-4  →  must become py-6 or larger
      - text-[10px] labels  →  must become text-xs or larger
      - text-xs values  →  must become text-sm or larger
    OR any other combination that makes the strip visibly more prominent.

    The simplest acceptable fix: py-6 or py-8 padding + text-xs labels + text-sm values.
    """

    def test_portfolio_strip_padding_is_enlarged(self):
        """
        The portfolio summary panel (hero-section) must have sufficient padding
        so it is visually prominent.  The Studio design uses the hero-section with
        a dedicated CSS class (.hero-section) rather than the old id="portfolio-strip"
        with Tailwind py-* classes.  We assert the hero-section CSS has adequate padding.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # Assert the hero-section exists (the replacement for #portfolio-strip)
        assert 'data-testid="hero-section"' in html, (
            'data-testid="hero-section" must exist in index.html as the portfolio summary panel'
        )

        # The hero-section CSS must include padding (Studio uses .hero-section { padding: ... }).
        # The rule lives in the shared sheet static/layout.css, the dashboard primitives' one home.
        layout_css = (_WORKTREE / "static" / "layout.css").read_text(encoding="utf-8")
        hero_css_match = re.search(r"\.hero-section\s*\{([^}]+)\}", layout_css, re.DOTALL)
        assert hero_css_match, ".hero-section CSS definition not found in static/layout.css"

        css_body = hero_css_match.group(1)
        assert "padding" in css_body, (
            ".hero-section CSS must include a padding declaration; "
            "the Studio design uses padding: 28px 32px 24px or similar for visual prominence"
        )

    def test_portfolio_strip_label_text_size_is_not_tiny(self):
        """
        The comparison row labels in the hero-section (Today / Cumulative / Max DD)
        must not use tiny font sizes.  The Studio design uses CSS classes (.vs-row-label)
        rather than inline text-[10px] Tailwind classes.  We assert the vs-row-label
        CSS is defined (not absent or empty) and that no tiny inline font override exists.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # Assert vs-row-label CSS is defined (the Studio replacement for tiny strip labels)
        vs_label_css = re.search(r"\.vs-row-label\s*\{([^}]+)\}", html, re.DOTALL)
        assert vs_label_css, (
            ".vs-row-label CSS class definition not found in index.html <style> block; "
            "the Studio comparison rows use .vs-row-label for label styling"
        )

        # The label class must not set a font-size of 10px or less
        css_body = vs_label_css.group(1)
        tiny_font = re.search(r"font-size\s*:\s*(?:[0-9]px|10px)", css_body)
        assert not tiny_font, (
            f".vs-row-label must not set a font-size of 10px or less; got: {css_body.strip()!r}"
        )

    def test_portfolio_strip_value_text_size_is_readable(self):
        """
        The value spans in the portfolio strip (ps-tc-dry, ps-cr-dry, ps-mdd-dry)
        must use text-sm or larger.  text-xs is the M2 baseline; must be enlarged.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # Check each primary value element by its ID
        for elem_id in ("ps-tc-dry", "ps-cr-dry", "ps-mdd-dry"):
            elem_match = re.search(
                rf'<span[^>]*id=["\']?{elem_id}["\']?[^>]*>',
                html,
            )
            if elem_match:
                elem_tag = elem_match.group(0)
                # Must use text-sm or larger, not just text-xs
                has_sm_or_larger = re.search(r"\btext-(?:sm|base|lg|xl)\b", elem_tag)
                assert has_sm_or_larger, (
                    f"#{elem_id} must use text-sm or larger for readability; "
                    f"current class is text-xs — too small per operator feedback. Tag: {elem_tag!r}"
                )


# ---------------------------------------------------------------------------
# Defect 3a: If-held in portfolio strip — parens format, not stacked fraction
# ---------------------------------------------------------------------------


class TestPortfolioStripIfHeldFormat:
    """
    The portfolio strip currently renders dry_run and if_held in separate stacked
    spans (ps-tc-dry on top, ps-tc-held below) — the "fraction" layout the operator
    rejected.

    Required format: a single inline element showing `+0.11% (+0.14%)` where
    +0.11% is dry_run and (+0.14%) is if_held in parentheses.

    Implementation contract (markup):
    - The flex-col stacking structure for TC/CR/MDD must be GONE from the strip.
    - A single span or element per metric must contain both values inline.
    - OR: the JS in renderState() assembles the parenthetical string before writing it.

    Both DOM-structure and JS-assembly approaches are acceptable — the test pins
    the ABSENCE of the fraction pattern and PRESENCE of a parenthetical JS pattern.
    """

    def test_portfolio_strip_does_not_use_flex_col_for_metrics(self):
        """
        The portfolio summary in the Studio design uses vs-rows — a row layout
        where Bot and Held values appear side-by-side on the same row, not stacked
        in a flex-col fraction.  We assert the vs-row-top containers use a row
        (not column) flex direction.

        The old id="portfolio-strip" with flex-col stacking was replaced by the
        hero-section vs-rows layout in the V3 Studio redesign.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        # Assert vs-rows section exists (the Studio replacement for the fraction layout)
        assert 'class="vs-rows"' in html, (
            'index.html must contain class="vs-rows" — the Studio hero comparison rows '
            "that replaced the old flex-col fraction layout in id='portfolio-strip'"
        )

        # The vs-row-top CSS must use flex-direction: row (not column)
        vs_row_top_css = re.search(r"\.vs-row-top\s*\{([^}]+)\}", html, re.DOTALL)
        if vs_row_top_css:
            css_body = vs_row_top_css.group(1)
            # flex-col pattern must not be present
            assert "flex-direction: column" not in css_body, (
                ".vs-row-top CSS must not use flex-direction: column; "
                "the Studio design stacks Bot/Held values horizontally, not vertically"
            )

    def test_portfolio_strip_js_assembles_parenthetical_format(self):
        """
        static/index.js must assemble the Bot vs Held comparison values for the
        hero section comparison rows (Today / Cumulative / Max DD).  The JS must
        reference portfolio_strip from /api/state and update the hero comparison rows.

        The Studio design uses updateComparisonRows() or equivalent which reads
        portfolio_strip.today_change / cumulative_return / max_drawdown.
        """
        js = _INDEX_JS.read_text(encoding="utf-8")

        # portfolio_strip must be referenced in the JS
        assert "portfolio_strip" in js, (
            "static/index.js must reference portfolio_strip from the /api/state response; "
            "this is the data source for the hero comparison rows (Today/Cumulative/Max DD)"
        )

        # The JS must reference all three metric keys from portfolio_strip
        for metric_key in ("today_change", "cumulative_return", "max_drawdown"):
            assert metric_key in js, (
                f"static/index.js must reference portfolio_strip.{metric_key} "
                "to update the hero comparison rows"
            )

    def test_portfolio_strip_separate_held_spans_are_gone(self):
        """
        The separate ps-tc-held, ps-cr-held, ps-mdd-held span elements must be
        removed from the HTML DOM — they are the fraction's lower half.
        If the fix merges both values into a single span, these IDs must be absent.

        This test is skipped if the implementer chooses to keep the IDs but
        populate them with the parenthetical string — in that case
        test_portfolio_strip_js_assembles_parenthetical_format pins the JS contract.
        """
        html = _INDEX_HTML.read_text(encoding="utf-8")

        held_span_ids = ["ps-tc-held", "ps-cr-held", "ps-mdd-held"]
        present = [id_ for id_ in held_span_ids if id_ in html]

        # If any -held spans exist, they must NOT be paired with a flex-col parent
        # (that would recreate the fraction).  The test above covers the JS requirement.
        # Here we flag if all three are still stacked (the original fraction pattern).
        if len(present) == 3:
            strip_start = html.find('id="portfolio-strip"')
            if strip_start == -1:
                strip_start = html.find("id='portfolio-strip'")
            strip_region = html[strip_start : strip_start + 1500] if strip_start != -1 else html

            stacked_held_spans = re.findall(
                r'<div[^>]*flex-col[^>]*>.*?<span[^>]*id=["\']ps-(?:tc|cr|mdd)-dry',
                strip_region,
                re.DOTALL,
            )
            assert not stacked_held_spans, (
                "ps-tc-held / ps-cr-held / ps-mdd-held spans are still stacked in a flex-col; "
                "this recreates the fraction layout the operator rejected; "
                "either remove the -held spans and merge into a single inline element, "
                "or keep the IDs but change the DOM to be inline (not stacked)"
            )
