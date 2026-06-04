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
