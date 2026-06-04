"""
RED tests — dash-render-fix cycle.

THE BUG (PM live gate, 2026-06-04 @ main 5ca86f1):
  renderGuardAlpha (static/index.js) paints the hero Guard Alpha headline by computing
  `portfolio_strip.cumulative_return.dry_run - portfolio_strip.cumulative_return.if_held`.
  That `if_held` is the ACCOUNT-BASIS all-time CR (~63.95%), NOT the windowed VW if_held
  (~26.65%). So the headline shows −36.18% = 27.56 − 63.95 instead of +0.90%.

  Simultaneously, updateComparisonRows populates comp-cumulative-held-text from
  `portfolio_strip.cumulative_return.if_held` — the same account-basis value — so that
  cell also shows the wrong baseline (~63.95% rather than ~26.65%).

  The server ALREADY exposes `portfolio_strip.guard_alpha = 0.904` (the pre-computed
  windowed VW value) and the windowed strip at `/api/strip/<window>` has a VW if_held.
  Both the SSR template (templates/index.html:797-798) and the /api/strip route used
  correct values — the client renderer is the only broken surface.

INVARIANTS the fix must satisfy (these tests pin them):
  1. renderGuardAlpha reads portfolio_strip.guard_alpha directly — NOT re-derives cr−crHeld.
  2. On a windowed strip fetch, renderGuardAlpha reads the windowed strip's guard_alpha.
  3. The cumulative row Held in updateComparisonRows comes from the WINDOWED strip's
     if_held (present after a window-picker click) — NOT the account-basis field.
  4. account-all-time-cr is not written by renderGuardAlpha or updateComparisonRows
     (it must stay SSR-only, unchanged by picker window changes).
  5. Self-consistency: the displayed delta from updateComparisonRows equals
     displayed Bot − displayed Held (this is already wired by AC-4a in test_dashboard_render_consistency.py;
     validated here against the WINDOWED cumulative values).
  6. Zero oracle: when windowed strip dry_run == if_held (flat/never-traded portfolio),
     guard_alpha == 0.00% exactly. The current code would give 0.00% on the delta path
     but −0.00% on the headline if guard_alpha isn't served, so we pin the field presence.

Harness note: this project has no jsdom — the established pattern (feedback_visual_gate_catches_js_break)
is source-text assertions against static/index.js + node --check. The PM owns the live page gate on :8090.
These tests catch the client wiring contract; the ux-expert visual gate catches the rendered result.
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


def _html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Parse gate — broken JS means every renderer silently never runs
# ---------------------------------------------------------------------------


class TestIndexJsParseGate:
    """node --check must pass so the JS engine executes renderGuardAlpha at all."""

    def test_node_check_passes(self):
        node = shutil.which("node")
        if node is None:
            pytest.skip("node not available in this environment")
        result = subprocess.run(
            [node, "--check", str(_JS_PATH)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "RENDER-BASIS FAIL: static/index.js has a parse error. "
            "The entire renderGuardAlpha + updateComparisonRows never runs while "
            "every server/template test remains green.\n"
            f"node --check stderr:\n{result.stderr.strip()}"
        )


# ---------------------------------------------------------------------------
# Invariant 1 — renderGuardAlpha reads guard_alpha directly
# ---------------------------------------------------------------------------


class TestRenderGuardAlphaReadsGuardAlphaField:
    """renderGuardAlpha must read portfolio_strip.guard_alpha (or meta.portfolio.guard_alpha)
    directly instead of re-deriving cr - crHeld from the cumulative_return object.

    THE FAILING PATTERN (current code, line 136):
        var guard_alpha = cr - crHeld;

    That subtraction uses the account-basis if_held (~63.95%) for crHeld and the windowed
    VW dry_run (~27.56%) for cr, producing −36.18% instead of +0.90%.

    THE CORRECT PATTERN: read the server-computed guard_alpha directly.
        var guard_alpha = ps.guard_alpha;   // portfolio_strip.guard_alpha
        OR
        var guard_alpha = (data.meta.portfolio || {}).guard_alpha;
    """

    def test_render_guard_alpha_reads_guard_alpha_field_not_cr_minus_crheld(self):
        """renderGuardAlpha must NOT compute guard_alpha as cr − crHeld from
        cumulative_return. The subtraction mixes account-basis if_held with windowed
        VW dry_run, producing a fabricated negative number.

        Structural contract: the renderGuardAlpha function body must contain an access
        to a `.guard_alpha` property AND must NOT contain the pattern
        `cr - crHeld` or `dry_run... - ...if_held` as the headline source.
        """
        js = _js()

        # Locate the renderGuardAlpha function body.
        start = js.find("function renderGuardAlpha")
        assert start != -1, (
            "RENDER-BASIS FAIL: renderGuardAlpha not found in static/index.js. "
            "The function that paints the guard-alpha headline must exist."
        )
        # Slice up to the next top-level function declaration.
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 600]

        # The function must read guard_alpha from the payload — either from
        # portfolio_strip.guard_alpha or meta.portfolio.guard_alpha.
        reads_guard_alpha_field = (
            "guard_alpha" in body
            and (
                "ps.guard_alpha" in body
                or ".guard_alpha" in body
            )
        )
        assert reads_guard_alpha_field, (
            "RENDER-BASIS FAIL: renderGuardAlpha does not read a `.guard_alpha` "
            "field from the payload. It must read `ps.guard_alpha` (portfolio_strip) "
            "or `meta.portfolio.guard_alpha` directly — NOT re-derive the value. "
            f"renderGuardAlpha body:\n{body[:500]}"
        )

    def test_render_guard_alpha_does_not_subtract_crheld_from_cr(self):
        """The specific subtract-pattern that caused the bug must NOT exist in
        renderGuardAlpha. Any `cr - crHeld` (or equivalent dry_run minus if_held
        arithmetic) in the headline renderer is the live bug — account if_held (~63.95)
        is not commensurable with windowed VW dry_run (~27.56).
        """
        js = _js()

        start = js.find("function renderGuardAlpha")
        assert start != -1, "renderGuardAlpha function must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 600]

        # The buggy arithmetic: cr - crHeld.
        # Accept any of the common re-derivation patterns.
        buggy_patterns = [
            r"\bcr\s*-\s*crHeld\b",
            r"\bdry_run\s*-\s*if_held\b",
            r"\bcr\s*-\s*cr_if_held\b",
        ]
        for pattern in buggy_patterns:
            assert not re.search(pattern, body), (
                f"RENDER-BASIS FAIL: renderGuardAlpha contains the re-derive pattern "
                f"'{pattern}' — this is the arithmetic that mixes account-basis if_held "
                f"(~63.95%) with windowed VW dry_run (~27.56%), producing the live "
                f"−36.18% bug. The fix must read `ps.guard_alpha` directly.\n"
                f"renderGuardAlpha body:\n{body[:500]}"
            )


# ---------------------------------------------------------------------------
# Invariant 2 — windowed strip fetch also feeds guard_alpha directly
# ---------------------------------------------------------------------------


class TestWindowedStripFetchFeedsGuardAlphaField:
    """After a picker click, fetchWindowedStrip wraps the strip response and calls
    renderGuardAlpha(wrapped). The wrapped object must preserve the strip's guard_alpha
    field so renderGuardAlpha can read it — the wrapper must not strip it out.

    Current code at line 1247-1251:
        var wrapped = {
            portfolio_strip: strip,          <-- strip = full /api/strip/<window> response
            meta: { portfolio: { vol_bot: strip.vol_bot, vol_held: strip.vol_held } }
        };
        renderGuardAlpha(wrapped);

    If strip carries guard_alpha, wrapped.portfolio_strip.guard_alpha is accessible to
    renderGuardAlpha. This test pins that the wrapper pattern includes the full strip
    as portfolio_strip (not a subset that drops guard_alpha).
    """

    def test_windowed_strip_wrapped_as_portfolio_strip_field(self):
        """fetchWindowedStrip must wrap the full strip response as wrapped.portfolio_strip
        so renderGuardAlpha can access wrapped.portfolio_strip.guard_alpha.
        """
        js = _js()

        # Locate the fetchWindowedStrip function body.
        start = js.find("function fetchWindowedStrip")
        assert start != -1, (
            "RENDER-BASIS FAIL: fetchWindowedStrip not found in static/index.js. "
            "The function that wraps windowed strip data and calls renderGuardAlpha must exist."
        )
        end_marker = js.find("\n        function ", start + 1)
        if end_marker == -1:
            end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 600]

        # The wrapper must assign the full strip to portfolio_strip.
        assert "portfolio_strip: strip" in body or "portfolio_strip:strip" in body, (
            "RENDER-BASIS FAIL: fetchWindowedStrip does not wrap the strip as "
            "`portfolio_strip: strip`. If the wrapper drops guard_alpha from the strip, "
            "renderGuardAlpha cannot read it, and the windowed headline stays broken "
            "after a picker click.\nbody:\n" + body[:400]
        )

    def test_windowed_strip_fetch_calls_render_guard_alpha(self):
        """After fetching the windowed strip, renderGuardAlpha must be called with
        the wrapped payload so the headline re-renders for the selected window.
        """
        js = _js()

        start = js.find("function fetchWindowedStrip")
        assert start != -1, "fetchWindowedStrip function must exist"
        end_marker = js.find("\n        function ", start + 1)
        if end_marker == -1:
            end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 600]

        assert "renderGuardAlpha" in body, (
            "RENDER-BASIS FAIL: fetchWindowedStrip does not call renderGuardAlpha. "
            "The windowed headline must re-render after every picker fetch — "
            "fetching but not rendering leaves the headline stale.\nbody:\n" + body[:400]
        )


# ---------------------------------------------------------------------------
# Invariant 3 — updateComparisonRows cumulative Held comes from windowed VW
# ---------------------------------------------------------------------------


class TestCumulativeRowHeldIsWindowedVw:
    """updateComparisonRows reads the cumulative row's if_held from
    portfolio_strip.cumulative_return.if_held. When the page first loads, that field
    contains the ACCOUNT-BASIS all-time value (~63.95%). After a picker click, the
    windowed strip returns the WINDOWED VW if_held (~26.65%).

    The fix must ensure that when `portfolio_strip.guard_alpha` is available, the
    cumulative row Held is derived from the windowed strip's if_held — which is the
    value that is self-consistent with guard_alpha and dry_run from the same window.

    This test pins the structural contract: the default poll path must NOT use
    account-basis if_held for the comparison row when a windowed guard_alpha is present.
    The primary enforcement mechanism is that the server's windowed strip (called by
    the picker) supplies a VW if_held that `updateComparisonRows` reads directly.

    Source-level assertion: updateComparisonRows reads `portfolio_strip.cumulative_return.if_held`
    — which must be the VW value for windowed strip responses. We pin that the JS does NOT
    override/substitute an account-basis field for the cumulative row Held.
    """

    def test_update_comparison_rows_reads_cumulative_if_held_from_portfolio_strip(self):
        """updateComparisonRows must read the cumulative row's if_held from
        portfolio_strip.cumulative_return (the windowed strip), NOT from a separate
        account-basis source such as portfolio.cr_if_held or portfolio.account_all_time_cr.

        If the function substitutes the account CR, comp-cumulative-held-text would show
        ~63.95% instead of the windowed ~26.65%, and comp-cumulative-delta would
        fabricate ~−36pp instead of the true guard alpha.
        """
        js = _js()

        start = js.find("function updateComparisonRows")
        assert start != -1, "updateComparisonRows function must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 3000]

        # The function must NOT substitute account_all_time_cr or cr_if_held
        # into the cumulative row Held (that is the bug pattern).
        forbidden = ["account_all_time_cr", "account_cr", "cr_if_held"]
        for f in forbidden:
            assert f not in body, (
                f"RENDER-BASIS FAIL: updateComparisonRows references '{f}' — "
                f"this substitutes the account-basis value into the comparison rows. "
                f"The cumulative row Held must be read from the windowed strip's "
                f"portfolio_strip.cumulative_return.if_held, not the account basis.\n"
                f"body (first 500 chars):\n{body[:500]}"
            )

    def test_update_comparison_rows_cumulative_row_reads_if_held_from_strip(self):
        """The cumulative row entry in updateComparisonRows must use `ps.cumulative_return`
        (the windowed-strip-sourced object) for its if_held — not a hard-coded account field.
        This is the affirmative contract: the value MUST come from the strip.
        """
        js = _js()

        start = js.find("function updateComparisonRows")
        assert start != -1, "updateComparisonRows function must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 3000]

        # The row definition for 'cumulative' must reference ps.cumulative_return.
        assert "cumulative_return" in body, (
            "RENDER-BASIS FAIL: updateComparisonRows body does not reference "
            "`cumulative_return`. The cumulative comparison row must source its "
            "if_held (and dry_run) from `portfolio_strip.cumulative_return`."
        )


# ---------------------------------------------------------------------------
# Invariant 4 — account-all-time-cr is not written by JS renderers
# ---------------------------------------------------------------------------


class TestAccountAllTimeCrNotWrittenByJs:
    """account-all-time-cr (data-testid) is SSR-rendered by the template and must NOT
    be overwritten by renderGuardAlpha or updateComparisonRows on poll/picker events.
    If either function writes to it, the all-time stat would update on every 30s poll
    or picker click — which is wrong because the account-basis CR is not windowed.
    """

    def test_render_guard_alpha_does_not_reference_account_all_time_cr(self):
        """renderGuardAlpha must not address account-all-time-cr."""
        js = _js()

        start = js.find("function renderGuardAlpha")
        assert start != -1, "renderGuardAlpha function must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 600]

        assert "account-all-time" not in body and "account_all_time" not in body, (
            "RENDER-BASIS FAIL: renderGuardAlpha references account-all-time-cr. "
            "This function paints the WINDOWED guard-alpha headline — it must never "
            "write to the SSR-rendered account-all-time stat."
        )

    def test_update_comparison_rows_does_not_write_account_all_time_cr(self):
        """updateComparisonRows must not write to account-all-time-cr."""
        js = _js()

        start = js.find("function updateComparisonRows")
        assert start != -1, "updateComparisonRows function must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 3000]

        # Forbid textContent assignment targeting the account-all-time-cr element.
        # A querySelector + textContent write is the only way to modify it.
        combined = "account-all-time" in body and "textContent" in body
        assert not combined, (
            "RENDER-BASIS FAIL: updateComparisonRows contains both 'account-all-time' "
            "and 'textContent'. This risks overwriting the SSR-rendered account stat "
            "with a windowed value on every poll, breaking the 'Account · all-time' "
            "semantic."
        )

    def test_account_all_time_cr_only_in_template_not_in_js_textcontent(self):
        """account-all-time-cr must be rendered in the template SSR; index.js must not
        contain a textContent assignment that targets it. A querySelector for it in JS
        is acceptable for read-only purposes but a write would clobber the SSR value
        with a (potentially window-dependent) re-render.
        """
        js = _js()

        # Safe: presence of the testid string alone (e.g. for reading it in a test helper).
        # Unsafe: the testid AND a .textContent = pattern within 200 chars.
        idx = js.find("account-all-time-cr")
        if idx == -1:
            return  # Not referenced at all — ideal.
        nearby = js[max(0, idx - 50): idx + 200]
        assert ".textContent" not in nearby and "textContent =" not in nearby, (
            "RENDER-BASIS FAIL: index.js has a textContent assignment near "
            "'account-all-time-cr'. The account-all-time stat must be SSR-only; "
            "a JS write would clobber the template's rendered value with an "
            "incorrect or window-dependent number."
        )


# ---------------------------------------------------------------------------
# Invariant 5 — guard-alpha-headline testid must exist in the template
# ---------------------------------------------------------------------------


class TestGuardAlphaHeadlineTestidPresent:
    """The guard-alpha-headline element must carry a stable data-testid so the PM's
    live gate and the ux-expert's visual gate can locate and verify it.
    """

    def test_template_has_guard_alpha_headline_testid(self):
        html = _html()
        assert 'data-testid="guard-alpha-headline"' in html, (
            "RENDER-BASIS FAIL: templates/index.html has no element with "
            'data-testid="guard-alpha-headline". '
            "The PM's live gate + ux-expert visual gate rely on this testid to verify "
            "the rendered value."
        )

    def test_template_guard_alpha_headline_uses_server_guard_alpha(self):
        """The SSR template must use the server's guard_alpha field (not cr - cr_if_held)
        for the first-paint headline. The SSR value must match the JS renderer's source
        on first paint so the page doesn't flicker/correct on the first poll.

        The template comment at index.html:793-798 documents this: prefer _ga (guard_alpha)
        and fall back to cr - cr_if_held only if absent. The fallback must NOT be the
        default path when guard_alpha is present.
        """
        html = _html()
        # The template must reference guard_alpha (not just cr - cr_if_held).
        assert "guard_alpha" in html, (
            "RENDER-BASIS FAIL: templates/index.html does not reference 'guard_alpha'. "
            "The SSR headline must use the server's windowed guard_alpha field so the "
            "first-paint value matches the JS renderer (no flicker on first poll)."
        )


# ---------------------------------------------------------------------------
# Invariant 6 — zero oracle: guard_alpha == 0 when strip dry_run == if_held
# ---------------------------------------------------------------------------


class TestZeroOracleInRenderGuardAlpha:
    """When the windowed strip has dry_run == if_held (a flat/never-traded portfolio),
    the guard-alpha headline must display 0.00% exactly.

    Under the BUGGY code:
      cr = portfolio_strip.cumulative_return.dry_run  (VW, may be 0)
      crHeld = portfolio_strip.cumulative_return.if_held  (account-basis, >0)
      guard_alpha = cr - crHeld = 0 - 63.95 = −63.95%   <-- wrong

    Under the CORRECT code (reading guard_alpha directly):
      portfolio_strip.guard_alpha = 0 when dry_run == if_held on a VW basis
      → headline = 0.00%   <-- correct

    This is a source-level structural test: the function must NOT produce a non-zero
    result when guard_alpha == 0. We assert this by verifying the function reads
    guard_alpha (not the subtract path) — the arithmetic then naturally gives 0.
    """

    def test_render_guard_alpha_produces_zero_when_guard_alpha_is_zero(self):
        """Structural guard: renderGuardAlpha reading guard_alpha == 0 must paint 0.00%,
        not account_all_time_cr or cr - crHeld.

        This is encoded as the source contract: the function must use guard_alpha as its
        source value. When guard_alpha == 0 (VW dry_run == VW if_held), the output is 0.
        The buggy subtract pattern would give -63.95% or similar instead.
        """
        js = _js()

        start = js.find("function renderGuardAlpha")
        assert start != -1, "renderGuardAlpha must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 600]

        # The function must contain the direct guard_alpha read (from Invariant 1).
        # When guard_alpha == 0, fmtPct(0) == "+0.00%" — correct.
        # When guard_alpha == cr - crHeld with account if_held, the result is wrong.
        reads_field = ".guard_alpha" in body
        assert reads_field, (
            "ZERO-ORACLE FAIL: renderGuardAlpha does not read .guard_alpha. "
            "When the windowed strip has guard_alpha=0 (VW dry_run==if_held), "
            "the headline must show 0.00%. The buggy subtract path would show "
            "the full negative of account_all_time_cr instead (e.g. −63.95%)."
        )

    def test_render_guard_alpha_does_not_default_to_zero_guard_alpha(self):
        """renderGuardAlpha must not substitute 0 when guard_alpha is absent from the
        strip — it must forward the null/undefined so fmtPct returns '--' rather than
        fabricating a false 0.00% zero-oracle match. The function must pass through
        null/undefined, not default to 0.
        """
        js = _js()

        start = js.find("function renderGuardAlpha")
        assert start != -1, "renderGuardAlpha must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 600]

        # The function must NOT coerce a missing guard_alpha to 0 via `|| 0` or `|| 0.0`
        # on the guard_alpha read itself (the cumulative_return path did this and fabricated
        # non-null results when the field was absent).
        # Coercing the fmtPct default is fine; coercing the field itself is the bug.
        # Accept a guard_alpha null/undefined guard (like `if (ga == null) return;`)
        # but reject `ps.guard_alpha || 0` as the sole guard_alpha source.
        buggy_coerce = re.search(r"ps\.guard_alpha\s*\|\|\s*0", body)
        assert not buggy_coerce, (
            "ZERO-ORACLE FAIL: renderGuardAlpha coerces `ps.guard_alpha || 0`. "
            "A missing guard_alpha must render as '--' (via fmtPct null guard), not 0.00%. "
            "Otherwise when the strip is cold or missing the field, a false '0.00%' appears."
        )


# ---------------------------------------------------------------------------
# Invariant 7 — the comp-cumulative-delta self-consistency contract
# ---------------------------------------------------------------------------


class TestCumulativeDeltaSelfConsistency:
    """The comp-cumulative-delta span must show the delta that equals displayed Bot − Held.
    This is already wired by AC-4a (test_dashboard_render_consistency.py). Here we add the
    cross-check: after a picker fetch the delta must be derived from the WINDOWED cumulative
    row values (not from a stale account-basis Held). We test the structural source contract.
    """

    def test_update_comparison_rows_derives_cumulative_delta_from_its_row_values(self):
        """The delta for the cumulative row must be derived from the same `values` object
        (portfolio_strip.cumulative_return) as its bot and held text — not from a separate
        account-basis field. This pins that the delta computation uses `bot - held` where
        both bot and held come from the windowed strip.
        """
        js = _js()

        start = js.find("function updateComparisonRows")
        assert start != -1, "updateComparisonRows must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 3000]

        # The delta must be computed from the row's own bot/held values (inside the
        # rows.forEach loop), not from a separate account-level field. We assert the
        # delta assignment pattern (`bot - held` or equivalent) is present inside the
        # forEach body — which guarantees it uses the row's windowed values.
        assert re.search(r"\bbot\s*-\s*held\b", body), (
            "RENDER-BASIS FAIL: updateComparisonRows does not compute `bot - held` "
            "for the cumulative delta. The delta must be derived from the row's own "
            "windowed values so it equals displayed Bot − Held by construction. "
            "AC-4a compliance requires this arithmetic inside the rows.forEach loop."
        )


# ---------------------------------------------------------------------------
# Invariant 8 — updateComparisonRows uses windowed_cumulative_return when present
# ---------------------------------------------------------------------------


class TestUpdateComparisonRowsPrefersWindowedCumulativeReturn:
    """On initial page load, portfolio_strip.cumulative_return carries account-basis
    if_held (~63.95%). The windowed strip (post-picker-click) carries VW if_held (~26.65%).

    The implementer's fix plan: _compute_portfolio_strip stores the windowed cumulative
    row as `windowed_cumulative_return` in the strip. updateComparisonRows must prefer
    `ps.windowed_cumulative_return` over `ps.cumulative_return` for the cumulative row
    so the initial-load Held value is windowed VW, not account-basis.

    These tests are RED until index.js is updated to prefer windowed_cumulative_return.

    Note: the window-picker path (fetchWindowedStrip) already wraps the full windowed
    strip as portfolio_strip — so for the picker case the values in cumulative_return ARE
    windowed VW. The problem is the initial poll where the strip's cumulative_return
    carries the account-basis values. The windowed_cumulative_return field decouples
    the two paths.
    """

    def test_update_comparison_rows_references_windowed_cumulative_return(self):
        """updateComparisonRows must reference `windowed_cumulative_return` in its
        cumulative row definition so it can prefer the windowed VW values over the
        account-basis cumulative_return on initial load.
        """
        js = _js()

        start = js.find("function updateComparisonRows")
        assert start != -1, "updateComparisonRows must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 3000]

        assert "windowed_cumulative_return" in body, (
            "RENDER-BASIS FAIL: updateComparisonRows does not reference "
            "`windowed_cumulative_return`. The cumulative row must prefer the windowed VW "
            "values (dry_run ~27.56%, if_held ~26.65%) over the account-basis values "
            "(if_held ~63.95%) on initial page load. "
            "The implementer must: (1) add windowed_cumulative_return to portfolio_strip "
            "in app.py, (2) have updateComparisonRows prefer it for the cumulative row."
        )

    def test_cumulative_row_values_object_falls_back_to_cumulative_return(self):
        """When windowed_cumulative_return is absent (cold path), updateComparisonRows
        must fall back to cumulative_return so the row still renders rather than breaking.
        The fallback pattern `ps.windowed_cumulative_return || ps.cumulative_return || {}`
        or equivalent must be present.
        """
        js = _js()

        start = js.find("function updateComparisonRows")
        assert start != -1, "updateComparisonRows must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 3000]

        # Both field names must co-exist in the rows definition — the windowed field
        # as the preferred source and cumulative_return as the fallback.
        has_windowed = "windowed_cumulative_return" in body
        has_fallback = "cumulative_return" in body
        assert has_windowed and has_fallback, (
            "RENDER-BASIS FAIL: updateComparisonRows must reference both "
            "`windowed_cumulative_return` (preferred) and `cumulative_return` (fallback). "
            f"has_windowed={has_windowed}, has_fallback={has_fallback}. "
            "A cold cache must degrade gracefully — the row must not break when "
            "windowed_cumulative_return is absent from the strip."
        )

    def test_app_portfolio_strip_exposes_windowed_cumulative_return(self):
        """_compute_portfolio_strip (app.py) must expose windowed_cumulative_return in
        the strip dict. Without the server-side field the JS can never read it.
        Source-level: the field name must appear in app.py near the windowed strip logic.
        """
        app_py = (
            pathlib.Path(__file__).parent.parent.parent / "app.py"
        ).read_text(encoding="utf-8")

        assert "windowed_cumulative_return" in app_py, (
            "RENDER-BASIS FAIL: app.py does not contain `windowed_cumulative_return`. "
            "_compute_portfolio_strip must store the windowed VW cumulative_return "
            "(from compute_windowed_portfolio_strip) as this field so index.js can "
            "read it for the cumulative comparison row on initial load. "
            "Without this, the initial-load Held will always be account-basis (~63.95%)."
        )


# ---------------------------------------------------------------------------
# Invariant 9 — closed_frozen fallback: JS fetches /api/strip when guard_alpha absent
#
# UX-expert live gate finding (2026-06-04):
#   When market is closed_frozen, /api/state portfolio_strip has NO guard_alpha field.
#   The fix's renderGuardAlpha reads `ps.guard_alpha` which is undefined → null → '--'.
#   The headline shows '--' instead of the correct windowed value (+0.90%).
#
#   Root cause: the frozen path in app.py (_compute_portfolio_strip is not called for
#   the frozen /api/state response; the _portfolio_strip dict built at line 1262 never
#   invokes compute_windowed_portfolio_strip so guard_alpha is absent).
#
#   Required fix: when renderGuardAlpha receives a null guard_alpha on the first poll,
#   the JS must immediately fetch /api/strip/<default-window> and re-render the headline
#   with the strip's guard_alpha. /api/strip/<window> DOES return guard_alpha correctly
#   in all market states (confirmed by ux-expert).
#
#   Additionally: the frozen portfolio_strip.cumulative_return carries account-basis
#   if_held (~63.95%); windowed_cumulative_return is absent. The cumulative row therefore
#   falls back to account-basis. The same strip fetch must trigger updateComparisonRows
#   with the windowed strip so the cumulative row also corrects.
# ---------------------------------------------------------------------------


class TestClosedFrozenFallbackFetchesStrip:
    """When portfolio_strip.guard_alpha is absent (closed_frozen market state),
    renderGuardAlpha must trigger a fallback fetch of /api/strip/<default-window>
    to populate the headline. Without this, the headline shows '--' on market close.

    These are source-text assertions against index.js. The PM live gate on :8090
    (or :8095 with worktree code) is the definitive verification surface.
    """

    def test_render_guard_alpha_triggers_strip_fallback_when_guard_alpha_null(self):
        """When portfolio_strip.guard_alpha is absent (null), renderGuardAlpha or its
        caller must trigger fetchWindowedStrip so the headline does not stay '--'.

        Source contract: renderGuardAlpha must call fetchWindowedStrip when guard_alpha
        is null, OR updateDashboard/loadState must call fetchWindowedStrip after a null
        guard_alpha is detected. Either way: fetchWindowedStrip must be called from the
        renderGuardAlpha body OR from updateDashboard (which calls renderGuardAlpha).

        Current code: renderGuardAlpha body has `guard_alpha != null` only for the color
        guard — it does NOT call fetchWindowedStrip when guard_alpha is null. That is the
        bug: null guard_alpha → renders '--', never triggers fallback.
        """
        js = _js()

        start = js.find("function renderGuardAlpha")
        assert start != -1, "renderGuardAlpha must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 800]

        # renderGuardAlpha must call fetchWindowedStrip when guard_alpha is null.
        # The fallback can be inline: `if (guard_alpha == null) { fetchWindowedStrip('30d'); return; }`
        assert "fetchWindowedStrip" in body, (
            "FROZEN-PATH FAIL: renderGuardAlpha does not call fetchWindowedStrip. "
            "When portfolio_strip.guard_alpha is null (closed_frozen market state), "
            "the function renders '--' and the headline never corrects. "
            "The fix: inside renderGuardAlpha, when guard_alpha is null, call "
            "fetchWindowedStrip('30d') to populate the headline from the strip API."
        )

    def test_update_dashboard_calls_strip_fallback_when_guard_alpha_absent(self):
        """fetchWindowedStrip must be called on the INITIAL LOAD path, not only on
        picker click. The picker click is already wired (1 call site). A second call
        site is needed for the closed_frozen fallback.

        Source contract: fetchWindowedStrip must appear as a CALL (not just a definition)
        at least twice in index.js — once in the picker click handler (existing) and once
        on a path reachable without user interaction (new — for initial load fallback).

        Note: the function DEFINITION (`function fetchWindowedStrip(token) {`) is NOT a
        call site. We count only `fetchWindowedStrip(` occurrences that are NOT the
        function declaration line.
        """
        js = _js()

        # Count call sites (exclude the function declaration itself).
        # The declaration is: `function fetchWindowedStrip(token) {`
        # A call site is: `fetchWindowedStrip(` NOT preceded by `function `.
        # Strategy: find all matches of fetchWindowedStrip( and exclude the declaration.
        all_matches = [(m.start(), m.end()) for m in re.finditer(r"fetchWindowedStrip\s*\(", js)]
        call_sites = [
            pos for pos, end in all_matches
            if not js[max(0, pos - 10):pos].rstrip().endswith("function")
        ]

        assert len(call_sites) >= 2, (
            f"FROZEN-PATH FAIL: fetchWindowedStrip is called only {len(call_sites)} time(s) "
            "(excluding the function declaration). For the closed_frozen path, it must be "
            "called at least twice: once in the picker-click handler (existing) and once on "
            "the initial-load fallback path (new — fires when guard_alpha is absent). "
            f"Call site positions found: {call_sites}"
        )

    def test_strip_fallback_uses_default_window_token(self):
        """The fallback strip fetch must use the default window token ('30d') to match
        the active picker button and window label on page load. Using a hardcoded '30d'
        token or the _DEFAULT_HERO_WINDOW constant is acceptable — using 'all' or any
        non-default token would mismatch the label and produce a confusing headline value.
        """
        js = _js()

        # Locate fetchWindowedStrip call sites and check that at least one is called
        # with '30d' as the token (the default window that matches the active picker button).
        # This pins the default-window invariant: the fallback headline must match
        # what the picker shows as active on load.
        has_default_token_call = (
            "fetchWindowedStrip('30d')" in js
            or 'fetchWindowedStrip("30d")' in js
            or re.search(r"fetchWindowedStrip\s*\(\s*_DEFAULT_HERO_WINDOW\s*\)", js) is not None
            or re.search(r"fetchWindowedStrip\s*\(\s*['\"]30d['\"]", js) is not None
        )
        assert has_default_token_call, (
            "FROZEN-PATH FAIL: no fetchWindowedStrip('30d') call found in index.js. "
            "The closed_frozen fallback must use the default window token ('30d') so the "
            "headline value matches the active picker button label on initial load. "
            "Acceptable forms: fetchWindowedStrip('30d') or fetchWindowedStrip(_DEFAULT_HERO_WINDOW)."
        )


# ---------------------------------------------------------------------------
# Invariant 10 — frozen portfolio_strip must expose guard_alpha (server-side fix)
#   OR the JS fallback must fire — both paths are acceptable; these tests pin
#   whichever the implementer chooses. If the server is fixed, Invariant 9 is
#   redundant (tests still pass — the fallback just never fires). If only the
#   JS is fixed, the frozen _portfolio_strip dict stays as-is and the JS fallback
#   handles it.
# ---------------------------------------------------------------------------


class TestFrozenPortfolioStripGuardAlpha:
    """The frozen /api/state portfolio_strip either carries guard_alpha directly
    (server-side fix) OR the JS fetches /api/strip/<window> as a fallback (JS fix).
    These tests pin the server-side contract; if the implementer adds guard_alpha to
    the frozen strip instead of fixing the JS, these tests confirm it.

    These tests are complementary to Invariant 9 — at least one of the two paths
    must be correct for the headline to work on closed_frozen.
    """

    def test_frozen_portfolio_strip_dict_in_app_py_exposes_guard_alpha_or_js_fallback_exists(self):
        """Either (a) the frozen _portfolio_strip in app.py includes guard_alpha
        from compute_windowed_portfolio_strip, OR (b) fetchWindowedStrip is called
        on the initial-load path in index.js (Invariant 9).

        This test passes if EITHER path is wired. It fails only if NEITHER is present,
        which would leave the headline showing '--' on closed_frozen permanently.
        """
        app_py = (
            pathlib.Path(__file__).parent.parent.parent / "app.py"
        ).read_text(encoding="utf-8")
        js = _js()

        # Path A: server adds guard_alpha to the frozen strip.
        # The frozen _portfolio_strip is built at ~line 1262; check if guard_alpha
        # is set anywhere in the frozen path block (between the frozen snapshot path
        # and the return jsonify at ~line 1304).
        frozen_block_start = app_py.find("# On-the-fly portfolio_strip recompute")
        frozen_block_end = app_py.find("return jsonify", frozen_block_start) if frozen_block_start != -1 else -1
        server_has_frozen_guard_alpha = (
            frozen_block_start != -1
            and frozen_block_end != -1
            and "guard_alpha" in app_py[frozen_block_start:frozen_block_end]
        )

        # Path B: JS fetches strip on initial load when guard_alpha is absent.
        # Count CALL sites only (exclude function declaration).
        all_fws = [(m.start(), m.end()) for m in re.finditer(r"fetchWindowedStrip\s*\(", js)]
        fws_calls = [
            pos for pos, end in all_fws
            if not js[max(0, pos - 10):pos].rstrip().endswith("function")
        ]
        js_has_initial_strip_fallback = len(fws_calls) >= 2

        assert server_has_frozen_guard_alpha or js_has_initial_strip_fallback, (
            "FROZEN-PATH FAIL: neither fix path is wired. "
            "The frozen /api/state portfolio_strip has no guard_alpha, AND "
            "fetchWindowedStrip is not called on the initial-load path. "
            "The headline will show '--' on closed_frozen permanently. "
            "Fix: either add guard_alpha to the frozen _portfolio_strip in app.py "
            "OR add a fetchWindowedStrip('30d') call that fires when guard_alpha is "
            "absent from the first poll response."
        )


# ---------------------------------------------------------------------------
# Invariant 11 — fetchWindowedStrip must be at IIFE scope, not inside DOMContentLoaded
#
# quant-code-reviewer BLOCK (cycle-2, 5d3d5cd):
#   fetchWindowedStrip is defined at static/index.js:1260 INSIDE the DOMContentLoaded
#   callback. renderGuardAlpha is at line 131 in IIFE scope. When renderGuardAlpha
#   calls fetchWindowedStrip('30d'), it executes in IIFE scope where fetchWindowedStrip
#   is NOT defined — ReferenceError in strict mode, silently swallowed by updateDashboard
#   try/catch. The fallback never fires; headline stays '--' on closed_frozen.
#
#   Required fix: hoist fetchWindowedStrip to IIFE scope (above DOMContentLoaded).
# ---------------------------------------------------------------------------


class TestFetchWindowedStripAtIifeScope:
    """fetchWindowedStrip must be defined at IIFE scope so renderGuardAlpha can call
    it. A definition inside DOMContentLoaded is not visible from IIFE-scope functions.
    """

    def test_fetch_windowed_strip_defined_before_dom_content_loaded(self):
        """fetchWindowedStrip must be defined BEFORE the main DOMContentLoaded callback.
        If it is defined only inside DOMContentLoaded, renderGuardAlpha (IIFE scope)
        will throw ReferenceError when it calls it.

        Note: this file has two DOMContentLoaded listeners — a tiny CSRF-token fetch
        at line 15 and the main picker/chart setup at line 1234. fetchWindowedStrip
        must be defined before the MAIN (last/largest) DOMContentLoaded callback.

        Source contract: the `function fetchWindowedStrip` declaration must appear
        before the last `document.addEventListener('DOMContentLoaded'` in the file.
        """
        js = _js()

        decl_pos = js.find("function fetchWindowedStrip")
        assert decl_pos != -1, "fetchWindowedStrip function declaration must exist"

        # Find the LAST (main) DOMContentLoaded listener — this is the one that
        # currently contains fetchWindowedStrip and is the source of the scoping bug.
        pattern = "document.addEventListener('DOMContentLoaded'"
        pattern2 = 'document.addEventListener("DOMContentLoaded"'
        all_dom = [m.start() for m in re.finditer(re.escape(pattern), js)]
        all_dom += [m.start() for m in re.finditer(re.escape(pattern2), js)]
        assert all_dom, "DOMContentLoaded listener not found — cannot verify scoping"
        main_dom_pos = max(all_dom)

        assert decl_pos < main_dom_pos, (
            "SCOPING FAIL: fetchWindowedStrip is defined INSIDE the main DOMContentLoaded "
            f"callback (declaration at char {decl_pos}, main DOMContentLoaded starts at "
            f"char {main_dom_pos}). "
            "renderGuardAlpha lives at IIFE scope (char ~4692) and calls "
            "fetchWindowedStrip('30d') on the frozen-path fallback. With the function "
            "nested inside DOMContentLoaded it is invisible from IIFE scope — a "
            "ReferenceError is silently swallowed by updateDashboard's try/catch and the "
            "fallback never fires. "
            "Fix: move `function fetchWindowedStrip(token) {...}` to IIFE scope, BEFORE "
            "the main DOMContentLoaded listener so renderGuardAlpha can reach it."
        )

    def test_render_guard_alpha_and_fetch_windowed_strip_share_scope(self):
        """Both renderGuardAlpha and fetchWindowedStrip must be reachable from the
        same scope. renderGuardAlpha is at IIFE scope (between the two DOMContentLoaded
        callbacks). fetchWindowedStrip must also be at IIFE scope — not inside the
        main DOMContentLoaded (the second/last one, which contains the picker + chart
        setup). The CSRF DOMContentLoaded at line 15 is a tiny 4-line callback; the
        main DOMContentLoaded is the last one in the file and contains fetchWindowedStrip.

        Source contract: fetchWindowedStrip must appear BEFORE the LAST/MAIN
        DOMContentLoaded callback starts. `renderGuardAlpha` is already at IIFE scope.
        """
        js = _js()

        render_pos = js.find("function renderGuardAlpha")
        fetch_pos = js.find("function fetchWindowedStrip")

        assert render_pos != -1, "renderGuardAlpha must exist"
        assert fetch_pos != -1, "fetchWindowedStrip must exist"

        # Find the LAST DOMContentLoaded listener — this is the main callback that
        # contains fetchWindowedStrip in the broken implementation. After the fix,
        # fetchWindowedStrip must be BEFORE it.
        pattern = "document.addEventListener('DOMContentLoaded'"
        pattern2 = 'document.addEventListener("DOMContentLoaded"'
        all_dom = [m.start() for m in re.finditer(re.escape(pattern), js)]
        all_dom += [m.start() for m in re.finditer(re.escape(pattern2), js)]
        assert all_dom, "At least one DOMContentLoaded listener must exist"
        main_dom_pos = max(all_dom)  # The last (main) DOMContentLoaded

        assert fetch_pos < main_dom_pos, (
            "SCOPING FAIL: fetchWindowedStrip is defined INSIDE the main DOMContentLoaded "
            f"callback (declaration at char {fetch_pos}, main DOMContentLoaded at char "
            f"{main_dom_pos}). renderGuardAlpha is at IIFE scope (char {render_pos}) and "
            "cannot reach into the DOMContentLoaded closure. "
            "Fix: hoist `function fetchWindowedStrip(token) {...}` to IIFE scope, "
            "above the main DOMContentLoaded listener."
        )


# ---------------------------------------------------------------------------
# Invariant 12 — renderGuardAlpha must not infinitely recurse via fetchWindowedStrip
#
# quant-code-reviewer BLOCK (cycle-2, 5d3d5cd):
#   compute_windowed_portfolio_strip returns guard_alpha: null when weight_sum == 0
#   (no symphonies or all zero-weight). In that case the strip fetch response also
#   has guard_alpha: null. renderGuardAlpha(wrapped) is called from fetchWindowedStrip
#   with a null guard_alpha → triggers fetchWindowedStrip('30d') again → infinite loop.
#
#   Required fix: guard the fallback call so it cannot fire when already called from
#   a strip fetch. Simplest pattern: `_fromStrip` parameter.
# ---------------------------------------------------------------------------


class TestRenderGuardAlphaNoInfiniteLoop:
    """renderGuardAlpha must not infinitely recurse via fetchWindowedStrip when the
    strip response itself returns guard_alpha: null (zero-weight portfolio edge case).
    """

    def test_render_guard_alpha_fallback_is_guarded_against_recursion(self):
        """renderGuardAlpha must accept a parameter or use a flag that prevents the
        fetchWindowedStrip fallback from firing when renderGuardAlpha was itself
        called FROM fetchWindowedStrip (i.e. the strip response had null guard_alpha).

        Source contract: renderGuardAlpha's function signature must accept a second
        argument (e.g. `_fromStrip`) AND the fetchWindowedStrip fallback must be
        guarded by that argument, e.g.:

            if (guard_alpha === null) {
                if (!_fromStrip) fetchWindowedStrip('30d');
                return;
            }

        AND the call inside fetchWindowedStrip must pass the truthy flag:

            renderGuardAlpha(wrapped, true);

        Either the `_fromStrip` name or any structurally equivalent guard is acceptable.
        The test checks for the structural presence of a recursion guard.
        """
        js = _js()

        # Check 1: renderGuardAlpha function signature takes >=2 params OR uses a closure
        # variable as a recursion guard.
        start = js.find("function renderGuardAlpha")
        assert start != -1, "renderGuardAlpha must exist"
        sig_end = js.find(")", start)
        sig = js[start:sig_end + 1]

        # The signature must have a second parameter (anything after the first comma).
        has_second_param = sig.count(",") >= 1

        # Check 2: the fetchWindowedStrip call inside renderGuardAlpha is conditional,
        # not unconditional. Must not be bare `fetchWindowedStrip('30d');` without a guard.
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 800]

        # The fetchWindowedStrip call must be inside an `if` that checks something —
        # a bare call with no condition is the infinite-loop pattern.
        fws_pos = body.find("fetchWindowedStrip")
        has_guarded_call = False
        if fws_pos != -1:
            # Look back up to 80 chars for an `if` keyword guarding this call.
            pre = body[max(0, fws_pos - 80):fws_pos]
            has_guarded_call = "if (" in pre or "if(" in pre

        assert has_second_param or has_guarded_call, (
            "INFINITE-LOOP FAIL: renderGuardAlpha calls fetchWindowedStrip without a "
            "recursion guard. When the strip response returns guard_alpha: null "
            "(zero-weight portfolio), renderGuardAlpha will call fetchWindowedStrip again, "
            "which calls renderGuardAlpha again — infinite loop. "
            "Fix: add a `_fromStrip` parameter (or equivalent flag) and guard the call: "
            "  `if (guard_alpha === null) { if (!_fromStrip) fetchWindowedStrip('30d'); return; }` "
            "AND pass `true` as the second arg from inside fetchWindowedStrip: "
            "  `renderGuardAlpha(wrapped, true);` "
            f"Current signature: `{sig}`. "
            f"fetchWindowedStrip call context: `{body[max(0,fws_pos-80):fws_pos+30]}`"
        )

    def test_fetch_windowed_strip_passes_from_strip_flag_to_render_guard_alpha(self):
        """Inside fetchWindowedStrip, the call to renderGuardAlpha must pass a truthy
        second argument (the `_fromStrip` flag) so the recursion guard inside
        renderGuardAlpha can detect it came from a strip fetch.

        Source contract: within the fetchWindowedStrip function body, the call to
        renderGuardAlpha must have at least 2 arguments.
        """
        js = _js()

        start = js.find("function fetchWindowedStrip")
        assert start != -1, "fetchWindowedStrip must exist"
        # fetchWindowedStrip can be anywhere now (IIFE scope after hoisting fix)
        end_marker = js.find("\nfunction ", start + 1)
        if end_marker == -1:
            end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start:start + 600]

        # Find `renderGuardAlpha(` inside the body and check it has a second argument.
        rga_pos = body.find("renderGuardAlpha(")
        assert rga_pos != -1, (
            "fetchWindowedStrip must call renderGuardAlpha — function body does not "
            "contain `renderGuardAlpha(`."
        )

        # Extract the call up to the closing paren.
        call_snippet = body[rga_pos:rga_pos + 60]
        has_second_arg = "," in call_snippet.split(")")[0]

        assert has_second_arg, (
            "INFINITE-LOOP FAIL: fetchWindowedStrip calls renderGuardAlpha without a "
            "second argument. The recursion guard (`_fromStrip` or equivalent) in "
            "renderGuardAlpha will never be truthy, so a null guard_alpha in the strip "
            "response will trigger fetchWindowedStrip again, looping infinitely. "
            "Fix: pass `true` as the second argument: `renderGuardAlpha(wrapped, true);` "
            f"Found call: `{call_snippet}`"
        )
