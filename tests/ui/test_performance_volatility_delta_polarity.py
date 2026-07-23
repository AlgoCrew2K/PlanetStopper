"""RED tests -- Math Remediation R0, AC-7 (feature-plans/math-r0.md).

DE-MATH-AUDIT-001 / VERDICT.md ma-perf-findings.md MAPERF-05 (MEDIUM --
misleading quality signal; the numbers themselves are correct):

  static/performance.js's raw "Annualized volatility" table row colors its
  delta green/up-arrow whenever `shadow - live > 0` (performance.js:83,
  `fmtDelta`/`deltaClass`), with NO per-metric inversion. For volatility,
  LOWER is better -- so when the bot is MORE volatile than if-held
  (shadow_vol > live_vol, i.e. shadow - live > 0), the row renders GREEN,
  the "good" color, exactly backwards. The DERIVED "Volatility reduction" row
  (performance.js:316, `live.volatility - shadow.volatility`) is correct; the
  raw row contradicts it on the same table. Contrast: the dashboard Risk
  Profile panel (static/index.js:466-479) already has the correct pattern --
  a per-row `invertDelta` flag threaded into the delta-color decision
  (`deltaGood = invert ? (delta <= 0) : (delta >= 0)`) -- and the hero vol row
  (index.js:945) is also correct.

  AC-7: performance.js's volatility delta must render with correct polarity
  (bot MORE volatile = negative/red), reusing the existing invertDelta pattern
  from index.js.

Matches this repo's established JS-render-truth pattern (source-inspection
regex on the raw file -- see tests/ui/test_cycle_3_performance.py::
test_performance_js_headline_stats_color_coded) rather than executing JS.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
PERF_JS = PROJECT_ROOT / "static" / "performance.js"
INDEX_JS = PROJECT_ROOT / "static" / "index.js"


def _metric_labels_source(content: str) -> str:
    match = re.search(r"var\s+METRIC_LABELS\s*=\s*\[(.*?)\];", content, re.DOTALL)
    assert match is not None, "METRIC_LABELS array literal not found in performance.js"
    return match.group(1)


def _volatility_row_source(metric_labels_src: str) -> str:
    """Extract the single-line array-literal entry for the 'volatility' key
    (NOT 'volatility_delta' -- anchor on the exact key followed by a comma so
    the two don't collide)."""
    match = re.search(r"\[\s*'volatility'\s*,.*?\]\s*,", metric_labels_src)
    assert match is not None, (
        "the 'volatility' row was not found inside METRIC_LABELS -- "
        f"array source:\n{metric_labels_src}"
    )
    return match.group(0)


def _render_metrics_function_body(content: str) -> str:
    match = re.search(
        r"function\s+renderMetrics\s*\([^)]*\)\s*\{(.+?)(?=\n\s*function\s|\Z)",
        content,
        re.DOTALL,
    )
    assert match is not None, "renderMetrics function not found in performance.js"
    return match.group(1)


# ===========================================================================
# RED-10 -- METRIC_LABELS' volatility row must carry an invert marker.
# ===========================================================================


def test_ac7_volatility_row_carries_an_invert_flag():
    """The METRIC_LABELS entry for 'volatility' must be a 5-tuple whose 5th
    element marks it as an invert-polarity metric (lower is better) -- mirrors
    the [label, botVal, heldVal, kind, invertDelta] shape already used by
    index.js's Risk Profile panel (index.js:466-474).

    MUST FAIL at 98901abf: the volatility row is
    ['volatility', 'Annualized volatility', 'pct_frac', false] -- a 4-tuple,
    no invert marker."""
    content = PERF_JS.read_text(encoding="utf-8")
    metric_labels_src = _metric_labels_source(content)
    volatility_row = _volatility_row_source(metric_labels_src)

    # Count top-level comma-separated elements inside the row's brackets.
    inner = volatility_row.strip().rstrip(",").strip()
    assert inner.startswith("[") and inner.endswith("]"), (
        f"unexpected volatility row shape: {volatility_row!r}"
    )
    elements = [e.strip() for e in inner[1:-1].split(",")]
    assert len(elements) >= 5, (
        f"the 'volatility' METRIC_LABELS row has only {len(elements)} elements "
        f"({elements}) -- AC-7 requires a 5th element marking it as an "
        "invert-polarity metric (lower volatility is better), mirroring "
        "index.js's [label, botVal, heldVal, kind, invertDelta] pattern"
    )
    # The 5th element must be a truthy invert marker, not another 'false'
    # placeholder unrelated to polarity (e.g. accidentally duplicating isPrimary).
    assert elements[4].strip().lower() == "true", (
        f"the 'volatility' row's 5th element is {elements[4]!r} -- AC-7 requires "
        "it to be the invert-polarity marker set to true (lower volatility is better)"
    )


# ===========================================================================
# RED-11 -- renderMetrics must thread the invert flag into the delta color.
# ===========================================================================


def test_ac7_render_metrics_applies_invert_flag_to_delta_color():
    """renderMetrics must not compute the delta color via the raw, unconditional
    deltaClass(liveVal, shadowVal) call alone for an invert-flagged row -- it
    must consult the row's invert marker (spec[4]) the same way index.js's
    Risk Profile panel does (`deltaGood = invert ? (delta <= 0) : (delta >= 0)`).

    MUST FAIL at 98901abf: renderMetrics destructures only
    `key, label, kind, isPrimary = spec[0..3]` and calls
    `deltaClass(liveVal, shadowVal)` unconditionally -- no invert branch exists."""
    content = PERF_JS.read_text(encoding="utf-8")
    fn_body = _render_metrics_function_body(content)

    # The function must read a 5th spec element (the invert flag).
    reads_invert_element = bool(re.search(r"spec\s*\[\s*4\s*\]", fn_body))
    assert reads_invert_element, (
        "renderMetrics does not read spec[4] (the invert-polarity flag) -- "
        "it must destructure and use the 5th METRIC_LABELS element for the "
        "delta-color decision. Current body:\n" + fn_body
    )

    # The color decision must branch on the invert flag, mirroring index.js's
    # `invert ? (delta <= 0) : (delta >= 0)` pattern -- i.e. there must be a
    # conditional comparison using '<= 0' somewhere near the color logic (the
    # inverted branch), not just the unconditional '> 0'/'< 0' deltaClass calls.
    has_inverted_comparison = bool(re.search(r"<=\s*0", fn_body))
    assert has_inverted_comparison, (
        "renderMetrics has no inverted-polarity comparison (e.g. 'delta <= 0') "
        "for invert-flagged rows -- the volatility row's delta color is still "
        "computed identically to a normal (higher-is-better) metric"
    )


# ===========================================================================
# Self-guard -- index.js's ALREADY-CORRECT invertDelta pattern must not
# regress while r0-fe fixes performance.js.
# ===========================================================================


def test_selfguard_index_js_risk_profile_invert_pattern_is_present():
    """index.js's Risk Profile panel (the reference implementation AC-7 mirrors)
    must keep its invert-aware delta-color logic -- this must PASS both before
    and after the GREEN fix; it is NOT part of this cycle's scope."""
    content = INDEX_JS.read_text(encoding="utf-8")
    assert "invertDelta" in content or re.search(r"invert\s*\)\s*\?", content), (
        "index.js's invertDelta pattern (the Risk Profile panel reference "
        "implementation AC-7 is modeled on) appears to have been removed or "
        "renamed -- this file must not touch index.js at all"
    )
    assert re.search(r"deltaGood\s*=\s*invert\s*\?", content), (
        "index.js's `deltaGood = invert ? (delta <= 0) : (delta >= 0)` pattern "
        "(index.js:479) must remain present unchanged"
    )
