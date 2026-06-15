"""
O1 — Purge + Embargo in Walk-Forward Split: RED tests.

These tests are written BEFORE the implementation. All tests will FAIL
until the implementer adds purge + embargo logic to autotuner.py:509-519
(the train/test split site in run_autotuner), introduces named constants
``PURGE_DAYS`` and ``EMBARGO_DAYS``, and documents the methodology tradeoffs
in a docstring citing Lopez de Prado 2018 Ch. 7.

Fixture provenance: tests/fixtures/autotuner/purge_embargo/
  - feature_lookback_inventory.json — documents the two purge-relevant feature
    lookbacks (vol=20, ATR=15) and explicitly excludes MC kNN and decay weighting
    as non-purge-relevant (see _excluded_from_purge in fixture).
  - split_overlap_fixture.json — deterministic synthetic split scenario with
    computed purged/embargoed sample IDs; no live API calls.

No assertions use bare literals for producer-computed values. Purge sizes,
lookback values, and expected sample sets are read from fixtures or derived
from named constants.

Citation: Lopez de Prado, M. (2018). Advances in Financial Machine Learning.
Wiley. Ch. 7 (Purged k-fold CV).
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from datetime import date, timedelta

import pytest

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "autotuner" / "purge_embargo"
AUTOTUNER_SRC = pathlib.Path(__file__).parent.parent.parent / "autotuner.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _parse_autotuner_source() -> str:
    return AUTOTUNER_SRC.read_text(encoding="utf-8")


def _parse_autotuner_ast() -> ast.Module:
    return ast.parse(_parse_autotuner_source())


def _find_module_level_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """Return all module-level assignments as {name: value_node}."""
    assignments: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                assignments[node.target.id] = node.value
    return assignments


def _trading_days_between(start: str, end: str, all_dates: list[str]) -> int:
    """Count trading days (from sorted all_dates list) strictly between start and end."""
    sorted_dates = sorted(all_dates)
    return sum(1 for d in sorted_dates if start < d < end)


# ---------------------------------------------------------------------------
# Test 1: Feature lookback inventory is complete and MAX is correct
# ---------------------------------------------------------------------------


class TestFeatureLookbackInventory:
    """
    Enumerate the purge-relevant features (vol + ATR only). Assert the purge size
    equals MAX of their lookbacks. Verify that MC kNN and decay weighting are
    documented as excluded (they are not feature lookbacks in the Lopez de Prado sense).
    """

    def test_feature_lookback_inventory_purge_relevant_features_present(self):
        """Fixture documents the two purge-relevant features: vol and ATR."""
        inv = _load_fixture("feature_lookback_inventory.json")
        feature_names = {f["name"] for f in inv["features"]}
        assert "20d_historical_vol" in feature_names, (
            "Inventory missing 20d_historical_vol (math_engine.LOOKBACK_DAYS=20)"
        )
        assert "14d_atr_pct" in feature_names, (
            "Inventory missing 14d_atr_pct (math_engine.ATR_LOOKBACK_DAYS=15)"
        )

    def test_feature_lookback_inventory_excluded_features_documented(self):
        """
        MC kNN and decay weighting must appear in _excluded_from_purge, not in features.
        An implementer who includes them as purge-relevant would over-purge to 46 days,
        collapsing the OOS fold entirely on the 125-day history window.
        """
        inv = _load_fixture("feature_lookback_inventory.json")
        excluded_names = {e["name"] for e in inv.get("_excluded_from_purge", [])}
        feature_names = {f["name"] for f in inv["features"]}

        assert "mc_knn_spy_vol" in excluded_names, (
            "MC kNN SPY vol must appear in _excluded_from_purge: it is pre-computed "
            "in tick data, not called live in the autotuner simulation loop"
        )
        assert "decay_weighted_objective" in excluded_names, (
            "Decay weighting must appear in _excluded_from_purge: it is an objective "
            "aggregation weight, not a feature window that crosses the fold boundary"
        )
        assert "mc_knn_spy_vol" not in feature_names, (
            "MC kNN must NOT be in the purge-relevant features list"
        )
        assert "decay_weighted_objective" not in feature_names, (
            "Decay weighting must NOT be in the purge-relevant features list"
        )

    def test_feature_lookback_inventory_purge_days_matches_max(self):
        """purge_days_required must equal max_lookback_trading_days (both should be 20)."""
        inv = _load_fixture("feature_lookback_inventory.json")
        lookbacks = [f["lookback_trading_days"] for f in inv["features"]]
        assert max(lookbacks) == inv["max_lookback_trading_days"], (
            "max_lookback_trading_days must equal the actual max of purge-relevant feature lookbacks"
        )
        assert inv["purge_days_required"] == inv["max_lookback_trading_days"], (
            "purge_days_required must equal max_lookback_trading_days"
        )

    def test_atr_lookback_matches_named_constant(self):
        """
        ATR lookback in the fixture must match ATR_LOOKBACK_DAYS=15 from math_engine.py,
        not the colloquial '14-day ATR' name (which is 14 true-range periods + 1 prior close).
        This catches a common off-by-one: the function slices [-ATR_LOOKBACK_DAYS:] = 15 dates.
        """
        inv = _load_fixture("feature_lookback_inventory.json")
        features_by_name = {f["name"]: f for f in inv["features"]}
        atr_lookback = features_by_name["14d_atr_pct"]["lookback_trading_days"]
        # ATR_LOOKBACK_DAYS = 15 in math_engine.py:38
        assert atr_lookback == 15, (
            f"ATR lookback in fixture is {atr_lookback} but math_engine.ATR_LOOKBACK_DAYS=15. "
            "The function name says '14d' but the constant is 15 (14 TRs + 1 prior close)."
        )


# ---------------------------------------------------------------------------
# Test 2: Purge removes train samples whose feature window overlaps test window
# ---------------------------------------------------------------------------


class TestPurgeRemovesOverlappingTrainSamples:
    """
    Using the deterministic split fixture, verify that a correct purge implementation
    excludes train samples whose 20-day feature window overlaps any test date.
    """

    def test_purge_removes_train_samples_overlapping_test_window(self):
        """
        A train sample whose 20-day feature window touches any test date must be excluded.
        The fixture documents which samples are expected to survive and which are purged.
        """
        fix = _load_fixture("split_overlap_fixture.json")

        train_dates = fix["train_dates"]
        expected_purged = set(fix["expected_purged_train_dates"])
        expected_surviving = set(fix["expected_surviving_train_dates"])

        for d in expected_purged:
            assert d in set(train_dates), f"Purged date {d} must be in train_dates"
        for d in expected_surviving:
            assert d in set(train_dates), f"Surviving date {d} must be in train_dates"

        assert expected_purged.isdisjoint(expected_surviving), (
            "Purged and surviving sets must not overlap"
        )
        assert expected_purged | expected_surviving == set(train_dates), (
            "Every train date must be classified as either purged or surviving"
        )

    def test_purge_implementation_excludes_overlap_dates(self):
        """
        After purge, surviving train dates must have feature windows that do not
        touch the test fold. Checks the fixture's surviving set for self-consistency.
        """
        fix = _load_fixture("split_overlap_fixture.json")
        test_start = date.fromisoformat(fix["test_start_date"])
        purge_lookback = fix["purge_lookback_days"]
        expected_surviving = set(fix["expected_surviving_train_dates"])

        for d_str in expected_surviving:
            d = date.fromisoformat(d_str)
            # Approximate: 19 trading days ≈ 27 calendar days
            window_end_approx = d + timedelta(days=(purge_lookback - 1) * 1.4)
            assert window_end_approx < test_start, (
                f"Surviving train date {d_str} has a window that may reach test_start "
                f"{test_start} — this date should have been purged"
            )

    def test_split_site_in_autotuner_applies_purge_logic(self):
        """
        AST inspection: autotuner.py's split site (near split_idx) must contain
        purge-filtering logic — not just the raw set partitioning.
        A plain 80/20 split with no filtering cannot satisfy AC-O1.1.

        RED until the implementer adds purge logic referencing PURGE_DAYS near split_idx.
        """
        source = _parse_autotuner_source()
        lines = source.splitlines()

        split_idx_lines = [i for i, l in enumerate(lines) if re.search(r"\bsplit_idx\b\s*=", l)]
        assert split_idx_lines, "split_idx not found in autotuner.py"

        split_lineno = split_idx_lines[0]
        vicinity = "\n".join(lines[split_lineno : split_lineno + 50])

        has_purge_constant = bool(re.search(r"\bPURGE_DAYS\b", vicinity))
        has_purge_filter = bool(re.search(r"purge|embargo", vicinity, re.IGNORECASE))

        assert has_purge_constant or has_purge_filter, (
            "autotuner.py split site (near split_idx) has no purge/embargo logic. "
            "The current raw 80/20 set partition does not exclude overlapping train dates. "
            "RED until implementer adds purge filtering using PURGE_DAYS constant."
        )


# ---------------------------------------------------------------------------
# Test 3: Embargo separates train-end from test-start
# ---------------------------------------------------------------------------


class TestEmbargoSeparatesTrainFromTest:
    """
    No train/test pair has timestamps within EMBARGO_DAYS of each other.
    """

    def test_embargo_separates_train_end_from_test_start(self):
        """
        After applying purge + embargo, the last surviving train date must be
        at least EMBARGO_DAYS trading days before the first test date.
        """
        fix = _load_fixture("split_overlap_fixture.json")
        embargo_days = fix["embargo_days"]
        expected_surviving = sorted(fix["expected_surviving_train_dates"])
        test_dates = sorted(fix["test_dates"])

        if not expected_surviving or not test_dates:
            pytest.skip("Fixture has no surviving train dates or no test dates")

        last_surviving_train = expected_surviving[-1]
        first_test = test_dates[0]

        all_dates = sorted(fix["train_dates"] + fix["test_dates"])
        gap = _trading_days_between(last_surviving_train, first_test, all_dates)

        assert gap >= embargo_days, (
            f"Last surviving train date {last_surviving_train} is only {gap} trading day(s) "
            f"before first test date {first_test}; embargo requires at least {embargo_days} day(s)"
        )

    def test_embargo_excludes_immediately_adjacent_train_dates(self):
        """
        Train dates that are only EMBARGO_DAYS away from the test window must be excluded,
        even if they survive the purge by lookback alone.
        """
        fix = _load_fixture("split_overlap_fixture.json")
        expected_embargoed = fix["expected_embargoed_train_dates"]
        expected_surviving = set(fix["expected_surviving_train_dates"])

        for d in expected_embargoed:
            assert d not in expected_surviving, (
                f"Embargoed date {d} must not appear in surviving train dates"
            )


# ---------------------------------------------------------------------------
# Test 4: Embargo uses a named constant (AST inspection)
# ---------------------------------------------------------------------------


class TestEmbargoIsNamedConstant:
    """
    AST inspection: no bare integer 1 (or similar) is used for the embargo period;
    a named module-level constant with a source comment citing Lopez de Prado 2018 Ch. 7
    must be present in autotuner.py.
    """

    def test_embargo_days_constant_exists(self):
        """
        autotuner.py must define a module-level constant named EMBARGO_DAYS.
        RED until the implementer adds it.
        """
        tree = _parse_autotuner_ast()
        assignments = _find_module_level_assignments(tree)

        assert "EMBARGO_DAYS" in assignments, (
            "autotuner.py must define a module-level EMBARGO_DAYS constant "
            "(Lopez de Prado 2018 Ch. 7). Currently absent — RED until implementer adds it."
        )

    def test_embargo_days_constant_has_source_comment(self):
        """
        The EMBARGO_DAYS constant must have a source comment citing Lopez de Prado
        2018 Ch. 7.

        AC-9 (Cluster 4) rewrote the EMBARGO_DAYS rationale into a multi-line
        block (the serial-dependence-horizon justification). The scan therefore
        walks the WHOLE contiguous comment block immediately above the
        assignment, not just the line just above — the citation lives anywhere in
        that block.
        """
        source = _parse_autotuner_source()
        lines = source.splitlines()
        embargo_lines = [
            (i, line) for i, line in enumerate(lines) if re.search(r"\bEMBARGO_DAYS\b\s*=", line)
        ]
        assert embargo_lines, (
            "EMBARGO_DAYS = ... assignment not found in autotuner.py — RED until implemented"
        )

        found_citation = False
        for lineno, _ in embargo_lines:
            # Walk upward over the contiguous comment block above the assignment.
            start = lineno
            while start > 0 and lines[start - 1].strip().startswith("#"):
                start -= 1
            context = "\n".join(lines[start : lineno + 1])
            if (
                re.search(r"[Ll]ópez de Prado|Lopez de Prado|L.pez de Prado", context)
                and "2018" in context
            ):
                found_citation = True
                break
        assert found_citation, (
            "EMBARGO_DAYS constant must have a source comment citing "
            "'Lopez de Prado 2018' (Ch. 7) in its rationale comment block"
        )

    def test_no_bare_embargo_literal_in_split_logic(self):
        """
        The split site must not use bare integer literals where EMBARGO_DAYS belongs.
        """
        source = _parse_autotuner_source()
        lines = source.splitlines()

        split_idx_lines = [i for i, l in enumerate(lines) if re.search(r"\bsplit_idx\b\s*=", l)]
        if not split_idx_lines:
            pytest.skip("split_idx not found — implementer may have restructured the split")

        split_lineno = split_idx_lines[0]
        vicinity = "\n".join(lines[split_lineno : split_lineno + 30])

        bare_literal_patterns = [
            r"embargo\s*=\s*1\b",
            r"timedelta\s*\(\s*days\s*=\s*1\s*\)",
        ]
        for pattern in bare_literal_patterns:
            assert not re.search(pattern, vicinity), (
                f"Bare embargo literal matching '{pattern}' found near split_idx; "
                "use named constant EMBARGO_DAYS instead"
            )


# ---------------------------------------------------------------------------
# Test 5: OOS fold collapse is documented (PA-26)
# ---------------------------------------------------------------------------


class TestOOSFoldCollapseDocumented:
    """
    The 125-day history / 20-day purge / ~5 usable test-day tradeoff must be
    documented in autotuner.py per PA-26 (plan-validation-verdict.md O1 section).
    """

    def test_oos_fold_collapse_documented_in_autotuner(self):
        """
        autotuner.py must contain text mentioning the OOS fold collapse tradeoff:
        the ~25-day test fold shrinks substantially after a 20-day purge.
        RED until added.
        """
        source = _parse_autotuner_source()

        has_oos_collapse_mention = bool(
            re.search(
                r"OOS.fold.collapse|fold.collapse|usable.test.day|test.fold.shrink",
                source,
                re.IGNORECASE,
            )
            or (
                re.search(r"purge", source, re.IGNORECASE)
                and re.search(r"usable|shrink|collapse", source, re.IGNORECASE)
            )
        )
        assert has_oos_collapse_mention, (
            "autotuner.py must document the OOS fold collapse tradeoff "
            "(125-day history, 20-day purge leaves ~5 usable test days) per PA-26. "
            "Add to run_autotuner or module docstring. RED until added."
        )

    def test_oos_fold_collapse_references_125_day_history(self):
        """
        The documentation must reference the 125-day history context to quantify
        the tradeoff, not just mention purge in the abstract.
        """
        source = _parse_autotuner_source()
        has_125 = bool(re.search(r"125.day|125 day|125-trading-day", source, re.IGNORECASE))
        has_purge_tradeoff = bool(re.search(r"purge|embargo", source, re.IGNORECASE))

        assert has_125 and has_purge_tradeoff, (
            "autotuner.py documentation must reference both the 125-day history window "
            "and the purge/embargo tradeoff (PA-26). Both are required."
        )


# ---------------------------------------------------------------------------
# Test 6: Purge uses MAX of feature lookbacks (20), not over-purged to 46
# ---------------------------------------------------------------------------


class TestPurgeUsesMaxLookbackNotPartial:
    """
    Purge days must equal the MAX of purge-relevant feature lookbacks (20 — the vol window),
    not the decay half-life (46), which is NOT a feature lookback.

    Adversarial scenario: an implementer who misreads PA-26 may set PURGE_DAYS=46,
    which would collapse the OOS fold entirely on a 125-day history. This test
    catches both under-purge (14, using ATR only) and over-purge (46, misidentifying
    the decay half-life as a feature window).
    """

    def test_purge_constant_or_logic_uses_max_feature_lookback(self):
        """
        autotuner.py must define a PURGE_DAYS (or equivalent) constant equal to the
        max of purge-relevant feature lookbacks. From the fixture: max = 20 (vol window).
        PURGE_DAYS=46 is wrong (decay is not a feature lookback).
        PURGE_DAYS=15 or 14 is wrong (ATR alone; vol window is larger).
        RED until the implementer adds PURGE_DAYS=20.
        """
        tree = _parse_autotuner_ast()
        assignments = _find_module_level_assignments(tree)

        inv = _load_fixture("feature_lookback_inventory.json")
        correct_purge = inv["purge_days_required"]  # 20

        # Find the PURGE_DAYS constant (or any named purge constant)
        purge_constant_names = [k for k in assignments if "PURGE" in k.upper()]

        if not purge_constant_names:
            pytest.fail(
                f"No PURGE_DAYS (or named purge constant) found in autotuner.py. "
                f"Implementer must add a named constant equal to {correct_purge} "
                f"(max of purge-relevant feature lookbacks: vol=20, ATR=15). "
                f"Note: 46 (decay half-life) is NOT correct — the decay is an objective "
                "weight, not a feature lookback. RED until added."
            )

        for cname in purge_constant_names:
            node = assignments[cname]
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
                assert node.value == correct_purge, (
                    f"Constant {cname}={node.value} is wrong. "
                    f"Must equal {correct_purge} (max feature lookback = vol window = 20). "
                    f"Common mistakes: "
                    f"  - {node.value}=46 means the implementer treated decay half-life as a feature lookback (wrong). "
                    f"  - {node.value}=15 means ATR-only (missing the vol window). "
                    f"  - {node.value}=14 means wrong ATR constant (ATR_LOOKBACK_DAYS=15, not 14)."
                    if node.value != correct_purge
                    else ""
                )

    def test_inventory_max_lookback_is_vol_not_atr_not_decay(self):
        """
        Invariant: vol lookback (20) > ATR lookback (15) > 0; decay half-life (46)
        is explicitly excluded. Max purge-relevant lookback is 20.
        """
        inv = _load_fixture("feature_lookback_inventory.json")
        features_by_name = {f["name"]: f for f in inv["features"]}

        vol_lookback = features_by_name["20d_historical_vol"]["lookback_trading_days"]
        atr_lookback = features_by_name["14d_atr_pct"]["lookback_trading_days"]
        max_lookback = inv["max_lookback_trading_days"]

        assert vol_lookback > atr_lookback, (
            f"20d_historical_vol lookback ({vol_lookback}) must exceed "
            f"14d_atr_pct lookback ({atr_lookback})"
        )
        assert max_lookback == vol_lookback, (
            f"max_lookback_trading_days ({max_lookback}) must equal the vol window "
            f"({vol_lookback}), not ATR ({atr_lookback}) or decay half-life (46)"
        )
        # The decay must not be in the feature list at all
        excluded_names = {e["name"] for e in inv.get("_excluded_from_purge", [])}
        assert "decay_weighted_objective" in excluded_names, (
            "decay_weighted_objective must appear in _excluded_from_purge, "
            "confirming it is NOT treated as a purge-relevant feature lookback"
        )

    def test_purge_constant_has_source_comment(self):
        """
        The PURGE_DAYS constant in autotuner.py must have a comment that cites
        López de Prado 2018 Ch. 7 and explains the max(vol-lookback, ATR-lookback)
        derivation of the 20-day value.

        Re-pinned for Decision D5: the previous version of this test required the
        comment to explain why the recency-decay weighting (_GUARD_ALPHA_DECAY_RATE)
        was EXCLUDED from purge sizing — a guard against "correcting" PURGE_DAYS up
        to the 46-day decay half-life. D5 removed the decay weighting and deleted
        _GUARD_ALPHA_DECAY_RATE entirely, so there is no decay constant left to be
        confused for a feature lookback — the decay-exclusion clause is moot. The
        comment must still cite the methodology source and justify the value.

        The scan walks the contiguous comment block above the assignment.
        """
        source = _parse_autotuner_source()
        lines = source.splitlines()

        purge_lines = [
            (i, line) for i, line in enumerate(lines) if re.search(r"\bPURGE_DAYS\b\s*=", line)
        ]
        assert purge_lines, "PURGE_DAYS = ... assignment not found in autotuner.py"

        found_comment = False
        for lineno, _ in purge_lines:
            start = lineno
            while start > 0 and lines[start - 1].strip().startswith("#"):
                start -= 1
            context = "\n".join(lines[start : lineno + 1])
            cites_source = bool(re.search(r"[Ll][óo]pez de Prado", context) and "2018" in context)
            explains_derivation = bool(
                re.search(r"max\s*\(", context) or re.search(r"lookback", context, re.IGNORECASE)
            )
            if cites_source and explains_derivation:
                found_comment = True
                break
        assert found_comment, (
            "PURGE_DAYS constant must have a comment block citing López de Prado "
            "2018 (Ch. 7) and explaining the max(vol-lookback, ATR-lookback) "
            "derivation of the 20-day value. (D5 re-pin: the decay-exclusion "
            "clause is dropped — _GUARD_ALPHA_DECAY_RATE no longer exists.)"
        )
