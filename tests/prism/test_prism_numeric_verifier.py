"""
RED tests for advisors.prism_numeric_verifier (DE-PRISM-NUMERIC-VERIFY-001).

The module does not exist yet — feature-plans/prism-numeric-verifier.md (Status: ready,
15 ACs). These tests define the contract for verify_cited_numbers(run_id, market_prism_row,
lens_sections=None) -> dict, the _INDICATOR_REGISTRY, and persist_verification(run_id,
result) -> int | None.

Import strategy: _import_verifier() is called INSIDE each test body (never at module
level), so a missing module produces one clean per-test failure (ModuleNotFoundError)
rather than a whole-file collection error — mirrors tests/ai_advisor/test_cycle4_lens_pipeline.py's
_import_pipeline() pattern. RED-for-the-right-reason on HEAD = ModuleNotFoundError for
every test in this file; that is the expected/correct RED state (the module is new).

Contract choices made here (test-writer defines the interface the implementer conforms to,
per the plan's Architecture section):
  - Each check dict in result["checks"] carries: indicator, lens, cited_value,
    ground_truth_value, classification (one of pass/flagged/overridden/unverifiable).
  - result carries: checks (list), summary (dict), verdict (str).
  - _INDICATOR_REGISTRY[indicator] is a 4-tuple (lens, payload_path, comparison_type,
    tolerance) per the plan's Architecture section (line "_INDICATOR_REGISTRY: dict[str, tuple]").

No hardcoded producer-computed values: boundary tests derive the exact boundary offset
from the module's OWN named constants (_INDICATOR_REGISTRY tolerance, _OVERRIDE_FACTOR)
rather than an assumed literal — this is the only way to test an inclusive boundary
without guessing the implementer's chosen tolerance magnitude. The 21.8/22.0 and 22/18.1
VIX examples and the 4.3/4.0 UNRATE example are lifted verbatim from the plan's own
Testing Strategy section (not invented) — they are worked examples IN the spec.

DB isolation: conftest._isolate_db autouse fixture redirects DB_PATH per test.
Run protocol: targeted -n0 only (tests/prism -n0 -o addopts= -p no:xdist). Never full suite.
"""

from __future__ import annotations

import copy
import importlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_RUN_ID = "verifier-test-run-0001"


def _import_verifier():
    """Import advisors.prism_numeric_verifier; raises ModuleNotFoundError pre-GREEN."""
    if "advisors.prism_numeric_verifier" in sys.modules:
        importlib.reload(sys.modules["advisors.prism_numeric_verifier"])
    return importlib.import_module("advisors.prism_numeric_verifier")


# ---------------------------------------------------------------------------
# Shared fixture builders (production-shape lens payloads, ai_advisor.py-confirmed)
# ---------------------------------------------------------------------------


def _make_market_prism_row(cited_numbers: list[dict], run_id: str = _RUN_ID) -> dict:
    """Build a MARKET_PRISM advisor_observations-shaped row with cited_numbers."""
    return {
        "id": 1,
        "advisor_role": "MARKET_PRISM",
        "subject_id": "",
        "verdict": "neutral",
        "raw_response": {
            "run_id": run_id,
            "run_ts": run_id,
            "overall_sentiment": "neutral",
            "sentiment_rationale": "Test synthesis.",
            "cited_numbers": cited_numbers,
        },
    }


def _make_market_prism_row_no_cited_numbers(run_id: str = _RUN_ID) -> dict:
    """Legacy/degraded-shape row — no cited_numbers key at all (AC-11)."""
    return {
        "id": 2,
        "advisor_role": "MARKET_PRISM",
        "subject_id": "",
        "verdict": "neutral",
        "raw_response": {
            "run_id": run_id,
            "run_ts": run_id,
            "overall_sentiment": "neutral",
            "sentiment_rationale": "Legacy row, no cited_numbers.",
        },
    }


# Full 5-lens payload set — shapes confirmed at:
#   derivatives: ai_advisor.py:755-765   macro: ai_advisor.py:900-905
#   sentiment:   ai_advisor.py:673-684   technicals: ai_advisor.py:542-552
#   fundamentals: ai_advisor.py:1242-1253
_FULL_LENS_SECTIONS: dict = {
    "derivatives": {
        "lens": "derivatives",
        "available": True,
        "payload": {
            "vix_level": 22.0,
            "vix_term_structure": {
                "spot": 22.0,
                "term_3m": 23.1,
                "ratio": 0.952,
                "spread": -1.1,
                "regime": "contango",
            },
            "risk_read": "neutral",
            "as_of_date": "2026-07-01",
        },
        "sources": [],
    },
    "macro": {
        "lens": "macro",
        "available": True,
        "payload": {
            "series": {
                "DGS10": {"label": "10-Year Treasury", "value": "4.35", "date": "2026-07-01"},
                "UNRATE": {"label": "Unemployment Rate", "value": "4.0", "date": "2026-06-01"},
                "CPIAUCSL": {"label": "CPI", "value": "314.2", "date": "2026-06-01"},
                "FEDFUNDS": {"label": "Fed Funds Rate", "value": "5.25", "date": "2026-06-01"},
            }
        },
        "sources": [],
    },
    "sentiment": {
        "lens": "sentiment",
        "available": True,
        "payload": {"tone_score": 0.10, "corpus": [], "events": [], "article_count": 5},
        "sources": [],
    },
    "technicals": {
        "lens": "technicals",
        "available": True,
        "payload": {
            "ma_posture": {},
            "breadth": 0.58,
            # 20d-return truths for the full 10-ticker proxy universe
            # (lens_technicals._PROXY_UNIVERSE) — DE-PRISM-MOMENTUM-REGISTRY-001.
            "momentum": {
                "SPY": -0.0124,
                "QQQ": 0.0562,
                "IWM": -0.0310,
                "EFA": 0.0080,
                "AGG": -0.0015,
                "GLD": 0.0450,
                "XLF": 0.0120,
                "XLE": -0.0670,
                "XLV": 0.0030,
                "XLI": 0.0210,
            },
        },
        "sources": [],
    },
    "fundamentals": {
        "lens": "fundamentals",
        "available": True,
        "payload": {
            "tickers": {
                "AAPL": {
                    "entity_name": "Apple Inc.",
                    "cik": "0000320193",
                    "key_facts": {
                        "Revenues": {
                            "label": "Revenue",
                            "value": 391000000000,
                            "unit": "USD",
                            "end": "2025-09-30",
                            "filed": "2025-11-01",
                            "form": "10-K",
                        },
                    },
                },
            },
            "coverage": {"available": 1, "universe": 1},
        },
        "sources": [],
    },
}


def _unavailable_lens(lens: str, reason: str = "StubError") -> dict:
    return {"lens": lens, "available": False, "reason": reason, "payload": None, "sources": []}


# ===========================================================================
# TestClassification — AC-5 / AC-6
# ===========================================================================


class TestClassification:
    def test_pass_within_tolerance(self):
        """Plan's own worked example (Testing Strategy): cited VIX 21.8, truth 22.0 -> pass."""
        mod = _import_verifier()
        row = _make_market_prism_row([{"indicator": "VIX", "value": 21.8, "lens": "derivatives"}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["checks"][0]["classification"] == "pass", (
            f"AC-5: cited 21.8 vs truth 22.0 (VIX) must classify as pass; "
            f"got {result['checks'][0]!r}"
        )

    def test_flagged_outside_tolerance_bounded(self):
        """Plan's own worked example: cited UNRATE 4.3, truth 4.0 (bounded) -> flagged."""
        mod = _import_verifier()
        row = _make_market_prism_row([{"indicator": "UNRATE", "value": 4.3, "lens": "macro"}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["checks"][0]["classification"] == "flagged", (
            f"AC-6: cited 4.3 vs truth 4.0 (UNRATE) must classify as flagged (bounded "
            f"mismatch, prose kept as-is); got {result['checks'][0]!r}"
        )

    def test_overridden_gross_mismatch(self):
        """Plan's own worked example: cited VIX 22, truth 18.1 -> overridden."""
        mod = _import_verifier()
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["derivatives"]["payload"]["vix_level"] = 18.1
        row = _make_market_prism_row([{"indicator": "VIX", "value": 22.0, "lens": "derivatives"}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        check = result["checks"][0]
        assert check["classification"] == "overridden", (
            f"AC-6: cited 22 vs truth 18.1 (VIX) is a gross mismatch -> overridden; got {check!r}"
        )
        assert check.get("ground_truth_value") == pytest.approx(18.1, abs=1e-6), (
            "AC-6: an overridden check must record the ground-truth value for render "
            f"preference; got {check.get('ground_truth_value')!r}"
        )

    def test_classification_boundary_exactly_at_tolerance_is_pass(self):
        """AC-5 boundary: abs(cited-truth) == tolerance (not < ) must still be pass.

        Derives the exact boundary offset from the module's own tolerance constant
        for VIX (never a guessed literal) so the test is valid regardless of the
        implementer's chosen tolerance magnitude.
        """
        mod = _import_verifier()
        _lens, _path, _cmp, tolerance = mod._INDICATOR_REGISTRY["VIX"]
        truth = 22.0
        cited = truth - tolerance  # exactly at the inclusive boundary
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["derivatives"]["payload"]["vix_level"] = truth
        row = _make_market_prism_row([{"indicator": "VIX", "value": cited, "lens": "derivatives"}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "pass", (
            f"AC-5 spec says 'pass iff abs(cited-truth) <= tolerance' (inclusive). "
            f"At exactly tolerance={tolerance!r}, classification must be pass; "
            f"got {result['checks'][0]!r}"
        )

    def test_classification_boundary_exactly_at_override_factor_is_flagged(self):
        """AC-6 boundary: abs(diff) == _OVERRIDE_FACTOR * tolerance must be flagged, not overridden.

        'bounded (<= _OVERRIDE_FACTOR x tolerance) -> flagged' is the plan's own wording —
        the boundary itself must land on flagged (inclusive), not overridden.
        """
        mod = _import_verifier()
        _lens, _path, _cmp, tolerance = mod._INDICATOR_REGISTRY["VIX"]
        truth = 22.0
        cited = truth - (mod._OVERRIDE_FACTOR * tolerance)
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["derivatives"]["payload"]["vix_level"] = truth
        row = _make_market_prism_row([{"indicator": "VIX", "value": cited, "lens": "derivatives"}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "flagged", (
            f"AC-6: at exactly _OVERRIDE_FACTOR({mod._OVERRIDE_FACTOR!r}) x "
            f"tolerance({tolerance!r}), classification must be flagged (inclusive upper "
            f"bound of the bounded-mismatch band), not overridden; got "
            f"{result['checks'][0]!r}"
        )

    def test_relative_tolerance_used_for_large_magnitude_fundamentals(self):
        """AC-5: fundamentals dollar figures use RELATIVE tolerance, not absolute.

        A $500M diff on a $391B revenue figure (~0.13% relative) must pass even though
        $500M would gate as a gross mismatch under any sane absolute tolerance — this
        proves relative tolerance is actually applied for large-magnitude indicators,
        not just documented.
        """
        mod = _import_verifier()
        truth = 391_000_000_000
        cited = 391_500_000_000  # +500M, ~0.128% relative
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["fundamentals"]["payload"]["tickers"]["AAPL"]["key_facts"]["Revenues"][
            "value"
        ] = truth
        row = _make_market_prism_row(
            [{"indicator": "AAPL.Revenues", "value": cited, "lens": "fundamentals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "pass", (
            f"AC-5: a 0.13%-relative diff on a $391B figure must pass under relative "
            f"tolerance; got {result['checks'][0]!r}. If this is overridden/flagged, "
            "the implementation is using absolute tolerance for a large-magnitude "
            "indicator instead of relative."
        )


# ===========================================================================
# TestRegistry — AC-3
# ===========================================================================


class TestRegistry:
    @pytest.mark.parametrize(
        "indicator,lens",
        [
            ("VIX", "derivatives"),
            ("VIXCLS", "derivatives"),
            ("VXVCLS", "derivatives"),
            ("VIX3M", "derivatives"),
            ("DGS10", "macro"),
            ("10Y", "macro"),
            ("UNRATE", "macro"),
            ("CPIAUCSL", "macro"),
            ("CPI", "macro"),
            ("FEDFUNDS", "macro"),
            ("tone", "sentiment"),
            ("breadth", "technicals"),
            ("momentum_SPY_20d", "technicals"),
            ("momentum_QQQ_20d", "technicals"),
            ("momentum_IWM_20d", "technicals"),
            ("momentum_EFA_20d", "technicals"),
            ("momentum_AGG_20d", "technicals"),
            ("momentum_GLD_20d", "technicals"),
            ("momentum_XLF_20d", "technicals"),
            ("momentum_XLE_20d", "technicals"),
            ("momentum_XLV_20d", "technicals"),
            ("momentum_XLI_20d", "technicals"),
        ],
    )
    def test_documented_indicator_never_resolves_unverifiable_when_source_available(
        self, indicator, lens
    ):
        """AC-3: every indicator named in the plan's registry list must resolve to SOME
        real classification when its source lens is available — never a silent
        unverifiable from a missing registry entry. (The cited value 1.0 is deliberately
        likely-wrong for most indicators — the point is classification != unverifiable,
        not which of pass/flagged/overridden it lands on.)
        """
        mod = _import_verifier()
        row = _make_market_prism_row([{"indicator": indicator, "value": 1.0, "lens": lens}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["checks"][0]["classification"] != "unverifiable", (
            f"AC-3: indicator {indicator!r} (documented in the plan's registry list) "
            f"resolved to unverifiable even though its source lens ({lens!r}) is "
            f"available — the registry is missing this indicator. Got "
            f"{result['checks'][0]!r}"
        )

    def test_ticker_concept_indicator_resolves_via_fundamentals_payload(self):
        """AC-3: '<TICKER>.<concept>' resolves via fundamentals.tickers.<T>.key_facts.<C>.value."""
        mod = _import_verifier()
        row = _make_market_prism_row(
            [{"indicator": "AAPL.Revenues", "value": 1.0, "lens": "fundamentals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["checks"][0]["classification"] != "unverifiable", (
            "AC-3: 'AAPL.Revenues' must resolve via the fundamentals payload's "
            f"tickers.AAPL.key_facts.Revenues.value path; got {result['checks'][0]!r}"
        )

    def test_unmapped_indicator_resolves_unverifiable(self):
        """AC-3: an indicator with no registry entry (e.g. a derived '2s10s_yoy' the
        council invented) must resolve to unverifiable, never a silent pass."""
        mod = _import_verifier()
        row = _make_market_prism_row(
            [{"indicator": "2s10s_yoy_inflation_pct", "value": 1.5, "lens": "macro"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["checks"][0]["classification"] == "unverifiable", (
            f"AC-3: an indicator with no registry mapping must be unverifiable; "
            f"got {result['checks'][0]!r}"
        )

    @pytest.mark.parametrize(
        "indicator",
        [
            "VIX",
            "VIXCLS",
            "VXVCLS",
            "VIX3M",
            "DGS10",
            "10Y",
            "UNRATE",
            "CPIAUCSL",
            "CPI",
            "FEDFUNDS",
            "tone",
            "breadth",
            "momentum_SPY_20d",
            "momentum_QQQ_20d",
            "momentum_IWM_20d",
            "momentum_EFA_20d",
            "momentum_AGG_20d",
            "momentum_GLD_20d",
            "momentum_XLF_20d",
            "momentum_XLE_20d",
            "momentum_XLV_20d",
            "momentum_XLI_20d",
        ],
    )
    def test_registry_comparison_type_is_absolute_for_non_fundamentals_indicators(self, indicator):
        """AC-5 sufficiency (PM sufficiency review, post-GREEN): every non-fundamentals
        indicator's registry entry must use "absolute" comparison_type, never "relative".

        Only the fundamentals wildcard (dollar-magnitude figures) should use "relative"
        (AC-5's own worked example — see test_relative_tolerance_used_for_large_magnitude_
        fundamentals). A "relative" mixup on e.g. a rate/VIX/tone/breadth indicator would
        silently widen or narrow its effective tolerance depending on the ground-truth
        magnitude — direct registry introspection catches this regardless of the specific
        truth/cited values a behavioral test happens to pick.
        """
        mod = _import_verifier()
        _lens, _path, comparison_type, _tolerance = mod._INDICATOR_REGISTRY[indicator]
        assert comparison_type == "absolute", (
            f"AC-5: indicator {indicator!r} must use comparison_type='absolute' — only "
            f"the fundamentals <TICKER>.<CONCEPT> wildcard uses 'relative'. Got "
            f"{comparison_type!r}."
        )

    def test_registry_comparison_type_is_relative_for_fundamentals_wildcard(self):
        """AC-5: the fundamentals wildcard entry (large-magnitude dollar figures) must
        use comparison_type='relative' — a fixed absolute tolerance would either falsely
        flag routine rounding on a $391B figure or pass a real error on a $1M one."""
        mod = _import_verifier()
        registry = mod._INDICATOR_REGISTRY
        wildcard_entries = [
            entry
            for key, entry in registry.items()
            if key
            not in (
                "VIX",
                "VIXCLS",
                "VXVCLS",
                "VIX3M",
                "DGS10",
                "10Y",
                "UNRATE",
                "CPIAUCSL",
                "CPI",
                "FEDFUNDS",
                "tone",
                "breadth",
                "momentum_SPY_20d",
                "momentum_QQQ_20d",
                "momentum_IWM_20d",
                "momentum_EFA_20d",
                "momentum_AGG_20d",
                "momentum_GLD_20d",
                "momentum_XLF_20d",
                "momentum_XLE_20d",
                "momentum_XLV_20d",
                "momentum_XLI_20d",
            )
        ]
        assert wildcard_entries, (
            "Expected a registry entry beyond the 22 literal indicators (the "
            "fundamentals <TICKER>.<CONCEPT> wildcard) — none found."
        )
        for entry in wildcard_entries:
            _lens, _path, comparison_type, _tolerance = entry
            assert comparison_type == "relative", (
                f"AC-5: the fundamentals wildcard registry entry must use "
                f"comparison_type='relative'; got {comparison_type!r} for entry {entry!r}"
            )


# ===========================================================================
# TestMomentumRegistryExpansion — DE-PRISM-MOMENTUM-REGISTRY-001
#
# Extends _INDICATOR_REGISTRY to cover the technicals "momentum" family: the
# council cites momentum_<TICKER>_20d (confirmed live for SPY/QQQ/XLV/XLF) but
# the verifier previously had no registry entry for any of them, so every such
# citation silently resolved "unverifiable" — a blind spot in the anti-
# fabrication guard. This adds one literal entry per proxy-universe ticker
# (SPY, QQQ, IWM, EFA, AGG, GLD, XLF, XLE, XLV, XLI — lens_technicals.py's own
# _PROXY_UNIVERSE), payload_path "momentum.<TICKER>", comparison_type
# "absolute", tolerance = new named constant _MOMENTUM_TOLERANCE.
#
# TOLERANCE GROUNDING (documented here so the rationale is auditable, not
# just asserted):
#   - comparison_type is "absolute", NOT "relative". Momentum is a naturally
#     bounded 20d-return fraction (~+/-0.15), the same family as `breadth`
#     (also absolute) — not a large-magnitude dollar figure like fundamentals.
#     Relative would be actively WRONG here for a second reason beyond scale:
#     _classify()'s relative branch divides by abs(truth), and momentum
#     legitimately sits near zero (e.g. 0.0003) — a relative tolerance would
#     make trivial rounding noise near zero look like a huge relative error.
#     See test_momentum_absolute_tolerance_correct_near_zero below.
#   - Magnitude (_MOMENTUM_TOLERANCE = 0.001): live council citations round to
#     ~4 decimals (e.g. -0.0124, 0.1102), so honest rounding noise tops out at
#     0.00005 (half the last digit) — 0.001 gives 20x headroom over that floor.
#   - Data-flow grounding (verified against prism_scheduler.py:560-657, not
#     assumed): the ground truth the verifier compares against is a POST-
#     council re-fetch (`_fetch_lens_sections()`, prism_scheduler.py:560),
#     not literally the same payload object the 6-agent council read at
#     synthesis time — the council gets its own market data independently,
#     before this patch-time re-fetch runs. This re-fetch is safe for momentum
#     specifically because momentum and breadth are computed inside the SAME
#     `_build_technicals_section()` call, from the SAME Alpaca daily-bar fetch
#     — identical code path, identical granularity. A completed trading day's
#     daily bar is immutable once posted, so two calls to that same code path
#     within one overnight window return identical values (unlike `tone`,
#     which genuinely drifts because GDELT's rolling artlist window moves
#     between council-time and verify-time — hence tone's deliberately wider
#     tolerance). This is why 0.001 only needs to absorb citation-rounding
#     noise, not re-fetch drift.
#   - Adversarial anchor: a hallucinated -0.02 cited against a true -0.0124
#     (diff 0.0076) must NOT pass — this is what rules out a looser guessed
#     tolerance like 0.01 (which would wrongly pass it). See
#     test_momentum_near_miss_hallucination_is_not_pass.
# ===========================================================================

_MOMENTUM_TICKERS = (
    "SPY",
    "QQQ",
    "IWM",
    "EFA",
    "AGG",
    "GLD",
    "XLF",
    "XLE",
    "XLV",
    "XLI",
)


class TestMomentumRegistryExpansion:
    def test_momentum_ticker_outside_proxy_universe_is_unmapped(self):
        """A ticker NOT in the 10-ticker proxy universe (e.g. TSLA) must resolve
        unverifiable — these are literal registry entries, not a wildcard regex
        (unlike the fundamentals <TICKER>.<CONCEPT> shape), so an unregistered
        ticker is never silently matched."""
        mod = _import_verifier()
        row = _make_market_prism_row(
            [{"indicator": "momentum_TSLA_20d", "value": 0.05, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["checks"][0]["classification"] == "unverifiable", (
            "momentum_TSLA_20d (outside the 10-ticker proxy universe) must be "
            f"unverifiable, not silently matched by a wildcard; got {result['checks'][0]!r}"
        )

    def test_registry_grows_by_exactly_ten_momentum_entries(self):
        """Regression pin: the 10 momentum entries are additive — the original 12
        literal indicators + 1 fundamentals wildcard (13 keys) must still be
        present and untouched; registry totals exactly 23 keys."""
        mod = _import_verifier()
        original_keys = {
            "VIX",
            "VIXCLS",
            "VXVCLS",
            "VIX3M",
            "DGS10",
            "10Y",
            "UNRATE",
            "CPIAUCSL",
            "CPI",
            "FEDFUNDS",
            "tone",
            "breadth",
            mod._FUNDAMENTALS_WILDCARD_KEY,
        }
        assert original_keys.issubset(mod._INDICATOR_REGISTRY.keys()), (
            "Adding the momentum entries must not remove/rename any of the "
            f"original 13 registry keys; missing: "
            f"{original_keys - mod._INDICATOR_REGISTRY.keys()!r}"
        )
        assert len(mod._INDICATOR_REGISTRY) == 23, (
            "Expected 13 original keys + 10 momentum keys == 23 total; got "
            f"{len(mod._INDICATOR_REGISTRY)}: {sorted(mod._INDICATOR_REGISTRY.keys())!r}"
        )

    def test_momentum_correctly_rounded_citation_passes(self):
        """PASS-side anchor: the council cites momentum at ~4-decimal precision
        (confirmed live: -0.0124, 0.1102). A citation that is the payload's true
        value rounded to 4 decimals must pass — the only legitimate source of
        cited-vs-truth diff for an honest citation is the council's own
        rounding, never a tolerance-driven false flag."""
        mod = _import_verifier()
        truth = -0.012419283  # unrounded internal float, as _compute_momentum would produce
        cited = round(truth, 4)  # -0.0124, matches the live citation precision
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["technicals"]["payload"]["momentum"]["SPY"] = truth
        row = _make_market_prism_row(
            [{"indicator": "momentum_SPY_20d", "value": cited, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "pass", (
            f"A 4-decimal-rounded honest citation ({cited!r} of truth {truth!r}) "
            f"must pass; got {result['checks'][0]!r}"
        )

    def test_momentum_boundary_exactly_at_tolerance_is_pass(self):
        """AC-5 boundary, mirrors the VIX pattern: abs(cited-truth) == tolerance
        (inclusive) must still be pass. Derived from the module's own tolerance
        constant, never a guessed literal."""
        mod = _import_verifier()
        _lens, _path, _cmp, tolerance = mod._INDICATOR_REGISTRY["momentum_SPY_20d"]
        truth = -0.0124
        cited = truth - tolerance
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["technicals"]["payload"]["momentum"]["SPY"] = truth
        row = _make_market_prism_row(
            [{"indicator": "momentum_SPY_20d", "value": cited, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "pass", (
            f"At exactly tolerance={tolerance!r}, classification must be pass; "
            f"got {result['checks'][0]!r}"
        )

    def test_momentum_boundary_exactly_at_override_factor_is_flagged(self):
        """AC-6 boundary, mirrors the VIX pattern: abs(diff) == _OVERRIDE_FACTOR *
        tolerance must be flagged (inclusive upper bound), not overridden."""
        mod = _import_verifier()
        _lens, _path, _cmp, tolerance = mod._INDICATOR_REGISTRY["momentum_SPY_20d"]
        truth = -0.0124
        cited = truth - (mod._OVERRIDE_FACTOR * tolerance)
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["technicals"]["payload"]["momentum"]["SPY"] = truth
        row = _make_market_prism_row(
            [{"indicator": "momentum_SPY_20d", "value": cited, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "flagged", (
            f"At exactly _OVERRIDE_FACTOR({mod._OVERRIDE_FACTOR!r}) x "
            f"tolerance({tolerance!r}), classification must be flagged, not "
            f"overridden; got {result['checks'][0]!r}"
        )

    def test_momentum_flagged_bounded_mismatch(self):
        """Middle tier: a diff strictly between tolerance and _OVERRIDE_FACTOR *
        tolerance must be flagged (bounded mismatch), not overridden — mirrors
        the plan's own UNRATE 4.3-vs-4.0 worked example for this indicator
        family."""
        mod = _import_verifier()
        _lens, _path, _cmp, tolerance = mod._INDICATOR_REGISTRY["momentum_SPY_20d"]
        truth = 0.0110
        cited = truth + (2 * tolerance)  # strictly inside (tolerance, 3*tolerance]
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["technicals"]["payload"]["momentum"]["SPY"] = truth
        row = _make_market_prism_row(
            [{"indicator": "momentum_SPY_20d", "value": cited, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "flagged", (
            f"A bounded mismatch (diff={abs(cited - truth)!r}, tolerance="
            f"{tolerance!r}) must be flagged; got {result['checks'][0]!r}"
        )

    def test_momentum_near_miss_hallucination_is_not_pass(self):
        """THE CRUX adversarial case: a hallucinated citation that is numerically
        CLOSE to the truth (-0.02 cited vs -0.0124 actual, diff 0.0076) must
        still NOT pass. This is the exact case that rules out a looser guessed
        tolerance (e.g. 0.01, under which this would wrongly pass) — the anti-
        fabrication guard must not be so loose that a plausible-looking wrong
        number slides through as a correct citation."""
        mod = _import_verifier()
        truth = -0.0124
        cited = -0.02  # diff 0.0076 — "close" in magnitude but a real mismatch
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["technicals"]["payload"]["momentum"]["SPY"] = truth
        row = _make_market_prism_row(
            [{"indicator": "momentum_SPY_20d", "value": cited, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        check = result["checks"][0]
        assert check["classification"] != "pass", (
            f"A hallucinated citation ({cited!r}) close to but meaningfully off "
            f"from the truth ({truth!r}, diff={abs(cited - truth)!r}) must not "
            f"pass; got {check!r}"
        )
        assert check["classification"] == "overridden", (
            f"At _MOMENTUM_TOLERANCE=0.001, diff=0.0076 is > 3x tolerance (0.003) "
            f"-> overridden is the expected tier; got {check!r}"
        )

    def test_momentum_gross_hallucination_overridden(self):
        """A grossly wrong citation (council cites 0.05 when the payload says
        -0.0124, diff 0.0624) -> overridden (gross mismatch), and the ground-
        truth value is recorded for render preference."""
        mod = _import_verifier()
        truth = -0.0124
        cited = 0.05
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["technicals"]["payload"]["momentum"]["SPY"] = truth
        row = _make_market_prism_row(
            [{"indicator": "momentum_SPY_20d", "value": cited, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        check = result["checks"][0]
        assert check["classification"] == "overridden", (
            f"cited {cited!r} vs truth {truth!r} (diff {abs(cited - truth)!r}) is "
            f"a gross mismatch -> overridden; got {check!r}"
        )
        assert check.get("ground_truth_value") == pytest.approx(truth, abs=1e-6), (
            "An overridden check must record the ground-truth value for render "
            f"preference; got {check.get('ground_truth_value')!r}"
        )

    def test_momentum_absolute_tolerance_correct_near_zero(self):
        """Design-rationale regression: momentum legitimately sits near zero
        (e.g. a flat 20d return). A correctly-rounded citation of a near-zero
        truth must still pass under the ABSOLUTE tolerance — proves the choice
        of comparison_type='absolute' (not 'relative') is load-bearing: under
        'relative', _classify() divides by abs(truth), and a tiny rounding diff
        against a near-zero truth would spuriously blow up to a huge relative
        error and misclassify honest rounding as a mismatch."""
        mod = _import_verifier()
        truth = 0.00031415  # near-zero, plausible flat-momentum reading
        cited = round(truth, 4)  # 0.0003
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["technicals"]["payload"]["momentum"]["AGG"] = truth
        row = _make_market_prism_row(
            [{"indicator": "momentum_AGG_20d", "value": cited, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "pass", (
            f"A near-zero truth ({truth!r}) with an honestly-rounded citation "
            f"({cited!r}) must pass under absolute tolerance; got "
            f"{result['checks'][0]!r}"
        )

    def test_momentum_comparison_type_is_absolute(self):
        """Direct registry introspection (mirrors the existing non-fundamentals
        comparison-type test): every momentum_<TICKER>_20d entry must use
        comparison_type='absolute', matching breadth/VIX/tone/rates — never
        'relative' (only the fundamentals dollar-magnitude wildcard uses
        that)."""
        mod = _import_verifier()
        for ticker in _MOMENTUM_TICKERS:
            indicator = f"momentum_{ticker}_20d"
            _lens, _path, comparison_type, _tolerance = mod._INDICATOR_REGISTRY[indicator]
            assert comparison_type == "absolute", (
                f"{indicator!r} must use comparison_type='absolute'; got "
                f"{comparison_type!r}"
            )


# ===========================================================================
# TestProseVsTupleResidualGap — D-1 residual-gap, plan Scope Boundaries "OUT"
# ===========================================================================


class TestProseVsTupleResidualGap:
    def test_prose_only_number_produces_no_check_declared_tuples_only(self):
        """Plan Scope Boundaries (OUT): "Full NLP prose-extraction of numbers not
        declared in cited_numbers ... is OUT; mitigated by the prompt rule only."

        This test turns that documented, accepted limitation into a permanent
        regression guard: a number stated in prose (sentiment_rationale) but absent
        from cited_numbers must produce NO check for that indicator — the verifier's
        coverage is "declared tuples only", never a silent assumption of full prose
        coverage. If a future dev adds prose-scanning, this test's expectation
        changes deliberately (not by accident).
        """
        mod = _import_verifier()
        row = {
            "id": 1,
            "advisor_role": "MARKET_PRISM",
            "subject_id": "",
            "verdict": "neutral",
            "raw_response": {
                "run_id": _RUN_ID,
                "run_ts": _RUN_ID,
                "overall_sentiment": "neutral",
                "sentiment_rationale": "VIX is 22, a calm print given the macro backdrop.",
                "cited_numbers": [],  # analyst stated VIX in prose but did NOT cite it
            },
        }
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["checks"] == [], (
            "The verifier must only check DECLARED cited_numbers tuples — a number "
            "stated only in prose (sentiment_rationale here) and absent from "
            f"cited_numbers must produce zero checks. Got {result['checks']!r}. "
            "(This is the plan's own documented D-1 residual-gap, mitigated only by "
            "the analyst/synthesizer prompt rule — not by prose NLP extraction.)"
        )


# ===========================================================================
# TestMalformedCitedNumbersContainer — nvreview Finding 1 (code-fix RED)
#
# A truthy-but-malformed cited_numbers value (not a list, or a list of non-dict/
# keyless entries) must never silently produce verdict="clean" — that is a silent
# pass in an anti-fabrication feature (a "clean" verdict implies "checked and all
# passed"; zero real checks were performed). Contract pinned here, not implementation
# shape: non-list container -> a verdict distinct from "clean" (e.g. no-numeric-claims
# or an explicit malformed-input verdict); a list containing non-dict/keyless entries
# -> each entry surfaces as an "unverifiable" check (never a silent drop) and the
# overall verdict is not "clean".
# ===========================================================================


class TestMalformedCitedNumbersContainer:
    def test_dict_shaped_cited_numbers_container_verdict_not_clean(self):
        """nvreview Finding 1: cited_numbers = {"indicator": "VIX"} (a dict, not a
        list) must not silently produce verdict="clean".

        RED on current code: list({"indicator": "VIX"}) yields ["indicator"] (the
        dict's keys) -> every entry is a bare string, filtered out by the
        isinstance(entry, dict) check -> checks=[] -> _derive_verdict([]) -> "clean".
        A dict-shaped cited_numbers is malformed input, not zero verified claims.
        """
        mod = _import_verifier()
        row = {
            "id": 1,
            "advisor_role": "MARKET_PRISM",
            "subject_id": "",
            "verdict": "neutral",
            "raw_response": {
                "run_id": _RUN_ID,
                "run_ts": _RUN_ID,
                "cited_numbers": {"indicator": "VIX"},  # malformed: dict, not a list
            },
        }
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["verdict"] != "clean", (
            "A dict-shaped (malformed) cited_numbers container must never produce "
            f"verdict='clean' — that is a silent pass. Got {result!r}"
        )

    def test_string_shaped_cited_numbers_container_verdict_not_clean(self):
        """nvreview Finding 1: cited_numbers = "garbage-string" must not silently
        produce verdict="clean".

        RED on current code: list("garbage-string") yields individual characters,
        all filtered out by isinstance(entry, dict) -> checks=[] -> "clean".
        """
        mod = _import_verifier()
        row = {
            "id": 1,
            "advisor_role": "MARKET_PRISM",
            "subject_id": "",
            "verdict": "neutral",
            "raw_response": {
                "run_id": _RUN_ID,
                "run_ts": _RUN_ID,
                "cited_numbers": "garbage-string",  # malformed: string, not a list
            },
        }
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["verdict"] != "clean", (
            "A string-shaped (malformed) cited_numbers container must never produce "
            f"verdict='clean' — that is a silent pass. Got {result!r}"
        )

    def test_list_of_malformed_entries_verdict_not_clean_and_no_silent_drop(self):
        """nvreview Finding 1: cited_numbers = [42, "foo", {"nope": 1}] — a real list,
        but every entry is either non-dict or a dict missing indicator/value — must
        not silently produce verdict="clean", AND no entry should be silently dropped
        (each of the 3 entries must surface as an unverifiable check).

        RED on current code: the list comprehension's `if isinstance(entry, dict)`
        guard drops 42 and "foo" silently (never become checks at all); only
        {"nope": 1} survives and produces one "unverifiable" check (indicator=None
        is unmapped) -> n_checks=1, n_overridden=0, n_flagged=0 -> _derive_verdict
        falls through to "clean" even though nothing was actually verified clean.
        """
        mod = _import_verifier()
        row = {
            "id": 1,
            "advisor_role": "MARKET_PRISM",
            "subject_id": "",
            "verdict": "neutral",
            "raw_response": {
                "run_id": _RUN_ID,
                "run_ts": _RUN_ID,
                "cited_numbers": [42, "foo", {"nope": 1}],
            },
        }
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["verdict"] != "clean", (
            "A list of entirely malformed/unverifiable entries must never produce "
            f"verdict='clean' — that is a silent pass. Got {result!r}"
        )
        assert len(result["checks"]) == 3, (
            "Every entry in cited_numbers must surface as a check (classified "
            "unverifiable if malformed) — never a silent drop. Expected 3 checks "
            f"(one per input entry); got {len(result['checks'])}: {result['checks']!r}"
        )
        assert all(c.get("classification") == "unverifiable" for c in result["checks"]), (
            "Every malformed entry (non-dict or missing indicator/value) must "
            f"classify as unverifiable; got {result['checks']!r}"
        )


# ===========================================================================
# TestGroundTruthResolution — AC-4 / AC-5
# ===========================================================================


class TestGroundTruthResolution:
    def test_fred_string_value_normalized(self):
        """AC-5: FRED value:'4.35' (string) must be compared correctly against a float cite."""
        mod = _import_verifier()
        row = _make_market_prism_row([{"indicator": "DGS10", "value": 4.36, "lens": "macro"}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        check = result["checks"][0]
        assert check["classification"] in ("pass", "flagged"), (
            f"AC-5: FRED's string value '4.35' must normalize to float and compare "
            f"sanely against a cited 4.36 (a 0.01 diff); string-vs-float never yields "
            f"unverifiable or a crash. Got {check!r}"
        )

    def test_breadth_normalized_as_0_to_1_fraction(self):
        """AC-5: breadth is a fraction (0-1), not a percentage — normalize accordingly."""
        mod = _import_verifier()
        row = _make_market_prism_row(
            [{"indicator": "breadth", "value": 0.60, "lens": "technicals"}]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        check = result["checks"][0]
        assert check["classification"] in ("pass", "flagged"), (
            f"AC-5: cited breadth 0.60 vs truth 0.58 (both 0-1 fractions) must compare "
            f"as close values, not be treated as a percent-vs-fraction mismatch; "
            f"got {check!r}"
        )

    def test_ground_truth_reused_no_duplicate_fetch_when_lens_sections_supplied(self):
        """AC-4: when lens_sections is supplied, the builders must NOT be invoked —
        the verifier reuses the caller-supplied payloads, never a duplicate fetch."""
        mod = _import_verifier()
        import ai_advisor

        row = _make_market_prism_row([{"indicator": "VIX", "value": 21.8, "lens": "derivatives"}])
        with (
            patch.object(ai_advisor, "_build_derivatives_section") as mock_deriv,
            patch.object(ai_advisor, "_build_macro_section") as mock_macro,
            patch.object(ai_advisor, "_build_sentiment_section") as mock_sent,
            patch.object(ai_advisor, "_build_technicals_section") as mock_tech,
            patch.object(ai_advisor, "_build_fundamentals_section") as mock_fund,
        ):
            mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)

        for name, mock in (
            ("derivatives", mock_deriv),
            ("macro", mock_macro),
            ("sentiment", mock_sent),
            ("technicals", mock_tech),
            ("fundamentals", mock_fund),
        ):
            assert not mock.called, (
                f"AC-4: ai_advisor._build_{name}_section was called even though "
                "lens_sections was fully supplied by the caller — the verifier must "
                "reuse the passed-in payloads on the happy path, never re-fetch."
            )


# ===========================================================================
# TestHonestDegradation — AC-1 / AC-11 / AC-12 / D-1
# ===========================================================================


class TestHonestDegradation:
    def test_no_cited_numbers_returns_no_claims_verdict(self):
        """AC-11: a MARKET_PRISM row with no cited_numbers key -> verdict 'no-numeric-claims'."""
        mod = _import_verifier()
        row = _make_market_prism_row_no_cited_numbers()
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert result["checks"] == [], (
            f"AC-11: no cited_numbers -> empty check set; got {result['checks']!r}"
        )
        assert result["verdict"] == "no-numeric-claims", (
            f"AC-11: verdict must be exactly 'no-numeric-claims' for a legacy/degraded "
            f"row with no cited_numbers key; got {result['verdict']!r}"
        )

    def test_unverifiable_source_unavailable(self):
        """AC-12: macro lens available=False -> every macro-mapped cited number is
        unverifiable, NEVER flagged/overridden (positive + negative assertion)."""
        mod = _import_verifier()
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["macro"] = _unavailable_lens("macro")
        row = _make_market_prism_row([{"indicator": "UNRATE", "value": 4.3, "lens": "macro"}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        check = result["checks"][0]
        assert check["classification"] == "unverifiable", (
            f"AC-12: macro unavailable -> UNRATE citation must be unverifiable; got {check!r}"
        )
        assert check["classification"] not in ("flagged", "overridden"), (
            "AC-12: a source-unavailable citation must NEVER be flagged/overridden — "
            f"that would imply a ground-truth comparison happened when it did not. "
            f"Got {check!r}"
        )

    def test_malformed_cited_value_never_raises(self):
        """Edge case: cited value is a non-numeric string / None -> unverifiable, no raise."""
        mod = _import_verifier()
        for bad_value in ("not-a-number", None, {"nested": "dict"}, [1, 2, 3]):
            row = _make_market_prism_row(
                [{"indicator": "VIX", "value": bad_value, "lens": "derivatives"}]
            )
            result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
            assert result["checks"][0]["classification"] == "unverifiable", (
                f"D-1: malformed cited value {bad_value!r} must classify as "
                f"unverifiable, never raise; got {result['checks'][0]!r}"
            )

    def test_verify_never_raises_when_internal_fetch_raises(self):
        """AC-1/D-1: when lens_sections=None and the internal builder fetch raises,
        verify_cited_numbers must never propagate the exception."""
        mod = _import_verifier()
        import ai_advisor

        row = _make_market_prism_row([{"indicator": "VIX", "value": 21.8, "lens": "derivatives"}])
        with patch.object(
            ai_advisor, "_build_derivatives_section", side_effect=RuntimeError("boom")
        ):
            result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=None)
        assert isinstance(result, dict), (
            "AC-1: verify_cited_numbers must never raise, even when an internal "
            "builder fetch fails — must degrade to a dict result."
        )

    def test_cited_numbers_list_length_bounded(self):
        """Security: an oversized cited_numbers list must not be processed unboundedly
        (DoS guard) — checks are capped at _MAX_CITED_NUMBERS."""
        mod = _import_verifier()
        oversized = [
            {"indicator": "VIX", "value": 22.0, "lens": "derivatives"}
            for _ in range(mod._MAX_CITED_NUMBERS + 50)
        ]
        row = _make_market_prism_row(oversized)
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert len(result["checks"]) <= mod._MAX_CITED_NUMBERS, (
            f"Security: cited_numbers list of {len(oversized)} entries produced "
            f"{len(result['checks'])} checks — must be bounded to <= "
            f"_MAX_CITED_NUMBERS ({mod._MAX_CITED_NUMBERS!r})."
        )

    def test_malformed_secret_bearing_exception_never_leaks_into_result(self):
        """D-1: an exception message that embeds a fake FRED_API_KEY-shaped secret must
        NEVER appear anywhere in the returned result — only type(exc).__name__ is ever
        surfaced (FRED embeds the API key as a URL query param — see ai_advisor.py's own
        _build_macro_section docstring discipline)."""
        mod = _import_verifier()
        import ai_advisor

        secret_marker = "FRED_API_KEY=zzz-fake-secret-marker-9f8e7d21"
        row = _make_market_prism_row([{"indicator": "DGS10", "value": 4.3, "lens": "macro"}])
        with patch.object(
            ai_advisor, "_build_macro_section", side_effect=RuntimeError(secret_marker)
        ):
            result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=None)
        serialized = json.dumps(result, default=str)
        assert secret_marker not in serialized, (
            "D-1: raw exception message (which may embed FRED_API_KEY) must never "
            f"leak into the verifier's result — only type(exc).__name__ is permitted. "
            f"Found the secret marker in the serialized result: {serialized!r}"
        )


# ===========================================================================
# TestDuplicateAndDrift — AC-13 / AC-14
# ===========================================================================


class TestDuplicateAndDrift:
    def test_duplicate_indicator_two_lenses_not_masked(self):
        """AC-14: two lenses citing the same indicator are verified independently
        against the canonical ground-truth source — a mismatch in one is not masked
        by a pass in the other."""
        mod = _import_verifier()
        row = _make_market_prism_row(
            [
                {"indicator": "VIX", "value": 21.8, "lens": "derivatives"},  # correct
                {"indicator": "VIX", "value": 5.0, "lens": "sentiment"},  # grossly wrong
            ]
        )
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=_FULL_LENS_SECTIONS)
        assert len(result["checks"]) == 2, (
            f"AC-14: both citations of VIX must produce independent checks — one must "
            f"not collapse/absorb the other. Got {len(result['checks'])} check(s): "
            f"{result['checks']!r}"
        )
        by_lens = {c["lens"]: c["classification"] for c in result["checks"]}
        assert by_lens.get("derivatives") == "pass", (
            f"AC-14: the correct derivatives citation of VIX must still pass; got {by_lens!r}"
        )
        assert by_lens.get("sentiment") in ("flagged", "overridden"), (
            f"AC-14: the grossly wrong sentiment citation of VIX must NOT be masked "
            f"by the correct derivatives citation passing; got {by_lens!r}"
        )

    def test_drift_tolerant_tone_has_larger_tolerance_than_stable_macro_series(self):
        """AC-13: GDELT tone (drift-prone) must use a strictly larger tolerance than a
        stable daily FRED series (DGS10) — derived from the module's own constants, not
        a guessed magnitude, so this holds regardless of the implementer's exact values."""
        mod = _import_verifier()
        tone_tolerance = mod._INDICATOR_REGISTRY["tone"][3]
        dgs10_tolerance = mod._INDICATOR_REGISTRY["DGS10"][3]
        assert tone_tolerance > dgs10_tolerance, (
            f"AC-13: tone's tolerance ({tone_tolerance!r}) must be strictly larger than "
            f"DGS10's ({dgs10_tolerance!r}) — otherwise legitimate rolling-artlist tone "
            "drift between council-time and verify-time would be misclassified as a "
            "mismatch, exactly the false-override DE-PRISM-SOURCES-001 precedent warns "
            "against."
        )

    def test_tone_pass_within_its_own_drift_tolerance_boundary(self):
        """AC-13: a tone delta exactly at tone's own (larger) tolerance must still pass —
        derived from the registry constant, not a hardcoded delta."""
        mod = _import_verifier()
        tolerance = mod._INDICATOR_REGISTRY["tone"][3]
        truth = 0.10
        cited = truth - tolerance
        lens_sections = copy.deepcopy(_FULL_LENS_SECTIONS)
        lens_sections["sentiment"]["payload"]["tone_score"] = truth
        row = _make_market_prism_row([{"indicator": "tone", "value": cited, "lens": "sentiment"}])
        result = mod.verify_cited_numbers(_RUN_ID, row, lens_sections=lens_sections)
        assert result["checks"][0]["classification"] == "pass", (
            f"AC-13: at exactly tone's own tolerance ({tolerance!r}), classification "
            f"must be pass; got {result['checks'][0]!r}"
        )


# ===========================================================================
# TestPersistence — AC-7 / AC-8 / D-2 (append-only)
# ===========================================================================

_SAMPLE_RESULT = {
    "checks": [
        {
            "indicator": "VIX",
            "lens": "derivatives",
            "cited_value": 21.8,
            "ground_truth_value": 22.0,
            "classification": "pass",
        },
    ],
    "summary": {"n_checks": 1, "n_pass": 1, "n_flagged": 0, "n_overridden": 0, "n_unverifiable": 0},
    "verdict": "clean",
}


class TestPersistence:
    def test_persist_writes_exactly_one_verification_row(self):
        """AC-7: persist_verification INSERTs exactly one MARKET_PRISM_VERIFICATION row."""
        mod = _import_verifier()
        import database

        with (
            patch.object(
                database,
                "get_latest_market_prism_verification_for_run",
                return_value=None,
                create=True,
            ),
            patch.object(database, "insert_advisor_observation", return_value=99) as mock_insert,
        ):
            row_id = mod.persist_verification(_RUN_ID, _SAMPLE_RESULT)

        assert mock_insert.call_count == 1, (
            f"AC-7: persist_verification must call insert_advisor_observation exactly "
            f"once; called {mock_insert.call_count} times."
        )
        assert row_id == 99, f"persist_verification must return the new row id; got {row_id!r}"

        kwargs = mock_insert.call_args.kwargs
        assert kwargs.get("advisor_role") == "MARKET_PRISM_VERIFICATION", (
            f"AC-7: the inserted row must have advisor_role='MARKET_PRISM_VERIFICATION'; "
            f"got {kwargs.get('advisor_role')!r}"
        )

    def test_persist_row_shape_has_required_keys(self):
        """AC-7: the persisted raw_response carries run_id, verified_at, checks, summary, verdict."""
        mod = _import_verifier()
        import database

        with (
            patch.object(
                database,
                "get_latest_market_prism_verification_for_run",
                return_value=None,
                create=True,
            ),
            patch.object(database, "insert_advisor_observation", return_value=1) as mock_insert,
        ):
            mod.persist_verification(_RUN_ID, _SAMPLE_RESULT)

        raw = mock_insert.call_args.kwargs.get("raw_response") or {}
        for key in ("run_id", "verified_at", "checks", "summary", "verdict"):
            assert key in raw, (
                f"AC-7: persisted raw_response is missing required key {key!r}; "
                f"raw_response keys: {list(raw.keys())!r}"
            )
        assert raw["run_id"] == _RUN_ID, f"raw_response['run_id'] must be {_RUN_ID!r}"
        for summary_key in ("n_checks", "n_pass", "n_flagged", "n_overridden", "n_unverifiable"):
            assert summary_key in raw["summary"], (
                f"AC-7: persisted summary is missing required key {summary_key!r}; "
                f"summary keys: {list(raw['summary'].keys())!r}"
            )

    def test_persist_idempotent_skips_second_insert(self):
        """AC-8: an existing MARKET_PRISM_VERIFICATION row for this run_id -> skip INSERT."""
        mod = _import_verifier()
        import database

        with (
            patch.object(
                database,
                "get_latest_market_prism_verification_for_run",
                return_value={"id": 42, "advisor_role": "MARKET_PRISM_VERIFICATION"},
                create=True,
            ),
            patch.object(database, "insert_advisor_observation") as mock_insert,
        ):
            mod.persist_verification(_RUN_ID, _SAMPLE_RESULT)

        assert not mock_insert.called, (
            "AC-8: persist_verification must skip the INSERT when a "
            "MARKET_PRISM_VERIFICATION row already exists for this run_id (idempotency)."
        )

    def test_market_prism_row_never_mutated_by_persist(self):
        """D-2 append-only invariant: persist_verification must never write
        advisor_role='MARKET_PRISM' — only MARKET_PRISM_VERIFICATION."""
        mod = _import_verifier()
        import database

        with (
            patch.object(
                database,
                "get_latest_market_prism_verification_for_run",
                return_value=None,
                create=True,
            ),
            patch.object(database, "insert_advisor_observation", return_value=1) as mock_insert,
        ):
            mod.persist_verification(_RUN_ID, _SAMPLE_RESULT)

        for call in mock_insert.call_args_list:
            assert call.kwargs.get("advisor_role") != "MARKET_PRISM", (
                "D-2 append-only invariant violated: persist_verification must never "
                "write (let alone update) an advisor_role='MARKET_PRISM' row — the "
                "MARKET_PRISM row is immutable."
            )


# ===========================================================================
# TestOffPathAndSafety — AC-1, AC-15, Security Considerations
# ===========================================================================


class TestOffPathAndSafety:
    def test_prism_numeric_verifier_not_imported_from_alpha_bot_execution(self):
        """CC-2 static scan, mirroring the existing lens_pipeline guard. This is
        expected to PASS trivially on day 1 (alpha_bot_execution.py does not yet
        mention this module at all) — it is a regression guard, not a RED gate."""
        source_path = _REPO_ROOT / "alpha_bot_execution.py"
        assert source_path.exists(), f"alpha_bot_execution.py not found at {source_path}"
        source = source_path.read_text(encoding="utf-8")
        assert "prism_numeric_verifier" not in source, (
            "CC-2 violation: alpha_bot_execution.py must never reference "
            "prism_numeric_verifier — the verifier must stay off the 1-minute "
            "execution path."
        )

    def test_module_exposes_named_tolerance_and_cap_constants(self):
        """AC-15: no magic numbers — tolerances/override-factor/cap are named constants."""
        mod = _import_verifier()
        for name in ("_INDICATOR_REGISTRY", "_OVERRIDE_FACTOR", "_MAX_CITED_NUMBERS"):
            assert hasattr(mod, name), (
                f"AC-15: prism_numeric_verifier must expose the named module constant "
                f"{name!r} (project standard: no magic numbers)."
            )


# ===========================================================================
# TestGoldenFixture — decision-layer golden fixture (quant-test-writer convention)
# ===========================================================================


class TestGoldenFixture:
    def test_verify_cited_numbers_golden_fixture_mixed_classifications(self):
        """Golden-fixture test: a schema-derived MARKET_PRISM row + lens_sections bundle
        spanning all 4 classifications in one run. Fixture provenance: schema-derived
        from the confirmed payload shapes at ai_advisor.py:755-765/900-905/673-684/
        542-552/1242-1253 (NOT parser+fixture co-design). Classification labels are the
        verifier's OWN decision output (not a producer-computed rate/dollar/percent) —
        assertable per the golden-fixture convention (input row -> expected decision)."""
        mod = _import_verifier()
        fixture_path = (
            _REPO_ROOT
            / "tests"
            / "fixtures"
            / "prism_verifier"
            / "verify_cited_numbers_mixed_classifications.json"
        )
        assert fixture_path.exists(), f"Golden fixture not found at {fixture_path}"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

        result = mod.verify_cited_numbers(
            fixture["run_id"],
            fixture["market_prism_row"],
            lens_sections=fixture["lens_sections"],
        )

        assert result["summary"]["n_checks"] == len(fixture["expected_classifications"]), (
            f"Golden fixture: expected {len(fixture['expected_classifications'])} checks, "
            f"got {result['summary']['n_checks']}. Full result: {result!r}"
        )

        by_indicator = {c["indicator"]: c["classification"] for c in result["checks"]}
        for indicator, expected_classification in fixture["expected_classifications"].items():
            assert by_indicator.get(indicator) == expected_classification, (
                f"Golden fixture: indicator {indicator!r} expected classification "
                f"{expected_classification!r}, got {by_indicator.get(indicator)!r}. "
                f"Full checks: {result['checks']!r}"
            )

        assert result["verdict"] != "no-numeric-claims", (
            "Golden fixture has non-empty cited_numbers — verdict must not be 'no-numeric-claims'."
        )
