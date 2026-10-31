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
from unittest.mock import patch

import pytest

_JS_PATH = pathlib.Path(__file__).parent.parent.parent / "static" / "index.js"
_HTML_PATH = pathlib.Path(__file__).parent.parent.parent / "templates" / "index.html"


def _js() -> str:
    return _JS_PATH.read_text(encoding="utf-8")


def _html() -> str:
    return _HTML_PATH.read_text(encoding="utf-8")


def _headline_writers(js: str) -> list[tuple[str, str]]:
    """(name, body) of every IIFE-scope function that writes #guard-alpha-headline."""
    positions = [(m.start(), m.group(1)) for m in re.finditer(r"\n    function (\w+)\(", js)]
    out = []
    for n, (start, name) in enumerate(positions):
        end = positions[n + 1][0] if n + 1 < len(positions) else len(js)
        body = js[start:end]
        if "guard-alpha-headline" in body:
            out.append((name, body))
    return out


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
        writers = _headline_writers(js)
        assert writers, (
            "RENDER-BASIS FAIL: no function in static/index.js writes #guard-alpha-headline. "
            "The function that paints the guard-alpha headline must exist."
        )
        body = "\n".join(b for _n, b in writers)

        # The function must read guard_alpha from the payload — either from
        # portfolio_strip.guard_alpha or meta.portfolio.guard_alpha.
        reads_guard_alpha_field = "guard_alpha" in body and (
            "ps.guard_alpha" in body or ".guard_alpha" in body
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

        writers = _headline_writers(js)
        assert writers, "some function must write #guard-alpha-headline"
        body = "\n".join(b for _n, b in writers)

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
# Invariant 2 (the windowed strip wrapped as a poll payload and fed to renderGuardAlpha) pinned a
# wrapper the refresh-timing dashboard plan removed; the strip now has one writer, covered under
# node in tests/dashboard/test_dashboard_window_hold.py.
# ---------------------------------------------------------------------------


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
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 3000]

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
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 3000]

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

        writers = _headline_writers(js)
        assert writers, "some function must write #guard-alpha-headline"
        body = "\n".join(b for _n, b in writers)

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
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 3000]

        # Forbid textContent assignment targeting the account-all-time-cr element.
        # A querySelector + textContent write is the only way to modify it.
        combined = "account-all-time" in body and "textContent" in body
        assert not combined, (
            "RENDER-BASIS FAIL: updateComparisonRows contains both 'account-all-time' "
            "and 'textContent'. This risks overwriting the SSR-rendered account stat "
            "with a windowed value on every poll, breaking the 'Account · all-time' "
            "semantic."
        )

    def test_account_all_time_cr_written_only_by_dedicated_renderer_not_windowed_renderers(self):
        """account-all-time-cr must NOT be written by renderGuardAlpha or
        updateComparisonRows (the windowed renderers).  A dedicated non-windowed
        renderer (e.g. updateAccountAllTime) IS permitted to write textContent —
        that is the cold-start fix (dash-polish cycle, 2026-06-05).

        The invariant that changed: previously the stat was SSR-only.  Now it may be
        created/updated by a dedicated renderer so a cold-start (cache warm after SSR)
        populates the element without a manual reload.  The constraint is that ONLY
        the dedicated renderer may write it; the windowed renderers must not.

        Tests for the dedicated renderer are in test_cold_start_account_stat.py.
        Tests for renderGuardAlpha isolation are in
            TestDedicatedAccountRendererExists.test_dedicated_function_is_not_render_guard_alpha
        Tests for updateComparisonRows isolation are in
            TestDedicatedAccountRendererExists.test_dedicated_function_is_not_update_comparison_rows
        This test is now a cross-check: if the testid appears in the windowed renderers
        with a textContent assignment, that is still wrong.
        """
        js = _js()

        # Check renderGuardAlpha body.
        for _name, rga_body in _headline_writers(js):
            assert "account-all-time-cr" not in rga_body, (
                "RENDER-BASIS FAIL: renderGuardAlpha references 'account-all-time-cr'. "
                "The windowed guard-alpha renderer must not touch the account stat."
            )

        # Check updateComparisonRows body.
        ucr_start = js.find("function updateComparisonRows")
        if ucr_start != -1:
            ucr_end = js.find("\n    function ", ucr_start + 1)
            ucr_body = js[ucr_start:ucr_end] if ucr_end != -1 else js[ucr_start : ucr_start + 3000]
            has_testid = "account-all-time-cr" in ucr_body
            has_write = "textContent" in ucr_body
            assert not (has_testid and has_write), (
                "RENDER-BASIS FAIL: updateComparisonRows writes to 'account-all-time-cr'. "
                "The windowed comparison-row renderer must not write the account stat."
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

        writers = _headline_writers(js)
        assert writers, "some function must write #guard-alpha-headline"
        body = "\n".join(b for _n, b in writers)

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

        writers = _headline_writers(js)
        assert writers, "some function must write #guard-alpha-headline"
        body = "\n".join(b for _n, b in writers)

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
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 3000]

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


class TestUpdateComparisonRowsSourcesLifetimeCumulativeReturn:
    """SUPERSEDED 2026-07-21 (DE-DISPLAY-TRUTH-001 / F-014) — the two tests below
    are REWRITTEN, not deleted, to pin the contract that replaced the one this
    class used to check.

    Era-1 (dash-render-fix, 2026-06-04, the class docstring above this note):
    portfolio_strip.cumulative_return carried the ACCOUNT-BASIS all-time if_held
    (~63.95%), and the SSR "Cumulative" row label itself did not yet claim
    "lifetime" — preferring a windowed value for that row was the correct fix
    available at the time (windowing the wrong-basis number was less wrong than
    leaving it alone).

    That era is over. F-014 (this cycle) found the label had since become
    "Cumulative · lifetime" (templates/index.html:920) while this JS still
    clobbered it with a windowed value on every 30s poll — the SAME preference
    this class used to require was BY THEN the bug, not the fix, because
    cumulative_return is now genuinely the VW-basis LIFETIME value (F-018's
    basis-consistency fix + this cycle's F-014 fix together). Preferring
    windowed_cumulative_return under a "lifetime" label silently mislabels a
    windowed figure as lifetime — exactly what F-014 exists to prevent.

    See tests/dashboard/test_display_truth_cluster_js.py
    (TestF014CumulativeLifetimeRowSourcesLifetimeValue) for the RED/GREEN pair
    that drove this fix, and DECISIONS.md's DE-DISPLAY-TRUTH-001 entry for the
    full account-basis-vs-VW-basis history across both eras.
    """

    def test_update_comparison_rows_does_not_prefer_windowed_cumulative_return(self):
        """SUPERSEDED CONTRACT (was: 'must reference windowed_cumulative_return' —
        see class docstring). The cumulative row's SSR label reads "Cumulative ·
        lifetime" — it must source the lifetime cumulative_return only, never a
        windowed value. Preferring windowed_cumulative_return here would silently
        clobber the lifetime-labeled figure with a windowed one on every poll —
        the exact defect F-014 removed."""
        js = _js()

        start = js.find("function updateComparisonRows")
        assert start != -1, "updateComparisonRows must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 3000]

        row_anchor = body.find("id: 'cumulative'")
        assert row_anchor != -1, (
            "the `id: 'cumulative'` row entry was not found in updateComparisonRows "
            "-- this test's location assumptions may be stale; update the markers."
        )
        # Anchor the closing boundary on `higherIsBetter` (which always follows the
        # `values:` expression) rather than the first bare "},"  -- the value
        # expression itself legitimately contains "|| {}," (an empty-dict fallback),
        # which a naive `body.find("},", row_anchor)` matches FIRST, truncating the
        # slice before the real closing brace and silently breaking any assertion
        # that needs the full "|| {}" text.
        hib_anchor = body.find("higherIsBetter", row_anchor)
        assert hib_anchor != -1, (
            "the `higherIsBetter` field was not found after the cumulative row's "
            "`id:` -- this test's location assumptions may be stale; update the markers."
        )
        row_close = body.find("},", hib_anchor)
        row_entry = body[row_anchor : row_close if row_close != -1 else hib_anchor + 100]

        assert "windowed_cumulative_return" not in row_entry, (
            "F-014 REGRESSION: updateComparisonRows' cumulative row once again "
            "prefers ps.windowed_cumulative_return -- this clobbers the "
            "'Cumulative · lifetime'-labeled value (templates/index.html:920) with "
            f"a windowed one on every poll. Row entry: {row_entry!r}"
        )
        assert "ps.cumulative_return" in row_entry, (
            f"F-014 REGRESSION: the cumulative row no longer sources "
            f"ps.cumulative_return at all -- row entry: {row_entry!r}"
        )

    def test_cumulative_row_values_object_sources_cumulative_return_only(self):
        """SUPERSEDED CONTRACT (was: 'must reference BOTH windowed_cumulative_return
        (preferred) and cumulative_return (fallback)' — see class docstring). There
        is no windowed/fallback structure anymore — the row sources
        cumulative_return unconditionally (the `|| {}` guard is the pre-existing
        missing-data fallback, unrelated to windowing)."""
        js = _js()

        start = js.find("function updateComparisonRows")
        assert start != -1, "updateComparisonRows must exist"
        end_marker = js.find("\n    function ", start + 1)
        body = js[start:end_marker] if end_marker != -1 else js[start : start + 3000]

        row_anchor = body.find("id: 'cumulative'")
        assert row_anchor != -1, (
            "the `id: 'cumulative'` row entry was not found in updateComparisonRows "
            "-- this test's location assumptions may be stale; update the markers."
        )
        # Anchor the closing boundary on `higherIsBetter` (which always follows the
        # `values:` expression) rather than the first bare "},"  -- the value
        # expression itself legitimately contains "|| {}," (an empty-dict fallback),
        # which a naive `body.find("},", row_anchor)` matches FIRST, truncating the
        # slice before the real closing brace and silently breaking any assertion
        # that needs the full "|| {}" text.
        hib_anchor = body.find("higherIsBetter", row_anchor)
        assert hib_anchor != -1, (
            "the `higherIsBetter` field was not found after the cumulative row's "
            "`id:` -- this test's location assumptions may be stale; update the markers."
        )
        row_close = body.find("},", hib_anchor)
        row_entry = body[row_anchor : row_close if row_close != -1 else hib_anchor + 100]

        assert "ps.cumulative_return || {}" in row_entry, (
            "F-014 REGRESSION: the cumulative row's `values:` expression must be "
            f"exactly `ps.cumulative_return || {{}}` -- got row entry: {row_entry!r}"
        )
        assert "windowed_cumulative_return" not in row_entry, (
            f"F-014 REGRESSION: a windowed_cumulative_return reference reappeared "
            f"in the cumulative row -- row entry: {row_entry!r}"
        )

    def test_app_portfolio_strip_exposes_windowed_cumulative_return(self):
        """_compute_portfolio_strip (app.py) must expose windowed_cumulative_return in
        the strip dict. Without the server-side field the JS can never read it.
        Source-level: the field name must appear in app.py near the windowed strip logic.
        """
        app_py = (pathlib.Path(__file__).parent.parent.parent / "app.py").read_text(
            encoding="utf-8"
        )

        assert "windowed_cumulative_return" in app_py, (
            "RENDER-BASIS FAIL: app.py does not contain `windowed_cumulative_return`. "
            "_compute_portfolio_strip must store the windowed VW cumulative_return "
            "(from compute_windowed_portfolio_strip) as this field so index.js can "
            "read it for the cumulative comparison row on initial load. "
            "Without this, the initial-load Held will always be account-basis (~63.95%)."
        )


# ---------------------------------------------------------------------------
# Invariants 9 to 12 (the 30d fallback, the frozen-path pin, the function's scope and the
# recursion guard) pinned the old design: the poll wrote the headline and, when its guard alpha
# was null, asked /api/strip/30d from a function named fetchWindowedStrip. The refresh-timing
# dashboard plan replaced it: the poll never writes the headline, a null guard alpha asks for the
# PICKED window's strip, and a strip with a null guard alpha cannot loop because nothing re-requests
# from the answer. Those behaviours are run under node in tests/dashboard/test_dashboard_window_hold.py
# (first poll, null guard alpha, picked window, failed and superseded answers), which replaces the
# source-text pins of function names and call sites.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Invariant 13 — frozen /api/state portfolio_strip must include account_all_time_cr
#
# PM live gate finding (cycle-4, 2026-06-04 @ main 18ac4d9):
#   On the frozen path, `account-all-time-cr` is absent from the rendered DOM even
#   though the value (63.95%) exists in _account_totals_cache.
#
#   Root cause: the frozen _portfolio_strip built at app.py ~line 1262 is a plain
#   5-key dict {today_change, cumulative_return, max_drawdown, account_value,
#   data_as_of} — it never adds account_all_time_cr. _build_meta is then called
#   with this incomplete strip, so meta.portfolio.account_all_time_cr is None, and
#   the template's `{% if _acct_cr is not none %}` guard silently omits the element.
#
#   The live path in _compute_portfolio_strip adds account_all_time_cr at line 833-835:
#       _acct_cr = _account_totals_cache.get("portfolio_cr")
#       if isinstance(_acct_cr, (int, float)):
#           _strip["account_all_time_cr"] = _acct_cr
#   The frozen path must mirror this.
#
#   Required fix (server-side, app.py): after building the frozen _portfolio_strip
#   dict at ~line 1262, add:
#       _acct_cr = _account_totals_cache.get("portfolio_cr")
#       if isinstance(_acct_cr, (int, float)):
#           _portfolio_strip["account_all_time_cr"] = _acct_cr
# ---------------------------------------------------------------------------

# Minimal frozen snapshot for route-level tests — schema-derived from the
# last_market_close_snapshot structure in alpha_bot_execution.py.
# Provenance: PA-CYC4: schema-derived from app.py frozen path (lines 1046-1330)
# and _account_totals_cache population (module-level dict). The snapshot shape
# mirrors frozen_portfolio_strip_populated.json; the _account_totals_cache value
# (portfolio_cr = 63.95) is the specific value the PM observed live on :8090.
_FROZEN_SNAPSHOT_FOR_ACCT_CR = {
    "trading_day": "2026-06-04",
    "captured_at_et": "16:00:01 ET",
    "data_as_of": "16:00 ET",
    "portfolio_strip": {
        "today_change": {"if_held": 0.2, "dry_run": 0.1},
        "cumulative_return": {"if_held": 63.95, "dry_run": 27.56},
        "max_drawdown": {"if_held": -8.1, "dry_run": -5.3},
    },
    "shadow_divergence": {"by_symphony": {}, "portfolio": 0.0},
    "accounts_map": {},
}

_ACCT_CR_LIVE = 63.95  # the value observed in _account_totals_cache on :8090


class TestFrozenPathAccountAllTimeCr:
    """The frozen /api/state portfolio_strip must include account_all_time_cr.

    Without this field, meta.portfolio.account_all_time_cr is None, the template's
    `{% if _acct_cr is not none %}` guard omits the entire element, and the operator
    sees a blank where the all-time stat should be.
    """

    @pytest.fixture()
    def frozen_client(self, monkeypatch):
        """Flask test client wired for the closed_frozen path with a valid snapshot
        and the close totals latched with portfolio_cr = 63.95."""
        import app as app_module

        app_module.app.config["TESTING"] = True

        bot_state = {
            "date": "2026-06-04",
            "last_market_close_snapshot": _FROZEN_SNAPSHOT_FOR_ACCT_CR,
        }

        monkeypatch.setattr(
            app_module, "get_market_state", lambda dt: "closed_frozen", raising=False
        )
        # After the close the frozen strip reads the account totals latched for its
        # snapshot (the first read at or after the capture), never the live cache.
        monkeypatch.setattr(
            app_module,
            "_frozen_account_totals",
            {
                "key": (
                    _FROZEN_SNAPSHOT_FOR_ACCT_CR["trading_day"],
                    _FROZEN_SNAPSHOT_FOR_ACCT_CR["captured_at_et"],
                ),
                "totals": {"portfolio_cr": _ACCT_CR_LIVE},
                "as_of": "2026-06-04 16:00:30 ET",
            },
        )

        with (
            patch.object(app_module, "analytics") as mock_analytics,
            patch.object(app_module, "database") as mock_db,
            patch.object(app_module, "schedule"),
        ):
            mock_db.load_state.return_value = bot_state
            mock_db.get_shadow_divergence.return_value = {
                "by_symphony": {},
                "portfolio_today": None,
            }
            mock_db.get_triggers.return_value = []
            mock_db.normalize_name.side_effect = lambda n: (n or "").lower()
            mock_db.read_fleet_alert.return_value = None

            mock_analytics.get_portfolio_today_change.return_value = {
                "if_held": 0.2,
                "dry_run": 0.1,
            }
            mock_analytics.get_portfolio_cumulative_return.return_value = {
                "if_held": 63.95,
                "dry_run": 27.56,
            }
            mock_analytics.get_portfolio_max_drawdown.return_value = {
                "if_held": -8.1,
                "dry_run": -5.3,
            }
            # This fixture latches portfolio_cr=63.95 with
            # portfolio_tc left unset (CR-warm/TC-absent) — the independent-gating
            # branch (DE-EOD-BASIS-001 review revise) now reaches these two wrap
            # helpers on the frozen path. Stub them so the route doesn't try to
            # JSON-serialize an unstubbed MagicMock; values are consistent with the
            # VW returns above (this test class only asserts on account_all_time_cr,
            # not on today_change/cumulative_return contents).
            mock_analytics.get_portfolio_cumulative_return_account_basis.return_value = {
                "if_held": 63.95,
                "dry_run": 27.56,
            }
            mock_analytics.get_portfolio_today_change_account_basis.return_value = {
                "if_held": 0.2,
                "dry_run": 0.1,
            }
            mock_analytics.compute_windowed_portfolio_strip.return_value = {
                "guard_alpha": 0.904,
                "window": "30d",
                "cumulative_return": {"if_held": 26.65, "dry_run": 27.56},
                "today_change": {"if_held": 0.2, "dry_run": 0.1},
                "max_drawdown": {"if_held": -8.1, "dry_run": -5.3},
                "vol_bot": None,
                "vol_held": None,
            }
            mock_analytics.get_portfolio_bot_and_held_daily_returns.return_value = None

            with app_module.app.test_client() as client:
                yield client

    def test_frozen_api_state_portfolio_strip_has_account_all_time_cr(self, frozen_client):
        """GET /api/state on the closed_frozen path must include account_all_time_cr
        in portfolio_strip. Without it, _build_meta sets meta.portfolio.account_all_time_cr
        to None and the template omits the element entirely.

        Current failure: the frozen _portfolio_strip dict (app.py ~line 1262) is built
        as a plain 5-key dict — it never reads _account_totals_cache["portfolio_cr"].
        """
        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        body = resp.get_json()
        ps = body.get("portfolio_strip")

        assert ps is not None, (
            "FROZEN-ACCT-CR FAIL: portfolio_strip is None on closed_frozen path. "
            "The frozen path must build a portfolio_strip dict."
        )
        assert "account_all_time_cr" in ps, (
            "FROZEN-ACCT-CR FAIL: portfolio_strip.account_all_time_cr is absent on "
            "the closed_frozen path. _build_meta reads this field to set "
            "meta.portfolio.account_all_time_cr; when absent, the template's "
            "`{% if _acct_cr is not none %}` guard omits the account-all-time-cr element "
            "from the DOM entirely. "
            "Fix: in app.py after building the frozen _portfolio_strip (~line 1280), add:\n"
            "    _acct_cr = _account_totals_cache.get('portfolio_cr')\n"
            "    if isinstance(_acct_cr, (int, float)):\n"
            "        _portfolio_strip['account_all_time_cr'] = _acct_cr\n"
            f"Got portfolio_strip keys: {sorted(ps.keys()) if isinstance(ps, dict) else ps!r}"
        )

    def test_frozen_api_state_portfolio_strip_account_all_time_cr_value(self, frozen_client):
        """The account_all_time_cr in portfolio_strip must equal the value from
        _account_totals_cache['portfolio_cr'] — the Composer account-lifetime CR.
        It must be a float, not None or a string.
        """
        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200

        body = resp.get_json()
        ps = body.get("portfolio_strip") or {}
        val = ps.get("account_all_time_cr")

        assert isinstance(val, (int, float)), (
            f"FROZEN-ACCT-CR FAIL: portfolio_strip.account_all_time_cr must be a "
            f"numeric value (float/int). Got {val!r} (type {type(val).__name__}). "
            "The value must come from _account_totals_cache['portfolio_cr'] — "
            "the Composer account-lifetime simple_return scalar."
        )
        assert abs(val - _ACCT_CR_LIVE) < 0.01, (
            f"FROZEN-ACCT-CR FAIL: portfolio_strip.account_all_time_cr = {val!r} "
            f"does not match _account_totals_cache['portfolio_cr'] = {_ACCT_CR_LIVE}. "
            "The frozen path must read the value from the cache, not fabricate it."
        )

    def test_frozen_api_state_meta_portfolio_has_account_all_time_cr(self, frozen_client):
        """meta.portfolio.account_all_time_cr must be present and numeric on the
        closed_frozen path. This is what the SSR template reads to render the
        `account-all-time-cr` element. When absent, the element is silently omitted.

        _build_meta propagates portfolio_strip.account_all_time_cr into
        meta.portfolio.account_all_time_cr via `ps.get('account_all_time_cr')` —
        so fixing portfolio_strip also fixes meta.portfolio, which fixes the SSR render.
        """
        resp = frozen_client.get("/api/state")
        assert resp.status_code == 200

        body = resp.get_json()
        meta = body.get("meta") or {}
        portfolio = meta.get("portfolio") or {}
        val = portfolio.get("account_all_time_cr")

        assert val is not None, (
            "FROZEN-ACCT-CR FAIL: meta.portfolio.account_all_time_cr is None/absent "
            "on the closed_frozen path. The SSR template reads this field at "
            "templates/index.html:808 (`{% set _acct_cr = _portfolio.get('account_all_time_cr') %}`). "
            "When None, the `{% if _acct_cr is not none %}` guard omits the entire "
            "account-all-time-cr DOM element — the operator sees a blank. "
            "Fix the frozen _portfolio_strip to include account_all_time_cr; "
            "_build_meta will then propagate it into meta.portfolio automatically."
        )
        assert isinstance(val, (int, float)), (
            f"FROZEN-ACCT-CR FAIL: meta.portfolio.account_all_time_cr = {val!r} is not numeric. "
            "Expected a float matching _account_totals_cache['portfolio_cr']."
        )

    def test_frozen_path_account_all_time_cr_does_not_regress_open_path(self, monkeypatch):
        """The fix to the frozen path must not regress the live (open) path.
        On market open, _compute_portfolio_strip already sets account_all_time_cr —
        that path must continue to work.

        Source contract: app.py must contain `account_all_time_cr` in the context
        of both the live portfolio strip AND the frozen path enrichment.
        """
        app_py = (pathlib.Path(__file__).parent.parent.parent / "app.py").read_text(
            encoding="utf-8"
        )

        # The live path already sets this (line 835):
        #   _strip["account_all_time_cr"] = _acct_cr
        # After the fix, there must be a SECOND assignment on the frozen path.
        # Count assignments to account_all_time_cr (excluding comments and docstrings).
        import re as _re

        assignments = _re.findall(
            r'["\'_]account_all_time_cr["\']\s*\]\s*=',
            app_py,
        )
        assert len(assignments) >= 2, (
            "FROZEN-ACCT-CR FAIL: app.py contains fewer than 2 assignments to "
            f"`account_all_time_cr` (found {len(assignments)}). "
            "The live path already assigns it once (_compute_portfolio_strip ~line 835). "
            "The frozen path needs a second assignment (after the _portfolio_strip dict "
            "is built at ~line 1262). "
            "Fix: add `_portfolio_strip['account_all_time_cr'] = _acct_cr` on the frozen path."
        )
