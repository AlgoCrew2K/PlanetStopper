"""
Tests pinning the 2026-07 "Numeric Verification" overlay redesign (commit ef9bba1,
"feat(ui): redesign AI-Advisor Numeric Verification as verdict-first summary"),
built on top of DE-PRISM-NUMERIC-VERIFY-001.

Scope: this file is ADDITIVE to tests/app/test_ai_advisor_tab_verification_overlay.py
(untouched — all 9 existing tests still pass unmodified) and pins ONLY the new
redesign surface:

  1. `_build_verification_count_line(summary)` (app.py:3744) — the new testable
     formatting helper, unit-tested directly with summary dicts, plus a hypothesis
     property test for the "flagged/overridden segment present iff nonzero" invariant.
  2. Clean case (25 pass / 2 unverifiable / 0 flagged / 0 overridden, verdict="clean"):
     the overlay renders the verdict pill + count line and ZERO top-level actionable
     badges; the <details> disclosure holds all 27 checks as styled pills (including
     the 2 unverifiable ones as PILLS, not bare text — the bug this redesign fixed).
  3. Actionable case (synthesized: 23 pass / 2 unverifiable / 1 flagged / 1 overridden,
     verdict="overrides-detected" per advisors/prism_numeric_verifier.py's
     _derive_verdict priority order — overridden beats flagged beats clean): the
     flagged + overridden checks render as top-level badges WITH their
     "council cited X; source says Y" annotation; pass/unverifiable indicators are
     never promoted to top level; the count line appends "1 flagged · 1 overridden".
  4. Bonus: the render guard now fires whenever a VERIFICATION row exists at all
     (not only when `checks` is non-empty) — pins a real behavior change called out
     in the ef9bba1 commit message.
  5. `_build_verdict_display(verdict)` (app.py, /review PR #92 fix, commit 8688bbf)
     — the honest-verdict-fallback helper: a recognized verdict renders its label
     verbatim with the matching (deduped) check-badge class; a falsy verdict
     renders ("unknown", unverifiable-class); an unrecognized/future verdict
     string renders ITS OWN raw string verbatim — never silently coerced to the
     misleading "no-numeric-claims" label.
  6. Route-level: a VERIFICATION row with `raw_response.verdict` missing/None
     renders '>unknown<' on the verdict pill (the F1 correctness fix) — the old
     Jinja default would have rendered the misleading '>no-numeric-claims<'.
  7. Macro-equivalence guard (/review PR #92's `render_verify_check` extraction):
     a flagged/overridden check present in BOTH `actionable_checks` and the full
     `checks` list renders via BOTH macro call sites — both testid families and
     the annotation text (appearing twice, once per site) are still produced.

Why these are meaningful — verified via `git show ef9bba1^:templates/ai_advisor.html`,
`git show ef9bba1^:app.py`, and `git show 8688bbf^:app.py` (the pre-redesign and
pre-dedup states respectively) — every test below would FAIL against the
relevant prior commit:
  - The old (pre-ef9bba1) template's guard was `{% if market_prism_verification
    and market_prism_verification.get('checks') %}`, with a single `{% for %}`
    over ALL checks rendering EVERY pass/unverifiable/flagged/overridden entry
    as a top-level `data-testid="prism-verify-check-{indicator}"` badge. There
    was no `prism-verify-verdict`, `prism-verify-count-line`, `prism-verify-
    summary`, or `prism-verify-details` testid anywhere in the old template —
    every assertion on those testids below fails outright (content simply
    absent) against old HTML.
  - The old (pre-ef9bba1) app.py had no `_build_verification_count_line`
    function at all (the `from app import _build_verification_count_line` in
    every helper test below raises ImportError against that commit), and
    `market_prism_verification` never carried `count_line`/`actionable_checks`.
  - Test 2's "zero top-level actionable badges for a clean run" is the direct
    inverse of the old behavior (old design rendered all 27 as top-level badges,
    so `_TOP_LEVEL_CHECK_TESTID_RE.findall(html)` would return 27 matches, not []).
  - Test 3's "pass/unverifiable never promoted to top level" fails against old
    design, which promoted every classification to a top-level badge.
  - Test 4's "overlay renders even with empty checks" fails against the old guard,
    which short-circuits (renders nothing) whenever `checks` is falsy.
  - The old (pre-8688bbf) app.py had no `_build_verdict_display` function at all
    (ImportError against ef9bba1) — section 5's tests fail outright.
  - Section 6's route test fails against ef9bba1's Jinja default
    (`market_prism_verification.get('verdict') or 'no-numeric-claims'`), which
    renders '>no-numeric-claims<' for a null verdict, not '>unknown<'.
  - Section 7's macro-equivalence test is a REGRESSION/equivalence guard, not a
    new-behavior pin — it is expected to pass against both ef9bba1 and 8688bbf
    (the refactor is explicitly behavior-preserving for these two testid
    families); its value is catching a FUTURE regression in the shared macro.

DB isolation: N/A — route-level test, DB accessors are mocked (mirrors
test_ai_advisor_tab_verification_overlay.py exactly). tests/conftest.py's
session-level guard already redirects DB_PATH to an isolated temp file before
any module import.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Flask test client
# ---------------------------------------------------------------------------


@pytest.fixture
def client():
    import app as app_module

    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


# ---------------------------------------------------------------------------
# Shared fixture data (production-shape) — mirrors the _MARKET_PRISM_ROW in
# test_ai_advisor_tab_verification_overlay.py.
# ---------------------------------------------------------------------------

_RUN_ID = "efefefef-efef-efef-efef-efefefefefef"

_MARKET_PRISM_ROW = {
    "id": 800,
    "advisor_role": "MARKET_PRISM",
    "subject_id": "",
    "verdict": "neutral",
    "created_at": "2026-07-06 03:00:00",
    "raw_response": {
        "run_id": _RUN_ID,
        "run_ts": _RUN_ID,
        "overall_sentiment": "neutral",
        "sentiment_rationale": "Redesign overlay test.",
        "cited_numbers": [
            {"indicator": "VIX", "value": 22.0, "lens": "derivatives"},
        ],
        "per_lens_digest": {
            "derivatives": {"available": True, "summary": "VIX near 22", "sources": []},
        },
    },
}


def _make_checks(
    n_pass: int, n_unverifiable: int, n_flagged: int = 0, n_overridden: int = 0
) -> list[dict]:
    """Build a real-shaped `checks` list (indicator/lens/cited_value/
    ground_truth_value/classification — matches advisors/prism_numeric_verifier.py's
    _build_check output shape) with the given counts of each classification.
    """
    checks: list[dict] = []
    for i in range(n_pass):
        checks.append(
            {
                "indicator": f"PASS_{i}",
                "lens": "derivatives",
                "cited_value": 10.0 + i,
                "ground_truth_value": 10.0 + i,
                "classification": "pass",
            }
        )
    for i in range(n_unverifiable):
        checks.append(
            {
                "indicator": f"UNVER_{i}",
                "lens": "macro",
                "cited_value": None,
                "ground_truth_value": None,
                "classification": "unverifiable",
            }
        )
    for i in range(n_flagged):
        checks.append(
            {
                "indicator": f"FLAG_{i}",
                "lens": "sentiment",
                "cited_value": 5.0 + i,
                "ground_truth_value": 5.4 + i,
                "classification": "flagged",
            }
        )
    for i in range(n_overridden):
        checks.append(
            {
                "indicator": f"OVER_{i}",
                "lens": "fundamentals",
                "cited_value": 7.0 + i,
                "ground_truth_value": 3.0 + i,
                "classification": "overridden",
            }
        )
    return checks


def _make_verification_row(checks: list[dict], verdict: str) -> dict:
    """Assemble a MARKET_PRISM_VERIFICATION DB row whose summary counts are
    derived FROM the checks list (never hand-typed alongside it), so the fixture
    can never drift out of sync with its own checks."""
    n_pass = sum(1 for c in checks if c["classification"] == "pass")
    n_unverifiable = sum(1 for c in checks if c["classification"] == "unverifiable")
    n_flagged = sum(1 for c in checks if c["classification"] == "flagged")
    n_overridden = sum(1 for c in checks if c["classification"] == "overridden")
    return {
        "id": 801,
        "advisor_role": "MARKET_PRISM_VERIFICATION",
        "subject_id": "global",
        "verdict": None,
        "created_at": "2026-07-06 03:05:00",
        "raw_response": {
            "run_id": _RUN_ID,
            "verified_at": "2026-07-06T03:05:00Z",
            "checks": checks,
            "summary": {
                "n_checks": len(checks),
                "n_pass": n_pass,
                "n_flagged": n_flagged,
                "n_overridden": n_overridden,
                "n_unverifiable": n_unverifiable,
            },
            "verdict": verdict,
        },
    }


# Real 27-check payload shape (25 pass / 2 unverifiable / 0 flagged / 0 overridden)
# — the "clean" case per the brief.
_CLEAN_CHECKS = _make_checks(n_pass=25, n_unverifiable=2)
_VERIFICATION_ROW_CLEAN = _make_verification_row(_CLEAN_CHECKS, verdict="clean")

# Synthesized actionable case: 23 pass / 2 unverifiable / 1 flagged / 1 overridden
# (still 27 checks total). verdict="overrides-detected" matches the real
# _derive_verdict priority (overridden > flagged > clean) in
# advisors/prism_numeric_verifier.py.
_ACTIONABLE_CHECKS = _make_checks(n_pass=23, n_unverifiable=2, n_flagged=1, n_overridden=1)
_VERIFICATION_ROW_ACTIONABLE = _make_verification_row(
    _ACTIONABLE_CHECKS, verdict="overrides-detected"
)

# Matches a top-level `prism-verify-check-{indicator}` testid but NOT the
# `<details>` disclosure's `prism-verify-check-full-{indicator}` variant.
_TOP_LEVEL_CHECK_TESTID_RE = re.compile(r'data-testid="prism-verify-check-(?!full-)([^"]*)"')
_FULL_CHECK_TESTID_RE = re.compile(r'data-testid="prism-verify-check-full-([^"]*)"')


def _get_ai_advisor(client, verification_row):
    with (
        patch("database.get_latest_market_prism_summary", return_value=_MARKET_PRISM_ROW),
        patch("database.get_latest_market_prism_sources_for_run", return_value=None),
        patch(
            "database.get_latest_market_prism_verification_for_run",
            return_value=verification_row,
            create=True,
        ),
    ):
        return client.get("/ai-advisor")


# ---------------------------------------------------------------------------
# 1. _build_verification_count_line(summary) — direct unit tests
# ---------------------------------------------------------------------------


def test_count_line_is_empty_string_when_zero_checks():
    from app import _build_verification_count_line

    summary = {"n_checks": 0, "n_pass": 0, "n_unverifiable": 0, "n_flagged": 0, "n_overridden": 0}
    assert _build_verification_count_line(summary) == ""


def test_count_line_is_empty_string_when_n_checks_key_absent():
    """A summary dict missing 'n_checks' entirely (the _empty_summary() shape
    behind a no-numeric-claims/no-verifiable-claims verdict) must also format
    as '' — not raise a KeyError."""
    from app import _build_verification_count_line

    assert _build_verification_count_line({}) == ""


def test_count_line_clean_case_shows_pass_and_unverifiable_only():
    from app import _build_verification_count_line

    summary = _VERIFICATION_ROW_CLEAN["raw_response"]["summary"]
    line = _build_verification_count_line(summary)

    assert line == f"{summary['n_pass']} verified · {summary['n_unverifiable']} unverifiable", (
        f"unexpected count line for the clean 25/2 case: {line!r}"
    )
    assert "flagged" not in line and "overridden" not in line, (
        "zero flagged/overridden counts must be omitted entirely from the count "
        f"line, not rendered as '0 flagged'/'0 overridden': {line!r}"
    )


def test_count_line_actionable_case_appends_flagged_and_overridden():
    from app import _build_verification_count_line

    summary = _VERIFICATION_ROW_ACTIONABLE["raw_response"]["summary"]
    line = _build_verification_count_line(summary)

    expected = (
        f"{summary['n_pass']} verified · {summary['n_unverifiable']} unverifiable"
        f" · {summary['n_flagged']} flagged · {summary['n_overridden']} overridden"
    )
    assert line == expected, f"unexpected count line for the actionable 23/2/1/1 case: {line!r}"


def test_count_line_appends_only_flagged_when_overridden_is_zero():
    from app import _build_verification_count_line

    summary = {"n_checks": 5, "n_pass": 3, "n_unverifiable": 1, "n_flagged": 1, "n_overridden": 0}
    line = _build_verification_count_line(summary)

    assert line.endswith("1 flagged"), f"expected trailing '1 flagged' segment: {line!r}"
    assert "overridden" not in line, f"n_overridden=0 must not appear as '0 overridden': {line!r}"


def test_count_line_appends_only_overridden_when_flagged_is_zero():
    from app import _build_verification_count_line

    summary = {"n_checks": 5, "n_pass": 3, "n_unverifiable": 1, "n_flagged": 0, "n_overridden": 1}
    line = _build_verification_count_line(summary)

    assert line.endswith("1 overridden"), f"expected trailing '1 overridden' segment: {line!r}"
    assert "flagged" not in line, f"n_flagged=0 must not appear as '0 flagged': {line!r}"


@given(
    n_pass=st.integers(min_value=0, max_value=200),
    n_unverifiable=st.integers(min_value=0, max_value=200),
    n_flagged=st.integers(min_value=0, max_value=200),
    n_overridden=st.integers(min_value=0, max_value=200),
)
def test_count_line_flagged_overridden_segments_present_iff_nonzero(
    n_pass, n_unverifiable, n_flagged, n_overridden
):
    """Property (invariant, not a fixture value): for ANY summary with at least
    one check, the count line contains a ' flagged' segment if and only if
    n_flagged > 0, and an ' overridden' segment if and only if n_overridden > 0 —
    never a spurious '0 flagged'/'0 overridden', and never a silently dropped
    nonzero one. Also: n_pass and n_unverifiable are always present verbatim."""
    from app import _build_verification_count_line

    n_checks = n_pass + n_unverifiable + n_flagged + n_overridden
    if n_checks == 0:
        return  # covered by test_count_line_is_empty_string_when_zero_checks
    summary = {
        "n_checks": n_checks,
        "n_pass": n_pass,
        "n_unverifiable": n_unverifiable,
        "n_flagged": n_flagged,
        "n_overridden": n_overridden,
    }
    line = _build_verification_count_line(summary)

    assert (" flagged" in line) == (n_flagged > 0), (
        f"n_flagged={n_flagged} but ' flagged' presence mismatch in {line!r}"
    )
    assert (" overridden" in line) == (n_overridden > 0), (
        f"n_overridden={n_overridden} but ' overridden' presence mismatch in {line!r}"
    )
    assert str(n_pass) in line, f"n_pass={n_pass} not found in count line {line!r}"
    assert str(n_unverifiable) in line, (
        f"n_unverifiable={n_unverifiable} not found in count line {line!r}"
    )


# ---------------------------------------------------------------------------
# 2. Clean case: verdict pill + count line, ZERO top-level actionable badges,
#    all 27 checks folded into <details> as styled pills.
# ---------------------------------------------------------------------------


def test_clean_case_renders_verdict_pill_and_count_line(client):
    resp = _get_ai_advisor(client, _VERIFICATION_ROW_CLEAN)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'data-testid="prism-verify-verdict"' in html
    assert ">clean<" in html, (
        f"verdict pill must render the literal verdict text 'clean'. "
        f"HTML snippet (first 3000 chars): {html[:3000]!r}"
    )

    summary = _VERIFICATION_ROW_CLEAN["raw_response"]["summary"]
    expected_line = f"{summary['n_pass']} verified · {summary['n_unverifiable']} unverifiable"
    assert 'data-testid="prism-verify-count-line"' in html
    assert expected_line in html, f"expected count line {expected_line!r} not found in HTML"


def test_clean_case_renders_zero_top_level_actionable_badges(client):
    """A clean run (0 flagged, 0 overridden) must render ZERO top-level
    `prism-verify-check-{indicator}` badges — the 25 passes and 2 unverifiables
    are folded into the count line, not individually surfaced (this is the whole
    point of the redesign)."""
    resp = _get_ai_advisor(client, _VERIFICATION_ROW_CLEAN)
    html = resp.get_data(as_text=True)

    top_level_matches = _TOP_LEVEL_CHECK_TESTID_RE.findall(html)
    assert top_level_matches == [], (
        "a clean run (0 flagged, 0 overridden) must render ZERO top-level "
        f"prism-verify-check-{{indicator}} badges; found: {top_level_matches!r}"
    )


def test_clean_case_details_holds_all_27_checks_as_styled_pills(client):
    resp = _get_ai_advisor(client, _VERIFICATION_ROW_CLEAN)
    html = resp.get_data(as_text=True)

    assert 'data-testid="prism-verify-details"' in html
    n_checks = len(_CLEAN_CHECKS)
    assert f"Show all {n_checks} checks" in html

    full_matches = _FULL_CHECK_TESTID_RE.findall(html)
    assert len(full_matches) == n_checks, (
        f"expected {n_checks} prism-verify-check-full-* pills inside <details>, "
        f"found {len(full_matches)}: {full_matches!r}"
    )

    # Count the rendered class ATTRIBUTE usage, not a bare substring — the page's
    # <style> block also contains a `.prism-verify-badge--unverifiable {` CSS
    # selector rule, which would inflate a naive `html.count(...)` by one.
    n_unverifiable = sum(1 for c in _CLEAN_CHECKS if c["classification"] == "unverifiable")
    unverifiable_pill_count = html.count(
        'class="prism-verify-badge prism-verify-badge--unverifiable"'
    )
    assert unverifiable_pill_count == n_unverifiable, (
        "each unverifiable check inside the <details> full list must render as a "
        "styled prism-verify-badge--unverifiable PILL, not bare unstyled text (the "
        f"bug this redesign fixed); expected {n_unverifiable} occurrences, found "
        f"{unverifiable_pill_count}"
    )


# ---------------------------------------------------------------------------
# 3. Actionable case: flagged/overridden checks render as top-level badges with
#    annotation; pass/unverifiable never promoted; count line reflects all four
#    counts.
# ---------------------------------------------------------------------------


def test_actionable_case_flagged_and_overridden_render_as_top_level_badges_with_annotation(
    client,
):
    resp = _get_ai_advisor(client, _VERIFICATION_ROW_ACTIONABLE)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    flagged_chk = next(c for c in _ACTIONABLE_CHECKS if c["classification"] == "flagged")
    overridden_chk = next(c for c in _ACTIONABLE_CHECKS if c["classification"] == "overridden")

    assert f'data-testid="prism-verify-check-{flagged_chk["indicator"]}"' in html
    assert f'data-testid="prism-verify-check-{overridden_chk["indicator"]}"' in html

    for chk in (flagged_chk, overridden_chk):
        annotation = f"council cited {chk['cited_value']}; source says {chk['ground_truth_value']}"
        assert annotation in html, (
            f"actionable check {chk['indicator']!r} ({chk['classification']}) must carry "
            f"the 'council cited X; source says Y' annotation: expected {annotation!r}. "
            f"HTML snippet (first 3000 chars): {html[:3000]!r}"
        )

    top_level_matches = set(_TOP_LEVEL_CHECK_TESTID_RE.findall(html))
    assert top_level_matches == {flagged_chk["indicator"], overridden_chk["indicator"]}, (
        "only the flagged + overridden indicators may render as top-level badges "
        f"(pass/unverifiable stay folded); found: {top_level_matches!r}"
    )


def test_actionable_case_pass_and_unverifiable_indicators_not_promoted_to_top_level(client):
    """A pass/unverifiable indicator must never render as a top-level actionable
    badge, even when OTHER checks in the same row are flagged/overridden — it
    should only appear inside <details> as `-full-{indicator}`."""
    resp = _get_ai_advisor(client, _VERIFICATION_ROW_ACTIONABLE)
    html = resp.get_data(as_text=True)

    pass_chk = next(c for c in _ACTIONABLE_CHECKS if c["classification"] == "pass")
    unverifiable_chk = next(c for c in _ACTIONABLE_CHECKS if c["classification"] == "unverifiable")

    for chk in (pass_chk, unverifiable_chk):
        assert f'data-testid="prism-verify-check-{chk["indicator"]}"' not in html, (
            f"a {chk['classification']} check ({chk['indicator']!r}) must not be promoted "
            "to a top-level actionable badge just because the row also has flagged/"
            "overridden checks."
        )
        assert f'data-testid="prism-verify-check-full-{chk["indicator"]}"' in html, (
            f"a {chk['classification']} check ({chk['indicator']!r}) must still appear "
            "inside the <details> full list."
        )


def test_actionable_case_count_line_appends_flagged_and_overridden(client):
    resp = _get_ai_advisor(client, _VERIFICATION_ROW_ACTIONABLE)
    html = resp.get_data(as_text=True)

    summary = _VERIFICATION_ROW_ACTIONABLE["raw_response"]["summary"]
    expected_line = (
        f"{summary['n_pass']} verified · {summary['n_unverifiable']} unverifiable"
        f" · {summary['n_flagged']} flagged · {summary['n_overridden']} overridden"
    )
    assert 'data-testid="prism-verify-count-line"' in html
    assert expected_line in html, f"expected count line {expected_line!r} not found in HTML"


# ---------------------------------------------------------------------------
# 4. Bonus: render guard now fires whenever a VERIFICATION row exists at all
#    (not only when `checks` is non-empty) — real behavior change called out
#    in the ef9bba1 commit message.
# ---------------------------------------------------------------------------


def test_overlay_renders_even_when_checks_list_is_empty(client):
    """The old guard was `{% if market_prism_verification and
    market_prism_verification.get('checks') %}` — falsy `checks` suppressed the
    whole overlay. The redesigned guard (`is not none`) must render the verdict
    pill even for a no-numeric-claims row with zero checks, so that verdict
    state is visible instead of silently rendering nothing."""
    empty_row = _make_verification_row([], verdict="no-numeric-claims")
    resp = _get_ai_advisor(client, empty_row)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'data-testid="prism-verification"' in html, (
        "the overlay container must render even when checks is empty — the old "
        "design's guard (`and market_prism_verification.get('checks')`) suppressed "
        "the whole block in this case."
    )
    assert 'data-testid="prism-verify-verdict"' in html
    assert ">no-numeric-claims<" in html


# ---------------------------------------------------------------------------
# 5. _build_verdict_display(verdict) -> (label, css_class) — /review PR #92's
#    honest-verdict-fallback fix (commit 8688bbf). Direct unit tests.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "verdict,expected_label,expected_class",
    [
        ("clean", "clean", "prism-verify-badge--pass"),
        ("flags-detected", "flags-detected", "prism-verify-badge--flagged"),
        ("overrides-detected", "overrides-detected", "prism-verify-badge--overridden"),
        ("no-verifiable-claims", "no-verifiable-claims", "prism-verify-badge--unverifiable"),
        ("no-numeric-claims", "no-numeric-claims", "prism-verify-badge--unverifiable"),
    ],
)
def test_verdict_display_recognized_verdict_renders_label_verbatim_with_mapped_class(
    verdict, expected_label, expected_class
):
    from app import _build_verdict_display

    label, css_class = _build_verdict_display(verdict)
    assert label == expected_label, f"expected label {expected_label!r}, got {label!r}"
    assert css_class == expected_class, f"expected css_class {expected_class!r}, got {css_class!r}"


@pytest.mark.parametrize("falsy_verdict", [None, ""])
def test_verdict_display_falsy_verdict_renders_unknown_unverifiable(falsy_verdict):
    from app import _build_verdict_display

    label, css_class = _build_verdict_display(falsy_verdict)
    assert label == "unknown", f"a falsy verdict ({falsy_verdict!r}) must render label 'unknown'"
    assert css_class == "prism-verify-badge--unverifiable"


def test_verdict_display_unrecognized_verdict_renders_raw_string_not_coerced():
    """F1 (/review PR #92): a future/unrecognized verdict string must render its
    OWN raw string verbatim as the label — never silently coerced to the
    misleading 'no-numeric-claims' label, which would falsely assert 'nothing
    was checked' when the data doesn't say that at all.

    RED intent: would FAIL against 8688bbf^ two ways — the helper doesn't exist
    yet (ImportError), and the old Jinja `.get(_verdict, 'prism-verify-badge--
    no-numeric-claims')` map (pre-dedup) plus the `or 'no-numeric-claims'`
    default would have coerced an unrecognized string's CSS class (not its
    label) toward the no-numeric-claims styling.
    """
    from app import _build_verdict_display

    label, css_class = _build_verdict_display("some-future-verdict")
    assert label == "some-future-verdict", (
        f"an unrecognized verdict must render its own raw string verbatim as the "
        f"label — got {label!r}"
    )
    assert label != "no-numeric-claims", (
        "an unrecognized verdict must NOT be coerced to the 'no-numeric-claims' label"
    )
    assert css_class == "prism-verify-badge--unverifiable", (
        "an unrecognized verdict must fall back to the neutral unverifiable-class pill"
    )


# ---------------------------------------------------------------------------
# 6. Route-level: a VERIFICATION row with a missing/None verdict renders
#    '>unknown<' on the verdict pill (the F1 correctness fix, /review PR #92).
#    Old design's Jinja default (`... or 'no-numeric-claims'`) would have
#    rendered the misleading '>no-numeric-claims<' label instead.
# ---------------------------------------------------------------------------


def test_route_renders_unknown_label_when_verdict_is_none(client):
    row_null_verdict = _make_verification_row([], verdict=None)
    resp = _get_ai_advisor(client, row_null_verdict)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert 'data-testid="prism-verify-verdict"' in html
    assert ">unknown<" in html, (
        "a null verdict must render the honest 'unknown' label on the verdict pill "
        f"(F1 fix). HTML snippet (first 3000 chars): {html[:3000]!r}"
    )
    assert ">no-numeric-claims<" not in html, (
        "the OLD design's Jinja default (`market_prism_verification.get('verdict') or "
        "'no-numeric-claims'`) coerced a null verdict to the misleading "
        "'no-numeric-claims' label — this must no longer happen."
    )


# ---------------------------------------------------------------------------
# 7. Macro-equivalence guard (/review PR #92's render_verify_check extraction):
#    a check present in BOTH actionable_checks and the full checks list must
#    render via BOTH macro call sites — the top-level actionable testid AND
#    the <details> full-list testid — with its annotation appearing at both.
# ---------------------------------------------------------------------------


def test_actionable_case_flagged_and_overridden_appear_in_both_actionable_and_full_lists(client):
    """A flagged/overridden check is a member of BOTH `actionable_checks` (top-
    level list) and `checks` (the <details> full list) — the shared
    render_verify_check macro must be called for it from both sites, producing
    both testid families and rendering the annotation text at both (the
    top-level copy carries the prism-verify-annotation testid; the <details>
    copy does not, per `show_annotation_testid=False` at that call site — same
    as the pre-extraction behavior)."""
    resp = _get_ai_advisor(client, _VERIFICATION_ROW_ACTIONABLE)
    html = resp.get_data(as_text=True)

    flagged_chk = next(c for c in _ACTIONABLE_CHECKS if c["classification"] == "flagged")
    overridden_chk = next(c for c in _ACTIONABLE_CHECKS if c["classification"] == "overridden")

    for chk in (flagged_chk, overridden_chk):
        assert f'data-testid="prism-verify-check-{chk["indicator"]}"' in html, (
            f"{chk['indicator']!r} must render via the top-level actionable macro call site"
        )
        assert f'data-testid="prism-verify-check-full-{chk["indicator"]}"' in html, (
            f"{chk['indicator']!r} must ALSO render inside <details> via the full-list "
            "macro call site — the macro extraction must not have dropped either site."
        )
        annotation = f"council cited {chk['cited_value']}; source says {chk['ground_truth_value']}"
        assert html.count(annotation) == 2, (
            f"the annotation for {chk['indicator']!r} must appear exactly twice — once "
            f"from each macro call site (top-level + <details>); found "
            f"{html.count(annotation)} occurrences. HTML snippet (first 3000 chars): "
            f"{html[:3000]!r}"
        )
