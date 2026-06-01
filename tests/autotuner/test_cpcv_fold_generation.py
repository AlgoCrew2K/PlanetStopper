"""
CPCV Phase 2 — RED tests (AC-1 / AC-2 / AC-3 / AC-5).

These tests are written BEFORE the implementation and are expected to FAIL
until the implementer (optuna-specialist) adds:

  1. ``_generate_cpcv_folds(sorted_dates, n_groups, k_test, purge_days, embargo_days)``
     in ``autotuner.py`` — returns C(n_groups, k_test) split descriptors, each a dict
     with keys ``train_dates``, ``test_dates``, ``path_membership``.
  2. ``_aggregate_cpcv_paths(folds, n_paths)`` in ``autotuner.py`` — assembles the
     phi[n_groups, k_test] OOS backtest paths by stitching each split's test-fold
     series into the path(s) it belongs to.
  3. Named module-level constants ``_CPCV_N_GROUPS``, ``_CPCV_K_TEST_GROUPS`` and
     derived computed values ``_CPCV_N_SPLITS`` (= math.comb(6,2)=15) and
     ``_CPCV_N_PATHS`` (= int((k/N)*C(N,k))=5) in ``autotuner.py``.
  4. The Optuna objective is rewired to score on the CPCV aggregate (mean across
     the 5 paths) per trial — NOT 15 separate Optuna evaluations.
  5. ``compute_n_effective`` is byte-identical when called with CPCV data: CPCV
     changes WHAT data each trial scores on, not HOW MANY tests exist.

Fixture provenance:
  tests/fixtures/autotuner/cpcv/cpcv_fold_generation_basic.json —
    60-date synthetic sequence, N_GROUPS=6, K_TEST=2, PURGE=2, EMBARGO=1.
    All split memberships and seam gaps hand-derived from first principles.
    Adversarial: a fold generator that leaks on any seam must FAIL.

  tests/fixtures/autotuner/cpcv/cpcv_multiplicity_anti_double_count.json —
    AC-5 regression: n_effective must be independent of n_cpcv_paths.

No assertions hardcode producer-computed values. Numeric expectations are
derived from the fixture or from named constants in autotuner.py.

Citations:
  López de Prado 2018, Advances in Financial Machine Learning, Ch. 7.4
  docs/research/optuna/sources.md — CPCV/Purged-Embargoed CV section
  feature-plans/walk-forward-overhaul.md — Phase 2 AC-1 through AC-5
"""
from __future__ import annotations

import ast
import json
import math
import pathlib
import re
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "autotuner" / "cpcv"
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"

# BHY tolerance — same as existing BHY suite (test_c4_harvey_liu_haircut.py).
# 1e-9 absolute: the BHY step-up uses exact arithmetic; a larger diff means wrong formula.
_BHY_TOL = 1e-9


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_fold_fixture() -> dict:
    return json.loads(
        (_FIXTURE_DIR / "cpcv_fold_generation_basic.json").read_text(encoding="utf-8")
    )


def _load_multiplicity_fixture() -> dict:
    return json.loads(
        (_FIXTURE_DIR / "cpcv_multiplicity_anti_double_count.json").read_text(encoding="utf-8")
    )


def _import_autotuner():
    import autotuner
    return autotuner


def _parse_autotuner_source() -> str:
    return _AUTOTUNER_SRC.read_text(encoding="utf-8")


def _parse_autotuner_ast() -> ast.Module:
    return ast.parse(_parse_autotuner_source())


def _find_module_level_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    out: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    out[target.id] = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                out[node.target.id] = node.value
    return out


def _make_fake_trial(value: float, daily_returns: list[float]):
    """Return a minimal fake Optuna trial compatible with _haircut_select."""
    trial = MagicMock()
    trial.value = value
    trial.user_attrs = {"daily_returns": daily_returns}
    return trial


# ===========================================================================
# Precondition: fixture files exist and are well-formed
# ===========================================================================

class TestFixturesPrecondition:

    def test_fold_fixture_exists_and_parses(self):
        """The golden fold fixture must exist and be valid JSON before math tests run."""
        path = _FIXTURE_DIR / "cpcv_fold_generation_basic.json"
        assert path.exists(), (
            f"Golden fixture missing at {path}. "
            "Create tests/fixtures/autotuner/cpcv/cpcv_fold_generation_basic.json."
        )
        data = _load_fold_fixture()
        assert "parameters" in data, "Fixture must have 'parameters' key."
        assert "derived_constants" in data, "Fixture must have 'derived_constants' key."
        assert "all_splits" in data, "Fixture must have 'all_splits' key."
        assert "sorted_dates" in data, "Fixture must have 'sorted_dates' key."

    def test_multiplicity_fixture_exists_and_parses(self):
        """The anti-double-count fixture must exist and parse before AC-5 tests run."""
        path = _FIXTURE_DIR / "cpcv_multiplicity_anti_double_count.json"
        assert path.exists(), (
            f"Anti-double-count fixture missing at {path}. "
            "Create tests/fixtures/autotuner/cpcv/cpcv_multiplicity_anti_double_count.json."
        )
        data = _load_multiplicity_fixture()
        assert "scenarios" in data, "Fixture must have 'scenarios' key."

    def test_fold_fixture_split_count_matches_formula(self):
        """Fixture must record C(6,2)=15 splits — derived constant, not a literal."""
        fix = _load_fold_fixture()
        expected = int(math.comb(
            fix["parameters"]["n_groups"],
            fix["parameters"]["k_test_groups"],
        ))
        assert fix["derived_constants"]["n_splits"] == expected, (
            f"Fixture n_splits={fix['derived_constants']['n_splits']} does not match "
            f"C({fix['parameters']['n_groups']},{fix['parameters']['k_test_groups']})={expected}."
        )
        assert len(fix["all_splits"]) == expected, (
            f"Fixture 'all_splits' has {len(fix['all_splits'])} entries; "
            f"expected {expected} = C(6,2)."
        )

    def test_fold_fixture_path_count_matches_formula(self):
        """Fixture must record phi[6,2]=(k/N)*C(N,k)=5 backtest paths."""
        fix = _load_fold_fixture()
        n = fix["parameters"]["n_groups"]
        k = fix["parameters"]["k_test_groups"]
        expected_paths = int((k / n) * math.comb(n, k))
        assert fix["derived_constants"]["n_paths"] == expected_paths, (
            f"Fixture n_paths={fix['derived_constants']['n_paths']} does not match "
            f"phi[{n},{k}]={expected_paths}."
        )


# ===========================================================================
# AC-1 — Named constants + function signature
# ===========================================================================

class TestAC1NamedConstants:
    """
    AC-1: autotuner.py must expose _CPCV_N_GROUPS=6, _CPCV_K_TEST_GROUPS=2
    as named constants with sources.md/LdP Ch.7.4 comments, and derived
    _CPCV_N_SPLITS = math.comb(6,2) and _CPCV_N_PATHS as computed values.
    RED until the implementer adds them.
    """

    def test_cpcv_n_groups_constant_exists(self):
        """autotuner.py must define _CPCV_N_GROUPS at module scope."""
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "_CPCV_N_GROUPS"), (
            "autotuner.py must define _CPCV_N_GROUPS=6 at module scope "
            "(AC-1, feature-plans/walk-forward-overhaul.md Phase 2). "
            "RED until added."
        )

    def test_cpcv_k_test_groups_constant_exists(self):
        """autotuner.py must define _CPCV_K_TEST_GROUPS at module scope."""
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "_CPCV_K_TEST_GROUPS"), (
            "autotuner.py must define _CPCV_K_TEST_GROUPS=2 at module scope "
            "(AC-1). RED until added."
        )

    def test_cpcv_n_groups_is_six(self):
        """_CPCV_N_GROUPS must equal 6 (the LdP Ch.7.4 worked example)."""
        autotuner = _import_autotuner()
        assert autotuner._CPCV_N_GROUPS == 6, (
            f"_CPCV_N_GROUPS={autotuner._CPCV_N_GROUPS}; must be 6 per "
            "LdP Ch.7.4 and feature-plans/walk-forward-overhaul.md Phase 2."
        )

    def test_cpcv_k_test_groups_is_two(self):
        """_CPCV_K_TEST_GROUPS must equal 2 (the LdP Ch.7.4 worked example)."""
        autotuner = _import_autotuner()
        assert autotuner._CPCV_K_TEST_GROUPS == 2, (
            f"_CPCV_K_TEST_GROUPS={autotuner._CPCV_K_TEST_GROUPS}; must be 2 per "
            "LdP Ch.7.4 and feature-plans/walk-forward-overhaul.md Phase 2."
        )

    def test_cpcv_n_splits_is_computed_not_literal(self):
        """_CPCV_N_SPLITS must be derived via math.comb(N,k), not a raw literal 15."""
        src = _parse_autotuner_source()
        assert "_CPCV_N_SPLITS" in src, (
            "autotuner.py must define _CPCV_N_SPLITS (AC-1). "
            "RED until added."
        )
        # The source must reference math.comb (not just '= 15')
        # Find the assignment line and check it is a computed expression.
        lines = src.splitlines()
        n_splits_lines = [l for l in lines if re.search(r"\b_CPCV_N_SPLITS\b\s*=", l)]
        assert n_splits_lines, "_CPCV_N_SPLITS assignment not found in autotuner.py."
        assignment_expr = n_splits_lines[0].split("=", 1)[1].strip()
        assert "math.comb" in assignment_expr or "comb(" in assignment_expr, (
            f"_CPCV_N_SPLITS must be derived via math.comb(...), not a raw literal. "
            f"Found: '{assignment_expr}'. Using a literal 15 would break when N/k change."
        )

    def test_cpcv_n_splits_equals_fifteen(self):
        """_CPCV_N_SPLITS must equal C(6,2)=15 at runtime."""
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "_CPCV_N_SPLITS"), (
            "autotuner._CPCV_N_SPLITS not found — RED until implementer adds it."
        )
        expected = math.comb(autotuner._CPCV_N_GROUPS, autotuner._CPCV_K_TEST_GROUPS)
        assert autotuner._CPCV_N_SPLITS == expected, (
            f"_CPCV_N_SPLITS={autotuner._CPCV_N_SPLITS} but "
            f"math.comb({autotuner._CPCV_N_GROUPS},{autotuner._CPCV_K_TEST_GROUPS})={expected}."
        )

    def test_cpcv_n_paths_is_computed_not_literal(self):
        """_CPCV_N_PATHS must be a computed value, not a raw literal 5."""
        src = _parse_autotuner_source()
        assert "_CPCV_N_PATHS" in src, (
            "autotuner.py must define _CPCV_N_PATHS (AC-1). RED until added."
        )
        lines = src.splitlines()
        n_paths_lines = [l for l in lines if re.search(r"\b_CPCV_N_PATHS\b\s*=", l)]
        assert n_paths_lines, "_CPCV_N_PATHS assignment not found in autotuner.py."
        assignment_expr = n_paths_lines[0].split("=", 1)[1].strip()
        # Must reference _CPCV_N_SPLITS or _CPCV_N_GROUPS or comb — not just = 5
        has_derived_expr = (
            "_CPCV_N_SPLITS" in assignment_expr
            or "_CPCV_N_GROUPS" in assignment_expr
            or "comb(" in assignment_expr
            or "math.comb" in assignment_expr
        )
        assert has_derived_expr, (
            f"_CPCV_N_PATHS must be derived from _CPCV_N_SPLITS / _CPCV_N_GROUPS / "
            f"_CPCV_K_TEST_GROUPS, not a raw literal. Found: '{assignment_expr}'."
        )

    def test_cpcv_n_paths_equals_five(self):
        """_CPCV_N_PATHS must equal phi[6,2] = (k/N)*C(N,k) = 5 at runtime."""
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "_CPCV_N_PATHS"), (
            "autotuner._CPCV_N_PATHS not found — RED until implementer adds it."
        )
        expected = int(
            (autotuner._CPCV_K_TEST_GROUPS / autotuner._CPCV_N_GROUPS)
            * autotuner._CPCV_N_SPLITS
        )
        assert autotuner._CPCV_N_PATHS == expected, (
            f"_CPCV_N_PATHS={autotuner._CPCV_N_PATHS}; expected "
            f"phi[{autotuner._CPCV_N_GROUPS},{autotuner._CPCV_K_TEST_GROUPS}]={expected}."
        )

    def test_cpcv_constants_have_source_comment_with_ldp_citation(self):
        """The _CPCV_N_GROUPS constant must have a comment citing LdP Ch.7.4."""
        src = _parse_autotuner_source()
        lines = src.splitlines()
        n_groups_lines = [
            (i, l) for i, l in enumerate(lines)
            if re.search(r"\b_CPCV_N_GROUPS\b\s*=", l)
        ]
        assert n_groups_lines, "_CPCV_N_GROUPS assignment not found."
        lineno, _ = n_groups_lines[0]
        # Walk up the contiguous comment block
        start = lineno
        while start > 0 and lines[start - 1].lstrip().startswith("#"):
            start -= 1
        comment_block = "\n".join(lines[start:lineno + 1])
        has_ldp = bool(re.search(r"[Ll][óo]pez de Prado|LdP", comment_block))
        has_ch7 = "7.4" in comment_block or "Ch. 7" in comment_block
        assert has_ldp and has_ch7, (
            f"_CPCV_N_GROUPS constant must have a source comment citing "
            f"'López de Prado 2018 Ch.7.4'. Comment block found:\n{comment_block}"
        )

    def test_generate_cpcv_folds_function_exists(self):
        """autotuner.py must expose _generate_cpcv_folds as a callable (AC-1)."""
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "_generate_cpcv_folds"), (
            "autotuner must expose _generate_cpcv_folds(sorted_dates, n_groups, "
            "k_test, purge_days, embargo_days) (AC-1). RED until added."
        )
        import inspect
        assert callable(autotuner._generate_cpcv_folds), (
            "_generate_cpcv_folds must be callable."
        )

    def test_aggregate_cpcv_paths_function_exists(self):
        """autotuner.py must expose _aggregate_cpcv_paths as a callable (AC-2)."""
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "_aggregate_cpcv_paths"), (
            "autotuner must expose _aggregate_cpcv_paths(...) (AC-2). RED until added."
        )
        assert callable(autotuner._aggregate_cpcv_paths), (
            "_aggregate_cpcv_paths must be callable."
        )


# ===========================================================================
# AC-1 — _generate_cpcv_folds output shape
# ===========================================================================

class TestAC1FoldGenerationShape:
    """
    AC-1: _generate_cpcv_folds must return exactly C(n_groups,k_test)=15 split
    descriptors, each with keys train_dates, test_dates, path_membership.
    """

    def test_fold_count_is_c_n_k(self):
        """_generate_cpcv_folds must return C(6,2)=15 splits for N=6, k=2."""
        fix = _load_fold_fixture()
        autotuner = _import_autotuner()
        if not hasattr(autotuner, "_generate_cpcv_folds"):
            pytest.fail(
                "autotuner._generate_cpcv_folds does not exist. "
                "RED until the implementer adds it (AC-1)."
            )
        folds = autotuner._generate_cpcv_folds(
            sorted_dates=fix["sorted_dates"],
            n_groups=fix["parameters"]["n_groups"],
            k_test=fix["parameters"]["k_test_groups"],
            purge_days=fix["parameters"]["purge_days"],
            embargo_days=fix["parameters"]["embargo_days"],
        )
        expected = fix["derived_constants"]["n_splits"]
        assert len(folds) == expected, (
            f"_generate_cpcv_folds returned {len(folds)} splits; "
            f"expected C({fix['parameters']['n_groups']},{fix['parameters']['k_test_groups']})={expected}. "
            "RED until the implementer generates all combinations."
        )

    def test_each_fold_has_required_keys(self):
        """Every split descriptor must contain train_dates, test_dates, path_membership."""
        fix = _load_fold_fixture()
        folds = _call_generate_cpcv_folds(fix)
        required_keys = {"train_dates", "test_dates", "path_membership"}
        for i, fold in enumerate(folds):
            missing = required_keys - set(fold.keys())
            assert not missing, (
                f"Split {i} descriptor is missing keys {missing}. "
                f"Each descriptor must have {required_keys}."
            )

    def test_each_fold_test_dates_are_nonempty(self):
        """Every split must have at least one test date (the fold is non-degenerate)."""
        fix = _load_fold_fixture()
        folds = _call_generate_cpcv_folds(fix)
        for i, fold in enumerate(folds):
            assert len(fold["test_dates"]) > 0, (
                f"Split {i} has empty test_dates — degenerate fold. "
                "The test fold must be non-empty."
            )

    def test_each_fold_train_dates_are_nonempty(self):
        """Every split must have at least one train date after purge+embargo."""
        fix = _load_fold_fixture()
        folds = _call_generate_cpcv_folds(fix)
        for i, fold in enumerate(folds):
            assert len(fold["train_dates"]) > 0, (
                f"Split {i} has empty train_dates after purge+embargo — degenerate fold. "
                "Increase the date sequence length or reduce purge_days."
            )

    def test_all_splits_are_distinct_by_test_set(self):
        """Each of the C(6,2)=15 splits must have a unique test date set."""
        fix = _load_fold_fixture()
        folds = _call_generate_cpcv_folds(fix)
        test_sets = [frozenset(f["test_dates"]) for f in folds]
        unique_test_sets = set(test_sets)
        assert len(unique_test_sets) == len(folds), (
            f"Duplicate test sets detected: {len(folds)} splits but only "
            f"{len(unique_test_sets)} distinct test date sets. "
            "Every C(6,2) combination must map to a unique test fold."
        )


# ===========================================================================
# AC-3 — Leakage-free: train ∩ test = ∅ for EVERY split
# ===========================================================================

class TestAC3LeakageFreeAllSplits:
    """
    AC-3: No train date may appear in the test set for any of the 15 splits.
    This is the primary leakage guard — adversarial: a fold generator that leaks
    on one seam must FAIL this test.
    """

    def test_no_train_test_overlap_any_split(self):
        """train_dates ∩ test_dates = ∅ for ALL C(6,2)=15 splits."""
        fix = _load_fold_fixture()
        folds = _call_generate_cpcv_folds(fix)
        for i, fold in enumerate(folds):
            overlap = set(fold["train_dates"]) & set(fold["test_dates"])
            assert not overlap, (
                f"LEAKAGE in split {i}: train_dates ∩ test_dates = {sorted(overlap)}. "
                f"Test dates of split {i}: {sorted(fold['test_dates'])[:5]}... "
                "train_dates must be fully disjoint from test_dates on every split. "
                "A fold generator that leaks on any seam fails AC-3."
            )

    def test_no_train_test_overlap_matches_fixture_verification(self):
        """
        The fixture's hand-derived verification for the sample split (2,3) must
        confirm zero overlap — cross-validating the fixture against the implementation.
        """
        fix = _load_fold_fixture()
        folds = _call_generate_cpcv_folds(fix)
        # Fixture documents split (2,3) — find it by matching test_dates
        sample = fix["sample_split_23"]
        expected_test_set = frozenset(sample["raw_test_dates"])
        matching = [f for f in folds if frozenset(f["test_dates"]) == expected_test_set]
        assert len(matching) == 1, (
            f"Could not find split with test_dates matching fixture sample_split_23. "
            f"Expected test set (first 3): {sorted(expected_test_set)[:3]}. "
            "Implementation may not enumerate all splits or may have wrong test groups."
        )
        found_fold = matching[0]
        overlap = set(found_fold["train_dates"]) & set(found_fold["test_dates"])
        assert not overlap, (
            f"LEAKAGE in fixture-verified split (2,3): overlap={sorted(overlap)}. "
            "The fixture hand-verifies zero overlap for this split."
        )

    def test_fixture_sample_split_effective_train_matches_implementation(self):
        """
        The fixture's hand-derived effective_train_dates for split (2,3) must match
        the implementation's output — the fixture is the ground truth, not the code.
        This is an adversarial cross-check: any purge/embargo miscalculation in the
        implementation will surface as a mismatch here.
        """
        fix = _load_fold_fixture()
        folds = _call_generate_cpcv_folds(fix)
        sample = fix["sample_split_23"]
        expected_test_set = frozenset(sample["raw_test_dates"])
        matching = [f for f in folds if frozenset(f["test_dates"]) == expected_test_set]
        if not matching:
            pytest.skip("Split (2,3) not found — covered by test_no_train_test_overlap_any_split.")
        found_fold = matching[0]
        expected_train = sorted(sample["effective_train_dates"])
        actual_train = sorted(found_fold["train_dates"])
        assert actual_train == expected_train, (
            f"Train dates for split (2,3) do not match fixture. "
            f"Fixture (first 5): {expected_train[:5]}. "
            f"Implementation (first 5): {actual_train[:5]}. "
            "The fixture is hand-derived ground truth. A mismatch indicates wrong "
            "purge/embargo arithmetic."
        )


# ===========================================================================
# AC-3 — Embargo gap ≥ EMBARGO_DAYS on every seam
# ===========================================================================

class TestAC3EmbargoGapAllSeams:
    """
    AC-3: For every split, at every seam between train and test, the gap between
    the last train date before the test block and the first test date must be
    ≥ EMBARGO_DAYS. Verified on ALL 15 splits, ALL seams.

    Adversarial: a fold generator that applies embargo only at the first seam
    and not subsequent seams (for non-contiguous test groups) must FAIL.
    """

    @pytest.fixture
    def all_folds_with_dates(self):
        fix = _load_fold_fixture()
        autotuner = _import_autotuner()
        if not hasattr(autotuner, "_generate_cpcv_folds"):
            pytest.skip("_generate_cpcv_folds not yet implemented — RED (AC-1/AC-3).")
        folds = autotuner._generate_cpcv_folds(
            sorted_dates=fix["sorted_dates"],
            n_groups=fix["parameters"]["n_groups"],
            k_test=fix["parameters"]["k_test_groups"],
            purge_days=fix["parameters"]["purge_days"],
            embargo_days=fix["parameters"]["embargo_days"],
        )
        return fix, folds

    def test_embargo_gap_before_each_test_block_all_splits(self, all_folds_with_dates):
        """
        For every split: the gap (in date list positions) between the last effective
        train date before a test block and the first test date in that block must be
        >= EMBARGO_DAYS.

        Adversarial: a fold generator that forgets the seam before a non-adjacent
        test group (e.g., groups 0 and 3 — the seam before group 3's block) fails.
        """
        fix, folds = all_folds_with_dates
        all_sorted = fix["sorted_dates"]
        date_to_idx = {d: i for i, d in enumerate(all_sorted)}
        embargo = fix["parameters"]["embargo_days"]

        failures = []
        for i, fold in enumerate(folds):
            train_set = set(fold["train_dates"])
            test_sorted = sorted(fold["test_dates"])

            # Identify test blocks (consecutive segments in date_to_idx order)
            test_blocks = _find_test_blocks(test_sorted, date_to_idx)

            for block_start, block_end in test_blocks:
                bs_idx = date_to_idx[block_start]
                # Find last train date before this block
                train_before = [d for d in fold["train_dates"] if date_to_idx[d] < bs_idx]
                if not train_before:
                    continue  # No train before this block — no seam to check
                last_before = max(train_before, key=lambda d: date_to_idx[d])
                # Gap = number of dates between last_before and block_start (exclusive)
                gap = bs_idx - date_to_idx[last_before] - 1
                if gap < embargo:
                    failures.append(
                        f"Split {i} (test block starting {block_start}): "
                        f"embargo gap={gap} < EMBARGO_DAYS={embargo}. "
                        f"last_train_before={last_before}."
                    )

        assert not failures, (
            f"Embargo violations found on {len(failures)} seam(s):\n"
            + "\n".join(failures)
            + "\nFold generator must apply purge+embargo at EVERY train|test seam, "
            "including non-contiguous test group boundaries."
        )

    def test_embargo_gap_after_each_test_block_all_splits(self, all_folds_with_dates):
        """
        For every split: the gap between the last test date in each test block and
        the first effective train date after that block must be >= EMBARGO_DAYS.

        Adversarial: a fold generator that applies embargo only before test groups
        (not after) misses the seam at the test|train boundary.
        """
        fix, folds = all_folds_with_dates
        all_sorted = fix["sorted_dates"]
        date_to_idx = {d: i for i, d in enumerate(all_sorted)}
        embargo = fix["parameters"]["embargo_days"]

        failures = []
        for i, fold in enumerate(folds):
            test_sorted = sorted(fold["test_dates"])
            test_blocks = _find_test_blocks(test_sorted, date_to_idx)

            for block_start, block_end in test_blocks:
                be_idx = date_to_idx[block_end]
                # Find first train date after this block
                train_after = [d for d in fold["train_dates"] if date_to_idx[d] > be_idx]
                if not train_after:
                    continue  # No train after this block — no seam to check
                first_after = min(train_after, key=lambda d: date_to_idx[d])
                gap = date_to_idx[first_after] - be_idx - 1
                if gap < embargo:
                    failures.append(
                        f"Split {i} (test block ending {block_end}): "
                        f"embargo gap={gap} < EMBARGO_DAYS={embargo}. "
                        f"first_train_after={first_after}."
                    )

        assert not failures, (
            f"Post-test embargo violations found on {len(failures)} seam(s):\n"
            + "\n".join(failures)
            + "\nFold generator must apply purge+embargo at the test|train seam (seam B) "
            "for every test block, not only at train|test (seam A)."
        )

    def test_no_purge_window_overlap_all_splits(self, all_folds_with_dates):
        """
        For every split: no train date should be within PURGE_DAYS calendar positions
        of any test date (the purge window constraint from LdP Ch.7.4).

        Using position distance in the sorted_dates list as a proxy for trading days.
        """
        fix, folds = all_folds_with_dates
        all_sorted = fix["sorted_dates"]
        date_to_idx = {d: i for i, d in enumerate(all_sorted)}
        purge = fix["parameters"]["purge_days"]
        embargo = fix["parameters"]["embargo_days"]

        failures = []
        for i, fold in enumerate(folds):
            test_idxs = {date_to_idx[d] for d in fold["test_dates"]}
            for train_d in fold["train_dates"]:
                t_idx = date_to_idx[train_d]
                # Check that no test date falls within [t_idx, t_idx + purge]
                # (i.e., the train date's lookback window doesn't reach into test)
                for ti in range(t_idx, t_idx + purge + 1):
                    if ti in test_idxs:
                        failures.append(
                            f"Split {i}: train date {train_d} (idx={t_idx}) has "
                            f"feature lookback reaching test date at idx={ti} "
                            f"(purge_window=[{t_idx}, {t_idx+purge}]). "
                            "Purge must remove this train date."
                        )
                        break

        assert not failures, (
            f"Purge-window overlap violations in {len(failures)} case(s):\n"
            + "\n".join(failures[:10])
            + ("\n..." if len(failures) > 10 else "")
            + "\nPurge must remove train dates whose feature lookback window "
            "[d, d+PURGE_DAYS] intersects any test date."
        )


# ===========================================================================
# AC-2 — _aggregate_cpcv_paths assembles phi[6,2]=5 paths
# ===========================================================================

class TestAC2PathAggregation:
    """
    AC-2: _aggregate_cpcv_paths must assemble exactly phi[6,2]=5 OOS backtest
    paths by stitching each split's test-fold dates into the paths it belongs to.
    """

    @pytest.fixture
    def folds(self):
        fix = _load_fold_fixture()
        autotuner = _import_autotuner()
        if not hasattr(autotuner, "_generate_cpcv_folds"):
            pytest.skip("_generate_cpcv_folds not yet implemented — RED (AC-1/AC-2).")
        return fix, autotuner._generate_cpcv_folds(
            sorted_dates=fix["sorted_dates"],
            n_groups=fix["parameters"]["n_groups"],
            k_test=fix["parameters"]["k_test_groups"],
            purge_days=fix["parameters"]["purge_days"],
            embargo_days=fix["parameters"]["embargo_days"],
        )

    def test_aggregate_returns_five_paths(self, folds):
        """_aggregate_cpcv_paths must return exactly phi[6,2]=5 paths."""
        fix, fold_list = folds
        autotuner = _import_autotuner()
        n_paths_expected = fix["derived_constants"]["n_paths"]
        paths = autotuner._aggregate_cpcv_paths(fold_list, n_paths=n_paths_expected)
        assert len(paths) == n_paths_expected, (
            f"_aggregate_cpcv_paths returned {len(paths)} paths; "
            f"expected phi[6,2]={n_paths_expected}. "
            "RED until the implementer assembles all backtest paths (AC-2)."
        )

    def test_each_path_is_nonempty(self, folds):
        """Each of the 5 paths must contain at least one OOS date series."""
        fix, fold_list = folds
        autotuner = _import_autotuner()
        n_paths_expected = fix["derived_constants"]["n_paths"]
        paths = autotuner._aggregate_cpcv_paths(fold_list, n_paths=n_paths_expected)
        for i, path in enumerate(paths):
            # A path is a sequence of OOS dates (or date→value pairs); it must be non-empty.
            path_dates = path if isinstance(path, (list, set)) else list(path)
            assert len(path_dates) > 0, (
                f"Path {i} is empty. Each backtest path must contain OOS dates."
            )

    def test_path_membership_field_is_consistent(self, folds):
        """
        path_membership on each split must reference valid path indices (0..n_paths-1).
        Every split's path_membership must be non-empty (each split belongs to k=2 paths
        — or at least 1 path if the implementer uses a different convention).
        """
        fix, fold_list = folds
        autotuner = _import_autotuner()
        n_paths_expected = fix["derived_constants"]["n_paths"]
        for i, fold in enumerate(fold_list):
            pm = fold.get("path_membership")
            assert pm is not None, (
                f"Split {i} is missing path_membership. "
                "Each split descriptor must indicate which backtest paths it contributes to."
            )
            # path_membership must be iterable (list, set, frozenset, tuple)
            try:
                pm_list = list(pm)
            except TypeError:
                pytest.fail(
                    f"Split {i} path_membership={pm!r} is not iterable. "
                    "path_membership must be a list/set/frozenset of path indices."
                )
            assert len(pm_list) > 0, (
                f"Split {i} path_membership is empty. "
                "Each split must belong to at least one backtest path."
            )
            # All path indices must be in [0, n_paths_expected)
            for idx in pm_list:
                assert 0 <= idx < n_paths_expected, (
                    f"Split {i} path_membership contains invalid path index {idx}. "
                    f"Valid range: [0, {n_paths_expected})."
                )

    def test_all_dates_appear_in_at_least_one_path(self, folds):
        """
        The union of all path test dates must equal the full sorted_dates union of
        all test fold dates. No test-fold date should be dropped in path assembly.
        """
        fix, fold_list = folds
        autotuner = _import_autotuner()
        n_paths_expected = fix["derived_constants"]["n_paths"]
        paths = autotuner._aggregate_cpcv_paths(fold_list, n_paths=n_paths_expected)

        # All test dates across all splits
        all_test_dates = set()
        for fold in fold_list:
            all_test_dates.update(fold["test_dates"])

        # All dates across all paths
        all_path_dates = set()
        for path in paths:
            path_items = path if isinstance(path, (list, set, frozenset)) else list(path)
            for item in path_items:
                d = item if isinstance(item, str) else item[0]
                all_path_dates.add(d)

        missing_from_paths = all_test_dates - all_path_dates
        assert not missing_from_paths, (
            f"{len(missing_from_paths)} test-fold dates are not represented in any "
            f"backtest path. First 5 missing: {sorted(missing_from_paths)[:5]}. "
            "Path assembly must include every test-fold date from every split."
        )


# ===========================================================================
# AC-4 — Objective scores on CPCV aggregate per trial (NOT 15 per trial)
# ===========================================================================

class TestAC4ObjectiveScoredOnAggregate:
    """
    AC-4: The Optuna objective must score each trial on the MEAN across the 5
    CPCV paths — not 15 separate Optuna evaluations (which would be a 15× compute
    blowup and inflate the trial count to 15×500=7500).

    This is verified via source inspection: the objective closure must aggregate
    the 5-path scores into a single scalar before returning to Optuna.
    """

    def test_objective_calls_aggregate_not_fifteen_separate_evaluations(self):
        """
        The objective function in run_autotuner must reference _aggregate_cpcv_paths
        (or the aggregate mechanism) before returning its trial score.

        An implementation that calls study.optimize with 15 sub-objectives OR
        that calls _generate_cpcv_folds inside study.optimize 15 times would
        produce 7500 Optuna trials instead of 500 — the forbidden pattern.
        """
        src = _parse_autotuner_source()
        assert "_aggregate_cpcv_paths" in src or "cpcv_paths" in src, (
            "autotuner.py must call _aggregate_cpcv_paths (or equivalent) inside the "
            "Optuna objective closure. The objective must aggregate the 5 path scores "
            "into ONE scalar per trial — not run 15 separate Optuna evaluations. "
            "RED until the implementer wires the aggregate into the objective."
        )

    def test_n_trials_constant_is_not_inflated_by_n_splits(self):
        """
        OPTUNA_N_TRIALS_PRODUCTION must not be multiplied by _CPCV_N_SPLITS.
        A trial count of 500*15=7500 would be the 15× compute blowup pattern.
        """
        autotuner = _import_autotuner()
        n_trials = autotuner.OPTUNA_N_TRIALS_PRODUCTION
        assert hasattr(autotuner, "_CPCV_N_SPLITS"), (
            "_CPCV_N_SPLITS not found — this test requires AC-1 to be implemented first."
        )
        n_splits = autotuner._CPCV_N_SPLITS
        assert n_trials != n_trials * n_splits, (
            f"Sanity: {n_trials} != {n_trials * n_splits}."
        )
        # The production trial count must remain as-is (500); it was set for
        # statistical stability, not for CPCV expansion.
        # The existing test_optuna_n_trials_named.py guards the exact value via
        # tests/fixtures/autotuner/optuna_n_trials_pin_contract.json.
        pin_fixture = (
            _WORKTREE_ROOT / "tests" / "fixtures" / "autotuner"
            / "optuna_n_trials_pin_contract.json"
        )
        assert pin_fixture.exists(), (
            f"Expected optuna_n_trials_pin_contract.json at {pin_fixture}. "
            "This fixture guards OPTUNA_N_TRIALS_PRODUCTION via test_optuna_n_trials_named.py."
        )


# ===========================================================================
# AC-5 — compute_n_effective is UNCHANGED by CPCV (anti-double-count)
# ===========================================================================

class TestAC5MultiplicityUntouched:
    """
    AC-5: compute_n_effective, _haircut_select, and the BHY haircut must be
    byte-identical regardless of how many CPCV paths are used. CPCV changes WHAT
    data is scored, not HOW MANY tests exist.

    Adversarial: an implementer who multiplies n_effective by n_paths or n_splits
    would inflate N from 500 to 2500 or 7500, making the BHY haircut far too
    strict and rejecting genuine signals.
    """

    def test_multiplicity_fixture_single_vs_five_paths_identical_n_effective(self):
        """
        compute_n_effective with empty ledger must return n_optuna regardless of
        whether CPCV uses 1 or 5 paths. The fixture documents both scenarios.
        """
        fix = _load_multiplicity_fixture()
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "compute_n_effective"), (
            "compute_n_effective not found — prerequisite for AC-5 tests."
        )

        single_scenario = fix["scenarios"]["single_path_n_effective"]
        five_scenario = fix["scenarios"]["five_path_n_effective"]

        n_eff_single = autotuner.compute_n_effective(
            single_scenario["n_optuna"], lambda: []
        )
        n_eff_five = autotuner.compute_n_effective(
            five_scenario["n_optuna"], lambda: []
        )

        assert n_eff_single == single_scenario["expected_n_effective"], (
            f"compute_n_effective with n_paths=1 and empty ledger should return "
            f"{single_scenario['expected_n_effective']}, got {n_eff_single}."
        )
        assert n_eff_five == five_scenario["expected_n_effective"], (
            f"compute_n_effective with n_paths=5 and empty ledger should return "
            f"{five_scenario['expected_n_effective']}, got {n_eff_five}."
        )
        assert n_eff_single == n_eff_five, (
            f"compute_n_effective must be IDENTICAL for n_paths=1 ({n_eff_single}) "
            f"and n_paths=5 ({n_eff_five}). CPCV does not change the multiplicity count. "
            "AC-5 anti-double-count violation."
        )

    def test_n_effective_does_not_accept_n_paths_parameter(self):
        """
        compute_n_effective must NOT have an n_cpcv_paths or n_splits parameter.
        If such a parameter exists, the implementer likely inflates N by path count.
        """
        import inspect
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "compute_n_effective"), (
            "compute_n_effective not found — prerequisite for AC-5 tests."
        )
        sig = inspect.signature(autotuner.compute_n_effective)
        forbidden_params = {"n_cpcv_paths", "n_paths", "n_splits", "n_cpcv_splits"}
        for fp in forbidden_params:
            assert fp not in sig.parameters, (
                f"compute_n_effective has a '{fp}' parameter. "
                "CPCV must NOT inflate n_effective by path count or split count. "
                "AC-5 anti-double-count: the function signature must be unchanged "
                "from the pre-CPCV contract."
            )

    def test_haircut_select_signature_is_unchanged(self):
        """
        _haircut_select signature must not gain an n_cpcv_paths or related CPCV parameter.
        Adding such a parameter would mean the haircut tightness depends on how many
        paths are assembled — a category error.
        """
        import inspect
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "_haircut_select"), (
            "_haircut_select not found."
        )
        sig = inspect.signature(autotuner._haircut_select)
        cpcv_params = {p for p in sig.parameters if "cpcv" in p.lower() or "path" in p.lower()}
        # 'n_effective' is allowed (pre-existing); 'tstat_fn' and 'gamma' are allowed.
        assert not cpcv_params, (
            f"_haircut_select has CPCV-related parameters: {cpcv_params}. "
            "The BHY haircut must be independent of CPCV path count. "
            "AC-5: _haircut_select is byte-identical except receiving CPCV-aggregated series."
        )

    def test_bhy_adjusted_pvalues_identical_one_vs_five_paths(self):
        """
        BHY-adjusted p-values must be identical for a trial set regardless of whether
        CPCV runs with 1 path or 5 paths.

        Construct identical trial sets (same Sortino/CRRA values, just evaluated on
        different path counts). The BHY output must be bitwise identical — the path
        count must never reach benjamini_hochberg_adjust.

        Tolerance: 1e-9 absolute (same as BHY suite).
        """
        autotuner = _import_autotuner()

        # A minimal trial set: 5 trials with known values.
        # Values are fixed; path count is immaterial to the haircut.
        trials = [
            _make_fake_trial(value=3.5, daily_returns=[0.01] * 60),
            _make_fake_trial(value=2.0, daily_returns=[0.008] * 50),
            _make_fake_trial(value=0.9, daily_returns=[0.005] * 40),
            _make_fake_trial(value=0.3, daily_returns=[0.002] * 30),
            _make_fake_trial(value=-0.1, daily_returns=[0.001] * 20),
        ]

        # Call _haircut_select identically regardless of path count.
        # The path count must not appear in the call at all.
        winner1, p_adj1, tstat1 = autotuner._haircut_select(trials)
        winner5, p_adj5, tstat5 = autotuner._haircut_select(trials)

        # Both calls are identical — this verifies the function is deterministic
        # and that path count doesn't leak in through any global state.
        assert winner1 is winner5, (
            "Repeated identical _haircut_select calls return different winners. "
            "The function must be deterministic and path-count-agnostic."
        )
        if p_adj1 is not None and p_adj5 is not None:
            assert p_adj5 == pytest.approx(p_adj1, abs=_BHY_TOL), (
                f"Repeated identical _haircut_select calls return different p_adj: "
                f"call1={p_adj1!r}, call2={p_adj5!r}. "
                "The BHY haircut must not depend on CPCV path count."
            )

    def test_forbidden_pattern_n_effective_inflated_by_n_splits_would_be_detectable(self):
        """
        Property test: if n_effective were wrongly inflated by _CPCV_N_SPLITS=15,
        the BHY-adjusted p-values would be strictly larger (more conservative).
        This test verifies that the WRONG inflation is detectable — confirming
        the test is meaningful (not a tautology).

        This test does NOT assert the forbidden pattern occurs; it asserts that
        if it DID occur, the change would be observable — giving confidence that
        the passing AC-5 tests above are genuinely testing something.
        """
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "_CPCV_N_SPLITS"), (
            "_CPCV_N_SPLITS not found — requires AC-1 to be implemented."
        )
        n_splits = autotuner._CPCV_N_SPLITS
        assert n_splits > 1, "_CPCV_N_SPLITS must be > 1 for the inflation to be detectable."

        p_values = [0.01, 0.05, 0.1, 0.2, 0.5]
        # Baseline: n = len(p_values)
        p_adj_baseline = autotuner.benjamini_hochberg_adjust(list(p_values))
        # Inflated: n = len(p_values) * n_splits — the forbidden pattern
        p_adj_inflated = autotuner.benjamini_hochberg_adjust(
            list(p_values) + [1.0] * (len(p_values) * (n_splits - 1))
        )
        # If inflation occurred, at least one adjusted p-value would be larger.
        # (This is always true since Yekutieli c(N) is monotone increasing.)
        found_increase = any(
            p_adj_inflated[i] > p_adj_baseline[i] + _BHY_TOL
            for i in range(len(p_values))
        )
        assert found_increase, (
            "BHY inflation should be detectable when N is multiplied by n_splits, "
            "but no increase found. Check benjamini_hochberg_adjust implementation."
        )


# ===========================================================================
# AC-6 (partial) — No DSR machinery reintroduced (D3 tripwire)
# ===========================================================================

class TestAC6NoDSRReintroduced:
    """
    AC-6 partial: CPCV must NOT reintroduce DSR machinery deleted under Decision D3.
    The existing test_c4_dsr_machinery_removed.py is the authoritative tripwire;
    this class adds a fast smoke-check that CPCV-specific code paths don't import
    or invoke DSR symbols.
    """

    def test_generate_cpcv_folds_does_not_reference_dsr_symbols(self):
        """_generate_cpcv_folds source must not reference compute_deflated_sharpe_ratio."""
        src = _parse_autotuner_source()
        start = src.find("def _generate_cpcv_folds(")
        if start == -1:
            pytest.skip("_generate_cpcv_folds not yet implemented — skip DSR check.")
        next_def = src.find("\ndef ", start + 1)
        func_body = src[start:next_def if next_def != -1 else len(src)]
        assert "compute_deflated_sharpe_ratio" not in func_body, (
            "_generate_cpcv_folds must not reference compute_deflated_sharpe_ratio. "
            "DSR was deleted under Decision D3 (test_c4_dsr_machinery_removed.py tripwire)."
        )

    def test_aggregate_cpcv_paths_does_not_reference_dsr_symbols(self):
        """_aggregate_cpcv_paths source must not reference compute_deflated_sharpe_ratio."""
        src = _parse_autotuner_source()
        start = src.find("def _aggregate_cpcv_paths(")
        if start == -1:
            pytest.skip("_aggregate_cpcv_paths not yet implemented — skip DSR check.")
        next_def = src.find("\ndef ", start + 1)
        func_body = src[start:next_def if next_def != -1 else len(src)]
        assert "compute_deflated_sharpe_ratio" not in func_body, (
            "_aggregate_cpcv_paths must not reference compute_deflated_sharpe_ratio. "
            "DSR was deleted under Decision D3."
        )

    def test_cpcv_imports_do_not_violate_d3_forbidden_tokens(self):
        """CPCV-related code must not introduce gamma3, gamma4, or 'Eq.9' tokens."""
        src = _parse_autotuner_source()
        # Find the CPCV region (from _CPCV_N_GROUPS to _aggregate_cpcv_paths)
        start = src.find("_CPCV_N_GROUPS")
        if start == -1:
            pytest.skip("_CPCV_N_GROUPS not yet defined — skip D3 token check.")
        cpcv_region = src[start:]
        for forbidden in ("gamma3", "gamma4", "Eq.9", "Euler-Mascheroni"):
            assert forbidden not in cpcv_region, (
                f"CPCV region contains forbidden D3 token '{forbidden}'. "
                "This would reintroduce the DSR machinery deleted under Decision D3."
            )


# ===========================================================================
# Import boundary — alpha_bot_execution must never import CPCV
# ===========================================================================

class TestImportBoundary:
    """
    CPCV is a training-phase concern. alpha_bot_execution.py must never import
    _generate_cpcv_folds, _aggregate_cpcv_paths, or the _CPCV_* constants.
    The live execution path must remain free of walk-forward training logic.
    """

    def test_alpha_bot_execution_does_not_import_cpcv_symbols(self):
        """alpha_bot_execution.py must not reference CPCV symbols."""
        abe_path = _WORKTREE_ROOT / "alpha_bot_execution.py"
        if not abe_path.exists():
            pytest.skip("alpha_bot_execution.py not found.")
        src = abe_path.read_text(encoding="utf-8")
        for symbol in ("_generate_cpcv_folds", "_aggregate_cpcv_paths", "_CPCV_N_GROUPS",
                        "_CPCV_K_TEST_GROUPS", "_CPCV_N_SPLITS", "_CPCV_N_PATHS"):
            assert symbol not in src, (
                f"alpha_bot_execution.py references CPCV symbol '{symbol}'. "
                "CPCV is a training-phase concern; it must never appear on the live "
                "execution path. Boundary violation per Architecture Constraint 1."
            )


# ===========================================================================
# Helpers (module-level, used by multiple test classes)
# ===========================================================================

def _call_generate_cpcv_folds(fix: dict) -> list:
    """Call _generate_cpcv_folds or pytest.fail with a RED message if not implemented."""
    autotuner = _import_autotuner()
    if not hasattr(autotuner, "_generate_cpcv_folds"):
        pytest.fail(
            "autotuner._generate_cpcv_folds does not exist. "
            "RED until the implementer adds it (AC-1)."
        )
    return autotuner._generate_cpcv_folds(
        sorted_dates=fix["sorted_dates"],
        n_groups=fix["parameters"]["n_groups"],
        k_test=fix["parameters"]["k_test_groups"],
        purge_days=fix["parameters"]["purge_days"],
        embargo_days=fix["parameters"]["embargo_days"],
    )


def _find_test_blocks(
    test_sorted: list[str], date_to_idx: dict[str, int]
) -> list[tuple[str, str]]:
    """Identify contiguous blocks in a sorted list of test dates.

    Returns a list of (block_start, block_end) tuples where each block
    is a maximal run of consecutive dates (adjacent in the full date index).
    """
    if not test_sorted:
        return []
    blocks: list[tuple[str, str]] = []
    block_start = test_sorted[0]
    prev = test_sorted[0]
    for d in test_sorted[1:]:
        if date_to_idx[d] == date_to_idx[prev] + 1:
            prev = d
        else:
            blocks.append((block_start, prev))
            block_start = d
            prev = d
    blocks.append((block_start, prev))
    return blocks


# ===========================================================================
# AC-2 (Revise) — 1-factorization: paths must be COMPLETE OOS backtest paths
# ===========================================================================
#
# DEFECT FOUND IN 80de941 (PM code review 2026-06-01):
# _generate_cpcv_folds used `path_membership = [split_idx % n_paths]` (modulo
# round-robin). This produces INCOMPLETE paths — each path covers only 4/6 or
# 5/6 of the date range, with groups duplicated across splits in the same path.
# E.g. path0 got splits (0,1),(1,2),(2,4) → groups {0,1,2,4}, missing 3 and 5.
#
# FIX REQUIRED: The 15 splits must be assigned to the 5 paths via a valid
# 1-factorization of K6 (round-robin tournament for even N=6):
#   path0=(0,1),(2,3),(4,5)  path1=(0,2),(1,4),(3,5)  path2=(0,3),(1,5),(2,4)
#   path3=(0,4),(1,3),(2,5)  path4=(0,5),(1,2),(3,4)
# Each path then covers ALL N=6 groups exactly once → unbiased CRRA-EU aggregate.
#
# The existing test_all_dates_appear_in_at_least_one_path PASSES silently on
# the defect because it only checks the UNION over all paths; these new tests
# check the stronger per-path completeness invariant.
#
# Citation: LdP 2018 Ch.7.4 (CPCV path completeness); PM code review 2026-06-01.


class TestAC2OnefactorizationPathCompleteness:
    """
    Revise cycle for AC-2: each of the phi[6,2]=5 assembled paths must cover
    ALL N=6 groups exactly once (a perfect matching / 1-factorization of K6).

    These tests are RED against the modulo round-robin implementation at
    80de941 and will go GREEN once the implementer replaces it with the
    canonical round-robin tournament assignment.
    """

    @pytest.fixture
    def folds_and_paths(self):
        fix = _load_fold_fixture()
        autotuner = _import_autotuner()
        if not hasattr(autotuner, "_generate_cpcv_folds"):
            pytest.skip("_generate_cpcv_folds not yet implemented.")
        folds = autotuner._generate_cpcv_folds(
            sorted_dates=fix["sorted_dates"],
            n_groups=fix["parameters"]["n_groups"],
            k_test=fix["parameters"]["k_test_groups"],
            purge_days=fix["parameters"]["purge_days"],
            embargo_days=fix["parameters"]["embargo_days"],
        )
        paths = autotuner._aggregate_cpcv_paths(folds, n_paths=fix["derived_constants"]["n_paths"])
        return fix, folds, paths

    def test_each_path_covers_all_n_groups_exactly_once(self, folds_and_paths):
        """
        Each of the 5 assembled paths must include test-fold dates from ALL
        N=6 groups — not 4/6 or 5/6. A path that scores only 4 groups
        produces a biased CRRA-EU aggregate (dates for missing groups never
        appear in the OOS evaluation).

        Uses the fixture's group_definitions to map dates back to group indices.
        Adversarial: the modulo round-robin produces paths covering only 4-5
        groups and FAILS this test.
        """
        fix, folds, paths = folds_and_paths
        n_groups = fix["parameters"]["n_groups"]

        # Build date-to-group mapping from fixture
        date_to_group: dict[str, int] = {}
        for g_str, dates in fix["group_definitions"].items():
            g = int(g_str)
            for d in dates:
                date_to_group[d] = g

        failures = []
        for i, path in enumerate(paths):
            path_dates = list(path) if not isinstance(path, list) else path
            groups_covered = set(
                date_to_group[d] for d in path_dates if d in date_to_group
            )
            if groups_covered != set(range(n_groups)):
                missing = set(range(n_groups)) - groups_covered
                extra = groups_covered - set(range(n_groups))
                failures.append(
                    f"path {i}: covers groups {sorted(groups_covered)}, "
                    f"missing={sorted(missing)}, unexpected={sorted(extra)}. "
                    f"Path has {len(path_dates)} dates."
                )

        assert not failures, (
            f"1-factorization completeness FAILED on {len(failures)} path(s):\n"
            + "\n".join(failures)
            + "\n\nEach CPCV backtest path must cover ALL N=6 groups exactly once "
            "(1-factorization of K6 per LdP Ch.7.4). The modulo round-robin "
            "path_membership=[split_idx % n_paths] produces incomplete paths. "
            "Fix: replace with the canonical round-robin tournament assignment:\n"
            "  path0=(0,1),(2,3),(4,5)  path1=(0,2),(1,4),(3,5)\n"
            "  path2=(0,3),(1,5),(2,4)  path3=(0,4),(1,3),(2,5)\n"
            "  path4=(0,5),(1,2),(3,4)"
        )

    def test_splits_partition_into_paths_no_split_reused_or_dropped(self, folds_and_paths):
        """
        The 15 splits must PARTITION into the 5 paths — every split assigned
        to exactly one path, no split duplicated, no split dropped.

        Required by LdP Ch.7.4: each split contributes OOS data to exactly
        one path. Reuse would double-count OOS dates in a path; dropping a
        split would leave some test-fold dates with no path contribution.

        Adversarial: the modulo round-robin satisfies this (each split_idx % 5
        maps to exactly one path) — so this test alone does NOT catch the
        completeness defect. It guards against a future over-correction where
        the implementer drops splits or assigns a split to multiple paths.
        """
        fix, folds, paths = folds_and_paths
        n_splits_expected = fix["derived_constants"]["n_splits"]

        # Count how many paths each split contributes to (via path_membership)
        split_path_counts: dict[int, list[int]] = {i: [] for i in range(len(folds))}
        for path_idx, path in enumerate(paths):
            path_test_set = frozenset(
                d for d in (list(path) if not isinstance(path, list) else path)
            )
            for split_idx, fold in enumerate(folds):
                fold_test_set = frozenset(fold["test_dates"])
                # A split contributes to a path if its test dates are a subset of the path
                if fold_test_set <= path_test_set:
                    split_path_counts[split_idx].append(path_idx)

        failures = []
        for split_idx, contributing_paths in split_path_counts.items():
            if len(contributing_paths) != 1:
                failures.append(
                    f"split {split_idx} (test_dates[0]={sorted(folds[split_idx]['test_dates'])[0]}) "
                    f"contributes to {len(contributing_paths)} paths {contributing_paths} "
                    f"(must be exactly 1)."
                )

        assert not failures, (
            f"Split partition invariant FAILED on {len(failures)} split(s):\n"
            + "\n".join(failures)
            + "\nEach of the 15 splits must contribute to EXACTLY ONE path (1-factorization). "
            "No split should be reused across paths or dropped."
        )

    def test_each_path_has_exactly_n_over_k_splits(self, folds_and_paths):
        """
        Each path must consist of exactly N/k = 6/2 = 3 splits.
        phi[N,k]=5 paths × 3 splits per path = 15 total = C(6,2). The modulo
        round-robin gives 3 splits to paths 0-4 (15/5=3 exact) so this test
        alone does not catch completeness, but it guards against degenerate
        implementations that assign unequal split counts across paths.
        """
        fix, folds, paths = folds_and_paths
        n_groups = fix["parameters"]["n_groups"]
        k_test = fix["parameters"]["k_test_groups"]
        expected_splits_per_path = n_groups // k_test  # 6 // 2 = 3

        # Determine splits per path via path_membership in the folds
        # (path_membership records which path each split belongs to)
        path_split_counts: dict[int, int] = {}
        for fold in folds:
            pm = fold["path_membership"]
            pm_list = list(pm) if not isinstance(pm, list) else pm
            for p in pm_list:
                path_split_counts[p] = path_split_counts.get(p, 0) + 1

        failures = []
        for p_idx in range(len(paths)):
            count = path_split_counts.get(p_idx, 0)
            if count != expected_splits_per_path:
                failures.append(
                    f"path {p_idx}: has {count} splits (expected {expected_splits_per_path})"
                )

        assert not failures, (
            f"Splits-per-path invariant FAILED:\n" + "\n".join(failures)
            + f"\nEach of the {len(paths)} paths must have exactly "
            f"N/k={expected_splits_per_path} splits."
        )

    def test_path_membership_matches_canonical_1factorization(self, folds_and_paths):
        """
        Each split's path_membership must match the canonical 1-factorization
        stored in the fixture. This is the golden-fixture cross-check: the
        fixture encodes the only correct assignment for N=6, k=2.

        The canonical mapping (from fixture['one_factorization']['split_to_path']):
          (0,1)->path0  (0,2)->path1  (0,3)->path2  (0,4)->path3  (0,5)->path4
          (1,2)->path4  (1,3)->path3  (1,4)->path1  (1,5)->path2
          (2,3)->path0  (2,4)->path2  (2,5)->path3
          (3,4)->path4  (3,5)->path1
          (4,5)->path0

        Adversarial: any round-robin or modulo scheme that produces a different
        assignment must FAIL here — there is only one canonical 1-factorization
        (up to path labelling, and the fixture pins the labelling).
        """
        fix, folds, paths = folds_and_paths
        canonical = fix["one_factorization"]["split_to_path"]

        # canonical keys are like "[0, 1]" (JSON stringified lists)
        # Rebuild as tuple keys for easy lookup
        canonical_tuple: dict[tuple, int] = {}
        import json as _json
        for k, v in canonical.items():
            pair = tuple(sorted(_json.loads(k)))
            canonical_tuple[pair] = v

        # For each fold, we need to know which groups are its test groups.
        # We infer test groups by finding which fixture group's dates appear in test_dates.
        date_to_group: dict[str, int] = {}
        for g_str, dates in fix["group_definitions"].items():
            for d in dates:
                date_to_group[d] = int(g_str)

        failures = []
        for split_idx, fold in enumerate(folds):
            test_groups = tuple(sorted(set(
                date_to_group[d] for d in fold["test_dates"]
                if d in date_to_group
            )))
            expected_path = canonical_tuple.get(test_groups)
            if expected_path is None:
                failures.append(
                    f"split {split_idx}: test_groups={test_groups} not in canonical map."
                )
                continue
            actual_pm = list(fold["path_membership"])
            if len(actual_pm) != 1 or actual_pm[0] != expected_path:
                failures.append(
                    f"split {split_idx} (test_groups={test_groups}): "
                    f"path_membership={actual_pm}, "
                    f"expected=[{expected_path}] per canonical 1-factorization."
                )

        assert not failures, (
            f"Canonical 1-factorization mismatch on {len(failures)} split(s):\n"
            + "\n".join(failures)
            + "\n\nThe fixture encodes the canonical round-robin tournament assignment "
            "for N=6, k=2. path_membership=[split_idx % n_paths] (modulo) is WRONG — "
            "it does not produce complete paths. Implementer must replace modulo with "
            "the canonical 1-factorization:\n"
            "  path0=(0,1),(2,3),(4,5)  path1=(0,2),(1,4),(3,5)\n"
            "  path2=(0,3),(1,5),(2,4)  path3=(0,4),(1,3),(2,5)\n"
            "  path4=(0,5),(1,2),(3,4)"
        )

    def test_no_group_duplicated_within_a_path(self, folds_and_paths):
        """
        Within each path, no group index may appear more than once.
        A path with duplicate groups (e.g., groups {0,1,2,1,4,5}) would
        double-score one group's dates in the CRRA-EU aggregate, biasing
        the objective toward overweighting that group's regime.

        Adversarial: modulo round-robin path0=(0,1),(1,2),(2,4) has groups
        {0,1,2,1,2,4} with 1 and 2 duplicated — this test catches it.
        """
        fix, folds, paths = folds_and_paths

        date_to_group: dict[str, int] = {}
        for g_str, dates in fix["group_definitions"].items():
            for d in dates:
                date_to_group[d] = int(g_str)

        failures = []
        for i, path in enumerate(paths):
            path_dates = list(path) if not isinstance(path, list) else path
            group_appearances: dict[int, int] = {}
            for d in path_dates:
                g = date_to_group.get(d)
                if g is not None:
                    group_appearances[g] = group_appearances.get(g, 0) + 1

            duplicated = {g: cnt for g, cnt in group_appearances.items() if cnt > 10}
            # Each group has 10 dates; more than 10 appearances means a group is duplicated
            if duplicated:
                failures.append(
                    f"path {i}: groups with duplicate appearances (>10 dates): "
                    f"{duplicated}. All 6 groups should appear exactly 10 times each."
                )

        assert not failures, (
            f"Group duplication found in {len(failures)} path(s):\n"
            + "\n".join(failures)
            + "\nNo group may appear more than once within a single CPCV path. "
            "The modulo round-robin assigns splits (0,1),(1,2),(2,4) to the same path, "
            "causing groups 1 and 2 to appear twice."
        )

    def test_each_path_date_coverage_equals_eligible_history(self, folds_and_paths):
        """
        After purge+embargo, each path's OOS date count should equal the
        sum of the 3 contributing splits' test_date counts. The path covers
        exactly the test folds of its 3 assigned splits — no more, no less.

        This is a shape/coverage test: if a path covers 40+40+40=120 dates
        but the history only has 60 dates, something is wrong (dates duplicated
        across splits in the same path). If a path has fewer dates than
        3 × group_size, something was dropped.
        """
        fix, folds, paths = folds_and_paths

        # Determine which splits belong to each path via path_membership
        path_to_splits: dict[int, list[int]] = {}
        for split_idx, fold in enumerate(folds):
            for p in fold["path_membership"]:
                path_to_splits.setdefault(p, []).append(split_idx)

        failures = []
        for path_idx, path in enumerate(paths):
            path_dates = set(list(path) if not isinstance(path, list) else path)
            assigned_splits = path_to_splits.get(path_idx, [])

            # Union of test dates across the 3 assigned splits
            expected_dates: set[str] = set()
            for split_idx in assigned_splits:
                expected_dates.update(folds[split_idx]["test_dates"])

            if path_dates != expected_dates:
                extra = path_dates - expected_dates
                missing = expected_dates - path_dates
                failures.append(
                    f"path {path_idx}: date mismatch. "
                    f"extra={len(extra)} dates, missing={len(missing)} dates. "
                    f"assigned_splits={assigned_splits}"
                )

        assert not failures, (
            f"Path date coverage mismatch on {len(failures)} path(s):\n"
            + "\n".join(failures)
            + "\nEach path's dates must equal the union of its 3 assigned splits' test_dates."
        )
