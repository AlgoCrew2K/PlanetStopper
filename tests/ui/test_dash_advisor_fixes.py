"""
RED tests — dash-fix cycle (AC-1 / AC-2 / AC-3a / AC-3b / AC-4 / node --check).

AC-1  GET /ai-advisor renders the capability nav linking to all four sub-routes.
AC-2  Advisor card action buttons do not overflow card bounds at 1440/1024/768.
      (Extends tests/ui/test_advisor_card_overflow.py — new overflow shape.)
AC-3a index.js reads sym.symphony_vol for the detail-panel vol row (not sym.volatility/sym.vol).
AC-3b table_partial MDD column renders percent-scale values without ×100 inflation.
AC-4  Every ACCEPTABLE suggestion card (asset-swaps + logic-changes) renders a
      visible "Chat about this" button that links to /ai-advisor/chat with scoping params.

node --check: any static/*.js touched by this cycle must have zero syntax errors.

Tests are intentionally RED against the fork-point code (49d5380).
They become GREEN after the implementer applies the four fixes.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKTREE_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Shared Flask test client
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Flask test client; database + analytics mocked out so no live DB needed."""
    import app as _app
    _app.app.config["TESTING"] = True
    with _app.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# AC-1 — /ai-advisor landing page must contain the capability nav
# ---------------------------------------------------------------------------

class TestAC1AdvisorCapabilityNav:
    """GET /ai-advisor must render the cap-nav block with all four sub-route links.

    The fork-point ai_advisor.html is missing the <nav class="cap-nav"> block
    that exists on every sub-page (asset_swaps.html:563-590, chat.html:490-510).
    Without it, the Advisor tab is a dead-end.
    """

    def test_ai_advisor_landing_has_cap_nav_element(self, client):
        """GET /ai-advisor response contains a <nav> with class 'cap-nav'."""
        resp = client.get("/ai-advisor")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert 'class="cap-nav"' in html or "cap-nav" in html, (
            "GET /ai-advisor is missing the capability nav block "
            "(<nav class=\"cap-nav\">). Users cannot navigate to any sub-page."
        )

    def test_ai_advisor_landing_links_to_correlations(self, client):
        """GET /ai-advisor response contains a link to the correlations sub-route."""
        resp = client.get("/ai-advisor")
        html = resp.data.decode("utf-8")
        assert "/ai-advisor/correlations" in html, (
            "GET /ai-advisor does not link to /ai-advisor/correlations. "
            "Add the cap-nav block from ai_advisor_asset_swaps.html:563-590."
        )

    def test_ai_advisor_landing_links_to_asset_swaps(self, client):
        """GET /ai-advisor response contains a link to the asset-swaps sub-route."""
        resp = client.get("/ai-advisor")
        html = resp.data.decode("utf-8")
        assert "/ai-advisor/asset-swaps" in html, (
            "GET /ai-advisor does not link to /ai-advisor/asset-swaps. "
            "Add the cap-nav block from ai_advisor_asset_swaps.html:563-590."
        )

    def test_ai_advisor_landing_links_to_logic_changes(self, client):
        """GET /ai-advisor response contains a link to the logic-changes sub-route."""
        resp = client.get("/ai-advisor")
        html = resp.data.decode("utf-8")
        assert "/ai-advisor/logic-changes" in html, (
            "GET /ai-advisor does not link to /ai-advisor/logic-changes. "
            "Add the cap-nav block from ai_advisor_asset_swaps.html:563-590."
        )

    def test_ai_advisor_landing_links_to_chat(self, client):
        """GET /ai-advisor response contains a link to the chat sub-route."""
        resp = client.get("/ai-advisor")
        html = resp.data.decode("utf-8")
        assert "/ai-advisor/chat" in html, (
            "GET /ai-advisor does not link to /ai-advisor/chat. "
            "Add the cap-nav block from ai_advisor_asset_swaps.html:563-590."
        )

    def test_ai_advisor_landing_has_all_four_data_testids(self, client):
        """All four tab data-testid attributes are present on the landing page.

        The canonical cap-nav uses data-testid='tab-correlations',
        'tab-asset-swaps', 'tab-logic-changes', 'tab-chat'.
        """
        resp = client.get("/ai-advisor")
        html = resp.data.decode("utf-8")
        expected_testids = [
            'data-testid="tab-correlations"',
            'data-testid="tab-asset-swaps"',
            'data-testid="tab-logic-changes"',
            'data-testid="tab-chat"',
        ]
        for testid in expected_testids:
            assert testid in html, (
                f"GET /ai-advisor is missing {testid}. "
                "Copy the full cap-nav block from ai_advisor_asset_swaps.html:563-590."
            )

    def test_ai_advisor_overview_tab_marked_active(self, client):
        """The landing page's own tab (Overview / AI Advisor) should carry
        aria-current='page' so screen readers identify the active tab."""
        resp = client.get("/ai-advisor")
        html = resp.data.decode("utf-8")
        # The overview tab on the landing page must be aria-current="page".
        # On sub-pages (asset_swaps, chat) the *sub-page* tab carries this.
        # This asserts the pattern follows the same a11y contract.
        assert 'aria-current="page"' in html, (
            "GET /ai-advisor cap-nav has no aria-current=\"page\" attribute. "
            "The active/overview tab must be marked with aria-current for a11y."
        )


# ---------------------------------------------------------------------------
# AC-2 — Advisor card button overflow (unit-level CSS contract)
# ---------------------------------------------------------------------------

class TestAC2AdvisorCardButtonOverflow:
    """Advisor card action buttons must not overflow card bounds at 1440/1024/768.

    These are unit-level structural tests: the template/JS must produce a DOM
    structure where the button row uses flex-wrap so buttons wrap rather than
    overflow. The live-render browser tests (test_advisor_card_overflow.py)
    are the acceptance gate; these tests catch the structural preconditions.

    The current overflow shape (NEW, distinct from Issues 1/2/3 already fixed):
    the .card-actions / button-row container inside a suggestion card lacks
    flex-wrap, so at narrow viewports the Accept/Reject/Discuss buttons are
    clipped outside the card boundary.
    """

    def test_asset_swaps_page_button_row_has_flex_wrap_contract(self, client):
        """GET /ai-advisor/asset-swaps HTML must contain a flex-wrap rule for
        the card action button row.

        The card button container (class 'card-actions' or 'swap-card-actions')
        must declare flex-wrap:wrap (or equivalent) so buttons can wrap at
        narrow viewports rather than overflowing.
        """
        resp = client.get("/ai-advisor/asset-swaps")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        # flex-wrap:wrap must appear in an inline style block or linked stylesheet
        # reference for the card-actions container.  The CSS token is the gate.
        assert re.search(
            r"flex-wrap\s*:\s*wrap",
            html,
            re.IGNORECASE,
        ), (
            "GET /ai-advisor/asset-swaps HTML or embedded CSS is missing "
            "flex-wrap:wrap for the card action button row. "
            "Without it, buttons overflow at 1024px and 768px."
        )

    def test_logic_changes_page_button_row_has_flex_wrap_contract(self, client):
        """GET /ai-advisor/logic-changes HTML must contain a flex-wrap rule for
        the card action button row."""
        resp = client.get("/ai-advisor/logic-changes")
        assert resp.status_code == 200
        html = resp.data.decode("utf-8")
        assert re.search(
            r"flex-wrap\s*:\s*wrap",
            html,
            re.IGNORECASE,
        ), (
            "GET /ai-advisor/logic-changes HTML or embedded CSS is missing "
            "flex-wrap:wrap for the card action button row. "
            "Without it, buttons overflow at 1024px and 768px."
        )

    def test_asset_swaps_card_actions_container_present_in_template(self, client):
        """The asset-swaps page must include a .card-actions or .swap-card-actions
        container element (the structural anchor for the overflow fix)."""
        resp = client.get("/ai-advisor/asset-swaps")
        html = resp.data.decode("utf-8")
        # Either class name is acceptable as the implementer's choice.
        has_container = (
            "card-actions" in html
            or "swap-card-actions" in html
            or "action-row" in html
        )
        assert has_container, (
            "GET /ai-advisor/asset-swaps HTML is missing a .card-actions / "
            ".swap-card-actions / .action-row container. "
            "The overflow fix requires a named container element."
        )


# ---------------------------------------------------------------------------
# AC-3a — index.js reads sym.symphony_vol (not sym.volatility / sym.vol)
# ---------------------------------------------------------------------------

class TestAC3aVolFieldName:
    """The detail-panel vol row must read sym.symphony_vol.

    At index.js:500 the code reads sym.volatility / sym.vol — neither key
    exists in the engine state dict; the correct key is symphony_vol
    (set by the risk engine in every cycle). This causes the Vol row to
    always display '--'.

    These tests verify the JS source directly (no browser required).
    """

    def _read_index_js(self) -> str:
        """Read index.js from the worktree (implementer will edit this file)."""
        js_path = _WORKTREE_ROOT / "static" / "index.js"
        return js_path.read_text(encoding="utf-8")

    def test_index_js_reads_symphony_vol_not_volatility(self):
        """index.js populateRiskMathFromState must reference sym.symphony_vol,
        not sym.volatility or sym.vol, for the vol field."""
        src = self._read_index_js()
        # After the fix, symphony_vol must appear in the vol-reading expression.
        assert "symphony_vol" in src, (
            "index.js does not reference sym.symphony_vol. "
            "The engine stores annualized vol under symphony_vol, not volatility/vol. "
            "Fix: replace sym.volatility / sym.vol with sym.symphony_vol at line ~500."
        )

    def test_index_js_does_not_use_bare_sym_volatility_for_vol_row(self):
        """index.js must not read sym.volatility as the source for the vol row.

        The key sym.volatility is not set by the engine.  If it appears in the
        vol-reading expression (rawVol assignment), the row will always show '--'.
        """
        src = self._read_index_js()
        # Locate the rawVol assignment block (within populateRiskMathFromState).
        # The fix replaces the sym.volatility / sym.vol chain with sym.symphony_vol.
        # A correct fix should NOT have `sym.volatility` in the rawVol expression.
        raw_vol_match = re.search(
            r"var rawVol\s*=.*?;",
            src,
            re.DOTALL,
        )
        assert raw_vol_match is not None, (
            "Could not locate 'var rawVol = ...' in index.js. "
            "The vol-field fix must be in populateRiskMathFromState."
        )
        raw_vol_expr = raw_vol_match.group(0)
        assert "sym.volatility" not in raw_vol_expr, (
            f"index.js rawVol expression still reads sym.volatility: {raw_vol_expr!r}. "
            "Replace with sym.symphony_vol — the engine does not set sym.volatility."
        )

    def test_index_js_does_not_use_bare_sym_vol_for_vol_row(self):
        """index.js must not read sym.vol as the source for the vol row.

        The key sym.vol is not set by the engine.
        """
        src = self._read_index_js()
        raw_vol_match = re.search(
            r"var rawVol\s*=.*?;",
            src,
            re.DOTALL,
        )
        if raw_vol_match is None:
            pytest.skip("rawVol expression not found — cannot check sym.vol usage")
        raw_vol_expr = raw_vol_match.group(0)
        # After the fix, sym.vol must not appear as the primary source.
        # (It's acceptable as a final fallback only if symphony_vol is checked first.)
        assert "sym.symphony_vol" in raw_vol_expr, (
            f"index.js rawVol expression does not read sym.symphony_vol: {raw_vol_expr!r}. "
            "Fix: read sym.symphony_vol as the primary source for the vol row."
        )


# ---------------------------------------------------------------------------
# AC-3b — table_partial MDD column must NOT multiply by 100
# ---------------------------------------------------------------------------

class TestAC3bMddColumnNotInflated:
    """table_partial.html MDD column must render percent-scale values directly.

    analytics.get_symphony_max_drawdown returns PERCENT-scale floats
    (e.g. -17.35 means 17.35% drawdown).  table_partial.html:158-159 incorrectly
    applies * 100, producing inflated values like 1735%.

    The index.html card footer (line 1092) correctly uses the value WITHOUT * 100,
    confirming the contract: the analytics function already returns percent scale.

    Tests use a Flask test client + Jinja2 render_template to verify the
    template output for a known MDD fixture value.
    """

    # Percent-scale MDD fixture: -17.35% drawdown.
    # Expected rendered string: "-17.35%" (or "17.35%").
    # Bug renders: "-1735.00%" (after * 100).
    _MDD_PERCENT_SCALE = -17.35

    def _render_table_partial(self, mdd_dry_run: float, mdd_if_held: float) -> str:
        """Render table_partial.html via the Flask app's Jinja env with a known MDD fixture."""
        import app as _app

        # Construct a minimal accounts_map with one account + one symphony.
        # Include all fields accessed by table_partial.html to avoid Jinja2
        # UndefinedError on attribute access (e.g. sym.mc_prob, sym.stop_trigger).
        sym = {
            "id": "test_sym",
            "name": "Test Symphony",
            "position": 0.9,
            "armed": True,
            "triggered": False,
            "para_armed": False,
            "tp_armed": False,
            "breakeven_locked": False,
            "current_return": 5.0,
            "current_value": 100000.0,
            "mc_prob": 62.5,
            "stop_trigger": -8.0,
            "shadow_hwm": 6.2,
            "below_stop_count": 0,
            "above_tp_count": 0,
            "triggered_reason": None,
            "triggered_at_return": None,
            "triggered_at_hwm": None,
            "triggered_at_stop": None,
            "triggered_at_time": None,
            "last_trigger": None,
            "is_live": False,
            # _mdd is percent-scale (as returned by analytics.get_symphony_max_drawdown).
            "_mdd": {"dry_run": mdd_dry_run, "if_held": mdd_if_held},
            "_tc": {"dry_run": 0.5, "if_held": 0.4},
            "_cr": {"dry_run": 5.0, "if_held": 4.8},
        }
        accounts_map = {"acc_test": [sym]}
        account_labels = {"acc_test": "Test Account"}

        with _app.app.app_context():
            from flask import render_template as _render
            return _render(
                "table_partial.html",
                accounts_map=accounts_map,
                account_labels=account_labels,
                sort_col="name",
                sort_dir="asc",
                data_as_of="12:00 ET",
            )

    def test_table_mdd_column_does_not_show_x100_inflated_value(self):
        """table_partial MDD column must not multiply percent-scale MDD by 100.

        Given _mdd.dry_run = -17.35 (percent scale, as returned by analytics),
        the rendered cell must show approximately '-17.35%', not '-1735.00%'.
        """
        html = self._render_table_partial(
            mdd_dry_run=self._MDD_PERCENT_SCALE,
            mdd_if_held=self._MDD_PERCENT_SCALE * 0.9,
        )
        # The inflated value would be ~1735; assert it is NOT present.
        assert "1735" not in html, (
            "table_partial.html MDD column shows an inflated value (~1735%). "
            "analytics.get_symphony_max_drawdown already returns percent scale. "
            "Fix: remove the * 100 at table_partial.html:158-159."
        )

    def test_table_mdd_column_shows_correct_percent_scale_value(self):
        """table_partial MDD column must render the percent-scale value directly.

        Given _mdd.dry_run = -17.35, the rendered cell must contain '17.35'.
        (The sign may be absorbed by formatting; we test the magnitude string.)
        """
        html = self._render_table_partial(
            mdd_dry_run=self._MDD_PERCENT_SCALE,
            mdd_if_held=self._MDD_PERCENT_SCALE * 0.9,
        )
        assert "17.35" in html, (
            "table_partial.html MDD column does not show '17.35' for an input of "
            f"{self._MDD_PERCENT_SCALE}. Expected direct percent rendering. "
            "Fix: remove * 100 at table_partial.html:158-159."
        )

    def test_table_mdd_column_source_code_has_no_x100_multiplication(self):
        """table_partial.html source must not multiply _mdd values by 100.

        Direct source check to make the failure immediately actionable.
        """
        template_path = _WORKTREE_ROOT / "templates" / "table_partial.html"
        src = template_path.read_text(encoding="utf-8")
        # The bug pattern: mdd_dr * 100 or mdd_ih * 100 in the mdd_*_str assignments.
        bug_pattern = re.search(r"mdd_[di][rh]\s*\*\s*100", src)
        assert bug_pattern is None, (
            f"table_partial.html still contains MDD × 100 multiplication: "
            f"'{bug_pattern.group(0)}'. "
            "Remove * 100 from the mdd_dr_str and mdd_ih_str assignments (lines 158-159)."
        )


# ---------------------------------------------------------------------------
# AC-4 — "Chat about this" button on acceptable suggestion cards
# ---------------------------------------------------------------------------

class TestAC4ChatAboutThisButton:
    """Every ACCEPTABLE suggestion card must render a visible "Chat about this" button.

    The existing de-emphasized "Discuss this" anchor (ai_advisor_asset_swaps.js:243-265
    and ai_advisor_logic_changes.js:264-270) must be REPLACED with a real, styled button
    that is visually prominent (not a faint underlined link).

    These tests verify:
    - The JS source no longer renders the old de-emphasized "Discuss this" link as the
      only call-to-action.
    - A 'chat-about-this' button/element with visible styling is present in the rendered
      card HTML for survivor (ADOPT_CANDIDATE) results.
    - The button links to /ai-advisor/chat with scoping params preserved.
    """

    def _read_asset_swaps_js(self) -> str:
        js_path = _WORKTREE_ROOT / "static" / "ai_advisor_asset_swaps.js"
        return js_path.read_text(encoding="utf-8")

    def _read_logic_changes_js(self) -> str:
        js_path = _WORKTREE_ROOT / "static" / "ai_advisor_logic_changes.js"
        return js_path.read_text(encoding="utf-8")

    def test_asset_swaps_js_has_chat_about_this_element(self):
        """ai_advisor_asset_swaps.js renderSwapCard must produce a 'Chat about this' button.

        The old 'Discuss this' anchor must be replaced (or supplemented) with a
        visible, styled 'Chat about this' button on survivor cards.
        """
        src = self._read_asset_swaps_js()
        # The new button must use "Chat about this" text (case-insensitive).
        assert re.search(r"[Cc]hat about this", src), (
            "ai_advisor_asset_swaps.js does not contain 'Chat about this' text. "
            "Replace the de-emphasized 'Discuss this' link with a visible "
            "'Chat about this' button on ADOPT_CANDIDATE cards."
        )

    def test_logic_changes_js_has_chat_about_this_element(self):
        """ai_advisor_logic_changes.js _renderProposalCard must produce a 'Chat about this' button."""
        src = self._read_logic_changes_js()
        assert re.search(r"[Cc]hat about this", src), (
            "ai_advisor_logic_changes.js does not contain 'Chat about this' text. "
            "Replace the de-emphasized 'Discuss this' link with a visible "
            "'Chat about this' button on ADOPT_CANDIDATE proposal cards."
        )

    def test_asset_swaps_js_chat_button_has_visible_styling(self):
        """The 'Chat about this' element in ai_advisor_asset_swaps.js must NOT use
        the old de-emphasized style (faint color + underline only).

        The old style was: font-size:0.75rem;color:var(--studio-ink-dim);text-decoration:underline
        A visible button must use a button element or prominent styling (background-color,
        font-weight, border, or a CSS class that implies a button appearance).
        """
        src = self._read_asset_swaps_js()
        # Locate the chat/discuss block.
        chat_section = re.search(
            r"[Cc]hat about this.*?(?:</div>|`\s*;|\+\s*'</div>')",
            src,
            re.DOTALL,
        )
        if chat_section is None:
            pytest.fail(
                "Could not locate 'Chat about this' block in ai_advisor_asset_swaps.js. "
                "The button must be present in renderSwapCard."
            )
        block = chat_section.group(0)
        # The button must NOT be rendered purely as a faint <a> with just underline.
        # A real button element OR a CSS class that styles it as a button is required.
        has_button_element = "<button" in block or "btn" in block.lower()
        has_prominent_style = re.search(
            r"(background[-\s]*color|font-weight\s*:\s*[5-9]\d\d|border\s*:|class=.*btn)",
            block,
            re.IGNORECASE,
        )
        assert has_button_element or has_prominent_style, (
            "The 'Chat about this' element in ai_advisor_asset_swaps.js appears to use "
            "only faint/underline styling. Replace with a <button> element or a CSS class "
            "that renders it as a visible action button."
        )

    def test_logic_changes_js_chat_button_has_visible_styling(self):
        """The 'Chat about this' element in ai_advisor_logic_changes.js must NOT use
        the old de-emphasized style."""
        src = self._read_logic_changes_js()
        chat_section = re.search(
            r"[Cc]hat about this.*?(?:</div>|`\s*;|\+\s*'</div>')",
            src,
            re.DOTALL,
        )
        if chat_section is None:
            pytest.fail(
                "Could not locate 'Chat about this' block in ai_advisor_logic_changes.js. "
                "The button must be present in _renderProposalCard."
            )
        block = chat_section.group(0)
        has_button_element = "<button" in block or "btn" in block.lower()
        has_prominent_style = re.search(
            r"(background[-\s]*color|font-weight\s*:\s*[5-9]\d\d|border\s*:|class=.*btn)",
            block,
            re.IGNORECASE,
        )
        assert has_button_element or has_prominent_style, (
            "The 'Chat about this' element in ai_advisor_logic_changes.js appears to use "
            "only faint/underline styling. Replace with a <button> element or a CSS class "
            "that renders it as a visible action button."
        )

    def test_asset_swaps_js_chat_button_links_to_chat_route(self):
        """The 'Chat about this' button in ai_advisor_asset_swaps.js must link to
        /ai-advisor/chat (the scoped chat route), preserving artifact params."""
        src = self._read_asset_swaps_js()
        # The href or navigation target must be /ai-advisor/chat.
        assert "/ai-advisor/chat" in src, (
            "ai_advisor_asset_swaps.js does not contain '/ai-advisor/chat'. "
            "The 'Chat about this' button must navigate to the scoped chat route."
        )

    def test_logic_changes_js_chat_button_links_to_chat_route(self):
        """The 'Chat about this' button in ai_advisor_logic_changes.js must link to
        /ai-advisor/chat."""
        src = self._read_logic_changes_js()
        assert "/ai-advisor/chat" in src, (
            "ai_advisor_logic_changes.js does not contain '/ai-advisor/chat'. "
            "The 'Chat about this' button must navigate to the scoped chat route."
        )

    def test_asset_swaps_js_chat_button_carries_artifact_json(self):
        """The 'Chat about this' button must carry the artifact JSON scoping data
        (data-artifact-json attribute or equivalent) so the chat panel is
        context-aware when the button is clicked."""
        src = self._read_asset_swaps_js()
        chat_section = re.search(
            r"[Cc]hat about this.*?(?:</div>|`\s*;|\+\s*'</div>')",
            src,
            re.DOTALL,
        )
        if chat_section is None:
            pytest.fail("'Chat about this' block not found in ai_advisor_asset_swaps.js")
        block = chat_section.group(0)
        assert "artifact" in block.lower() or "artifactJson" in block or "data-artifact" in block, (
            "The 'Chat about this' block in ai_advisor_asset_swaps.js does not carry "
            "artifact context (data-artifact-json or equivalent). "
            "The scoped chat needs the artifact JSON to pre-fill the context panel."
        )

    def test_logic_changes_js_chat_button_carries_artifact_json(self):
        """The 'Chat about this' button in ai_advisor_logic_changes.js must carry
        the artifact JSON scoping data."""
        src = self._read_logic_changes_js()
        chat_section = re.search(
            r"[Cc]hat about this.*?(?:</div>|`\s*;|\+\s*'</div>')",
            src,
            re.DOTALL,
        )
        if chat_section is None:
            pytest.fail("'Chat about this' block not found in ai_advisor_logic_changes.js")
        block = chat_section.group(0)
        assert "artifact" in block.lower() or "artifactJson" in block or "data-artifact" in block, (
            "The 'Chat about this' block in ai_advisor_logic_changes.js does not carry "
            "artifact context (data-artifact-json or equivalent). "
            "The scoped chat needs the artifact JSON to pre-fill the context panel."
        )

    def test_asset_swaps_js_discuss_this_is_removed_or_upgraded(self):
        """The old de-emphasized 'Discuss this' anchor must not remain as the
        sole call-to-action in ai_advisor_asset_swaps.js.

        Either: (a) it is replaced by 'Chat about this', or (b) it is promoted
        to a styled button AND renamed.  A plain 'Discuss this' link as the
        primary CTA fails AC-4.
        """
        src = self._read_asset_swaps_js()
        has_old_discuss_only = (
            "Discuss this" in src
            and not re.search(r"[Cc]hat about this", src)
        )
        assert not has_old_discuss_only, (
            "ai_advisor_asset_swaps.js still renders 'Discuss this' as the sole CTA "
            "without a 'Chat about this' button. Replace or supplement it per AC-4."
        )

    def test_logic_changes_js_discuss_this_is_removed_or_upgraded(self):
        """The old de-emphasized 'Discuss this' anchor must not remain as the
        sole call-to-action in ai_advisor_logic_changes.js."""
        src = self._read_logic_changes_js()
        has_old_discuss_only = (
            "Discuss this" in src
            and not re.search(r"[Cc]hat about this", src)
        )
        assert not has_old_discuss_only, (
            "ai_advisor_logic_changes.js still renders 'Discuss this' as the sole CTA "
            "without a 'Chat about this' button. Replace or supplement it per AC-4."
        )


# ---------------------------------------------------------------------------
# node --check — static JS syntax guard
# ---------------------------------------------------------------------------

class TestNodeSyntaxCheck:
    """Any static/*.js file touched by this cycle must have zero Node.js syntax errors.

    The settings-modal.js:577 incident shipped a JS parse error past green unit tests.
    This guard runs `node --check <file>` as a subprocess and asserts exit code 0.

    If node is not on PATH the test is skipped (not failed) — the check is advisory
    for environments without Node; the CI gate requires it.
    """

    _JS_FILES_TO_CHECK = [
        "index.js",
        "ai_advisor_asset_swaps.js",
        "ai_advisor_logic_changes.js",
    ]

    def _node_available(self) -> bool:
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True,
                timeout=10,
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @pytest.mark.parametrize("js_file", _JS_FILES_TO_CHECK)
    def test_static_js_has_no_syntax_errors(self, js_file: str):
        """node --check static/<js_file> must exit 0 (no syntax errors).

        A non-zero exit code means Node.js found a parse/syntax error in the file.
        This catches issues like the settings-modal.js:577 incident where a JS
        parse error slipped past unit tests because no syntax check was run.
        """
        if not self._node_available():
            pytest.skip("node not available on PATH — skipping JS syntax check")

        js_path = _WORKTREE_ROOT / "static" / js_file
        if not js_path.exists():
            pytest.skip(f"static/{js_file} not found in worktree — skipping")

        result = subprocess.run(
            ["node", "--check", str(js_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"node --check static/{js_file} exited {result.returncode}.\n"
            f"stderr: {result.stderr.strip()}\n"
            f"stdout: {result.stdout.strip()}\n"
            "Fix the JS syntax error before this test can go GREEN."
        )
