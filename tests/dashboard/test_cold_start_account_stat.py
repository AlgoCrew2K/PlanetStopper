"""
RED — dash-polish TASK 1: cold-start account-all-time-cr JS render.

THE BUG (PM live gate, post e863c9e):
  `account-all-time-cr` is SSR-only: templates/index.html:808-814 renders the span
  only when `_portfolio.get('account_all_time_cr')` is not None.  static/index.js
  never references the element — it is not read, created, or updated on poll.

  Cold-start scenario: daemon restarted, account cache not yet warm when the first
  /api/state poll fires.  `meta.portfolio.account_all_time_cr` arrives as a real
  value in the payload BUT the SSR omitted the DOM element because the cache was
  cold at render time.  The JS poll fires, sees the value, and does nothing — the
  stat is absent until the user manually reloads the page.

  The fix: a dedicated renderer (NOT renderGuardAlpha, NOT updateComparisonRows)
  must create the `.account-all-time-stat` container + `[data-testid="account-all-time-cr"]`
  span if they are absent, and update the textContent if they are present.  It reads
  `meta.portfolio.account_all_time_cr` (authoritative) with a
  `portfolio_strip.account_all_time_cr` fallback.

ACCEPTANCE CRITERIA (all must hold after the fix):

  AC-CS.1  static/index.js contains a dedicated function (e.g. updateAccountAllTime)
           that targets `[data-testid="account-all-time-cr"]`.

  AC-CS.2  The dedicated function reads account_all_time_cr from
           `meta.portfolio.account_all_time_cr` (primary path).

  AC-CS.3  The function creates the element when the DOM node is absent
           (cold-start path: SSR omitted the block).

  AC-CS.4  The dedicated renderer is wired into updateDashboard (called on each poll).

  AC-CS.5  renderGuardAlpha does NOT reference account-all-time-cr
           (no coupling between windowed-guard renderer and the fixed stat).

  AC-CS.6  updateComparisonRows does NOT write to account-all-time-cr
           (same — the windowed strip path must not clobber the fixed stat).

  AC-CS.7  static/index.js passes `node --check` (no syntax errors).

  NON-REGRESSION: guard-alpha-headline reads `ps.guard_alpha` directly (no regression
  of the render-basis fix), headline reads `ps.guard_alpha` not derived from cr-crHeld.

Harness note: the project has no jsdom.  The established pattern
(feedback_visual_gate_catches_js_break) is source-text assertions against
static/index.js + `node --check`.  The ux-expert visual gate (real browser on :8095
cold-start scenario) is the final acceptance check; these tests catch wiring contracts.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

_JS_PATH = pathlib.Path(__file__).parent.parent.parent / "static" / "index.js"
_HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"


def _js() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Parse gate — broken JS means every renderer silently never runs
# ---------------------------------------------------------------------------


def test_node_check_passes():
    """
    AC-CS.7: static/index.js must pass `node --check` with exit code 0.

    Any syntax error prevents the entire file from loading in the browser.
    Pinned here so a broken cold-start fix is caught before the ux-expert gate.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available in this environment")
    result = subprocess.run(
        [node, "--check", str(_JS_PATH)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        "AC-CS.7 FAIL: static/index.js has a parse error. "
        "The account-all-time cold-start fix broke the JS syntax.\n"
        f"node --check stderr:\n{result.stderr.strip()}"
    )


# ---------------------------------------------------------------------------
# AC-CS.1 — dedicated renderer exists and targets the element
# ---------------------------------------------------------------------------


class TestDedicatedAccountRendererExists:
    """
    A dedicated renderer (separate from renderGuardAlpha and updateComparisonRows)
    must target [data-testid="account-all-time-cr"].

    The separation is mandatory: renderGuardAlpha carries the WINDOWED guard-alpha
    headline and must not be polluted with the fixed all-time account stat.
    """

    def test_dedicated_function_targets_account_all_time_cr(self):
        """
        AC-CS.1: static/index.js must contain a function that references
        'account-all-time-cr' (the testid for the account stat element).

        The existing code has NO reference to this testid anywhere in index.js.
        This test is RED on current code.
        """
        js = _js()
        assert "account-all-time-cr" in js, (
            "AC-CS.1 FAIL: static/index.js has no reference to 'account-all-time-cr'. "
            "A dedicated renderer must target this element so the account stat is "
            "rendered/updated even when the SSR omitted the block (cold-start path). "
            "Current code has no JS reference to this testid."
        )

    def test_dedicated_function_is_not_render_guard_alpha(self):
        """
        AC-CS.1 + AC-CS.5: the reference to account-all-time-cr must NOT be inside
        renderGuardAlpha — it must be in a separate dedicated function.

        renderGuardAlpha is the windowed guard-alpha painter; coupling it to the
        fixed account stat is incorrect because the account stat must NOT window.
        """
        js = _js()

        # Locate renderGuardAlpha body (up to the next top-level function).
        start = js.find("function renderGuardAlpha")
        if start == -1:
            pytest.fail("renderGuardAlpha function not found in static/index.js")
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 800]

        assert "account-all-time-cr" not in body and "account_all_time_cr" not in body, (
            "AC-CS.1/AC-CS.5 FAIL: the account-all-time-cr reference is inside "
            "renderGuardAlpha. The account stat must be handled by a SEPARATE dedicated "
            "function so it does not get windowed when the picker changes."
        )

    def test_dedicated_function_is_not_update_comparison_rows(self):
        """
        AC-CS.6: updateComparisonRows must not write to account-all-time-cr.

        updateComparisonRows operates on windowed strip data; injecting account stat
        writes there would clobber the fixed all-time value on every picker click.
        """
        js = _js()

        start = js.find("function updateComparisonRows")
        if start == -1:
            pytest.fail("updateComparisonRows function not found in static/index.js")
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 3000]

        # A querySelector call AND a textContent write together = a DOM write.
        has_testid = "account-all-time-cr" in body
        has_write = "textContent" in body
        assert not (has_testid and has_write), (
            "AC-CS.6 FAIL: updateComparisonRows contains both 'account-all-time-cr' "
            "and 'textContent'. This would overwrite the all-time account stat with "
            "a windowed value on every picker click."
        )


# ---------------------------------------------------------------------------
# AC-CS.2 — reads meta.portfolio.account_all_time_cr
# ---------------------------------------------------------------------------


class TestDedicatedRendererReadsApiStateField:
    """
    The dedicated renderer must read account_all_time_cr from
    meta.portfolio.account_all_time_cr (the authoritative source in /api/state).

    A renderer that reads only from the DOM (e.g., reads the current textContent)
    instead of the fresh API payload would not update on cold-start.
    """

    def test_js_references_account_all_time_cr_field_from_meta(self):
        """
        AC-CS.2: index.js must reference `account_all_time_cr` as a field name
        (reading it from the API payload), not just as a CSS selector string.

        The testid string 'account-all-time-cr' (with dashes) exists in AC-CS.1.
        This test additionally requires the underscore variant (as used in the
        Python payload key `meta.portfolio.account_all_time_cr`) to appear, confirming
        the renderer reads from the data object, not just queries the DOM.

        Current code: no reference to 'account_all_time_cr' in index.js at all.
        """
        js = _js()
        assert "account_all_time_cr" in js, (
            "AC-CS.2 FAIL: static/index.js does not reference 'account_all_time_cr' "
            "(the underscore-form payload key). The dedicated renderer must read this "
            "field from `meta.portfolio.account_all_time_cr` (the /api/state payload "
            "shape). Absent this, the renderer cannot consume the API value on cold-start."
        )


# ---------------------------------------------------------------------------
# AC-CS.3 — creates the element when absent (cold-start path)
# ---------------------------------------------------------------------------


class TestDedicatedRendererCreatesMissingElement:
    """
    The cold-start failure happens because the SSR omitted the account-all-time-stat
    container + testid span when the cache was cold at render time.

    The dedicated renderer must create (or insert) the element when it is absent,
    not silently skip when querySelector returns null.

    Source-text contract: the renderer must contain a DOM creation pattern
    (createElement or innerHTML assignment to build the node) OR an explicit
    insertion pattern (appendChild / insertAdjacentHTML), rather than just
    a querySelector + textContent guard that silently returns when null.
    """

    def test_js_contains_create_element_or_equivalent_for_account_stat(self):
        """
        AC-CS.3: static/index.js must contain a DOM-creation pattern for the
        account-all-time stat element so cold-start (SSR omitted the block) is handled.

        Acceptable patterns (any one sufficient):
          - createElement (with 'account' nearby)
          - insertAdjacentHTML (with 'account' nearby)
          - innerHTML targeting the parent container

        A render function that only does `el.textContent = ...` and silently returns
        when `el` is null FAILS this requirement.

        Current code: no DOM creation for this element exists.
        """
        js = _js()

        # Find the account-all-time-cr reference section (±600 chars).
        idx = js.find("account-all-time-cr")
        if idx == -1:
            pytest.fail(
                "AC-CS.3 FAIL: 'account-all-time-cr' not found in index.js at all. "
                "The dedicated renderer does not exist yet."
            )

        # Widen the window to capture the full function (±1500 chars from the testid reference).
        context = js[max(0, idx - 1500) : idx + 1500]

        has_create = (
            "createElement" in context or "insertAdjacentHTML" in context or "innerHTML" in context
        )
        assert has_create, (
            "AC-CS.3 FAIL: the account-all-time-cr renderer does not contain a "
            "DOM-creation pattern (createElement / insertAdjacentHTML / innerHTML). "
            "The cold-start fix requires creating the element when the SSR omitted it. "
            "A querySelector-only renderer silently does nothing when the element is absent."
        )


# ---------------------------------------------------------------------------
# AC-CS.4 — renderer is wired into updateDashboard
# ---------------------------------------------------------------------------


class TestDedicatedRendererWiredIntoDashboard:
    """
    The dedicated renderer is only useful if it is called on each /api/state poll.
    updateDashboard is the central dispatch for all renderers; the account renderer
    must appear in its call list (or be called from a function it dispatches).
    """

    def test_update_dashboard_calls_account_renderer(self):
        """
        AC-CS.4: updateDashboard must call the dedicated account-all-time renderer.

        Contract: the updateDashboard function body must contain a call to a function
        that references account stat rendering — we detect this by asserting that a
        call involving 'updateAccount' or 'renderAccount' or 'AccountAllTime' appears
        in the updateDashboard body.  (Case-insensitive to allow reasonable naming.)

        Current code: updateDashboard has no such call.
        """
        js = _js()

        start = js.find("function updateDashboard")
        assert start != -1, "updateDashboard function must exist in static/index.js"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 2000]
        body_lower = body.lower()

        has_call = (
            "updateaccountalltime" in body_lower
            or "renderaccountalltime" in body_lower
            or "renderaccount" in body_lower
            or "updateaccount" in body_lower
        )
        assert has_call, (
            "AC-CS.4 FAIL: updateDashboard does not call an account-all-time renderer. "
            "The dedicated function must be dispatched from updateDashboard (the main "
            "poll handler) so the account stat is updated on each /api/state poll. "
            "Current updateDashboard has no such call."
        )


# ---------------------------------------------------------------------------
# Non-regression: renderGuardAlpha still reads ps.guard_alpha (render-basis fix)
# ---------------------------------------------------------------------------


class TestGuardAlphaRenderBasisNonRegression:
    """
    Non-regression: the render-basis fix (cycle dash-render-fix, merged e863c9e)
    wired renderGuardAlpha to read `ps.guard_alpha` directly.  The cold-start
    fix must NOT regress this by re-introducing a cr - crHeld derivation.
    """

    def test_render_guard_alpha_still_reads_ps_guard_alpha(self):
        """
        renderGuardAlpha must read `ps.guard_alpha` directly.

        The cold-start fix touches updateDashboard and adds a new function;
        it must not modify renderGuardAlpha.
        """
        js = _js()

        start = js.find("function renderGuardAlpha")
        if start == -1:
            pytest.fail("renderGuardAlpha function not found in static/index.js")
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 800]

        assert "ps.guard_alpha" in body or ".guard_alpha" in body, (
            "NON-REGRESSION FAIL: renderGuardAlpha no longer reads a .guard_alpha "
            "field. The cold-start fix must not regress the render-basis fix "
            "(dash-render-fix cycle, e863c9e)."
        )

    def test_render_guard_alpha_does_not_reintroduce_cr_minus_crheld(self):
        """
        renderGuardAlpha must NOT derive guard_alpha as cr − crHeld.

        That subtraction (the original bug) mixes account-basis if_held with
        windowed VW dry_run and produces a fabricated negative value.
        """
        js = _js()

        start = js.find("function renderGuardAlpha")
        if start == -1:
            pytest.fail("renderGuardAlpha function not found in static/index.js")
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 800]

        # Forbid the exact subtraction pattern that caused the original bug.
        # Strip comment lines (lines starting with optional whitespace + //) to avoid
        # matching the explanatory comment "Do NOT derive ... dry_run - ... if_held".
        non_comment_lines = "\n".join(
            line for line in body.splitlines() if not line.lstrip().startswith("//")
        )
        has_subtraction = bool(
            re.search(r"\bcr\s*-\s*crHeld\b", non_comment_lines)
            or re.search(r"dry_run\b.*?\bif_held\b", non_comment_lines, re.DOTALL)
        )
        assert not has_subtraction, (
            "NON-REGRESSION FAIL: renderGuardAlpha contains a cr - crHeld or "
            "dry_run - if_held subtraction (outside of comments). This is the "
            "original render-basis bug. The cold-start fix must not reintroduce it."
        )
