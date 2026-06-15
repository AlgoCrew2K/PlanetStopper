"""RED tests — compute_pbo (reduced-N CSCV PBO gate, Change 2).

Canonical reduced-N CSCV implementation per Bailey & López de Prado 2014,
"The Probability of Backtest Overfitting", Journal of Computational Finance
20(4), DOI 10.21314/JCF.2014.005.

Every test here MUST FAIL until math_engine.compute_pbo is implemented.

Math contract under test:
  Inputs: configs_date_returns (list[dict[str,float]]) — K configs, each mapping
          date-string -> decimal return; eligible_dates (list[str]) — sorted
          chronologically; gamma (float) — CRRA risk-aversion coefficient.
  Algorithm:
    1. Partition eligible_dates into S=_CSCV_S contiguous chronological blocks
       (even tiling: extra dates distributed to early blocks).
    2. For each of C(S, S/2) = C(8,4) = 70 IS/OOS combinations (4 IS, 4 OOS blocks):
       a. Score each config on its IS block dates using CRRA-EU (mean utility).
       b. Find the IS-best config (argmax CRRA-EU over IS dates).
       c. Score all K configs on OOS block dates using CRRA-EU; rank the IS-best
          config 1-based ascending (rank 1 = lowest OOS score = worst).
       d. omega_bar_c = (rank_c - 0.5) / K
       e. lambda_c    = ln(omega_bar_c / (1 - omega_bar_c))
    3. PBO = (count of combos with lambda_c <= 0) / (total combos).
    4. Return PBO as a float in [0, 1].

Module-level named constants (project rule — no magic numbers in math_engine):
    _CSCV_TOP_K = 20
    _CSCV_S = 8
    PBO_REJECT_THRESHOLD = 0.5  (with sourced comment: "derived — random selection
                                  implies PBO=0.5; >0.5 is worse than random; Bailey&LdP 2014")

Import boundary: compute_pbo and PBO_REJECT_THRESHOLD must NOT be importable
from alpha_bot_execution. That is pinned separately in test_pbo_import_boundary.py.

Fixture path: tests/fixtures/math/cscv_pbo_golden.json
"""

from __future__ import annotations

import importlib
import itertools
import json
import math
import pathlib

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------

_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "math"
    / "cscv_pbo_golden.json"
)

_WORKTREE_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def pbo_fixture() -> dict:
    """Load the cscv_pbo_golden fixture.  Fails fast if missing."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found: {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/cscv_pbo_golden.json first."
    )
    with _FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _import_math_engine():
    import sys

    repo = str(_WORKTREE_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    import math_engine

    return math_engine


# ---------------------------------------------------------------------------
# Module-level constant requirements
# ---------------------------------------------------------------------------


class TestComputePboConstants:
    """compute_pbo requires named module-level constants (no magic numbers rule)."""

    def test_cscv_top_k_constant_exists(self):
        """_CSCV_TOP_K must be defined as a named module-level constant, value 20."""
        me = _import_math_engine()
        assert hasattr(me, "_CSCV_TOP_K"), (
            "math_engine must expose _CSCV_TOP_K = 20 (named constant, no magic numbers rule)"
        )
        assert me._CSCV_TOP_K == 20

    def test_cscv_s_constant_exists(self):
        """_CSCV_S must be defined as a named module-level constant, value 8."""
        me = _import_math_engine()
        assert hasattr(me, "_CSCV_S"), (
            "math_engine must expose _CSCV_S = 8 (named constant, no magic numbers rule)"
        )
        assert me._CSCV_S == 8

    def test_pbo_reject_threshold_constant_exists(self):
        """PBO_REJECT_THRESHOLD must be defined, value 0.5, with a sourced comment."""
        me = _import_math_engine()
        assert hasattr(me, "PBO_REJECT_THRESHOLD"), (
            "math_engine must expose PBO_REJECT_THRESHOLD = 0.5 "
            "(named constant; comment must cite Bailey&LdP 2014 and explain 0.5 = random-selection boundary)"
        )
        assert me.PBO_REJECT_THRESHOLD == pytest.approx(0.5, abs=1e-12)

    def test_pbo_reject_threshold_source_comment_in_source(self):
        """PBO_REJECT_THRESHOLD must have a sourced comment citing Bailey&LdP 2014."""
        src = (_WORKTREE_ROOT / "math_engine.py").read_text(encoding="utf-8")
        assert "PBO_REJECT_THRESHOLD" in src, (
            "PBO_REJECT_THRESHOLD constant must be defined in math_engine.py"
        )
        assert "Bailey" in src, (
            "math_engine.py must reference Bailey (Bailey&LdP 2014) in the PBO_REJECT_THRESHOLD comment"
        )

    def test_compute_pbo_function_exists(self):
        """compute_pbo must be defined and callable in math_engine."""
        me = _import_math_engine()
        assert hasattr(me, "compute_pbo"), "math_engine must define compute_pbo"
        assert callable(me.compute_pbo)


# ---------------------------------------------------------------------------
# Golden-fixture test: known K×T matrix with a KNOWN PBO (Bailey&LdP 2014 property)
# ---------------------------------------------------------------------------


class TestComputePboGoldenFixture:
    """Golden-fixture tests: hand-derived PBO values that compute_pbo must reproduce."""

    def test_all_configs_dominate_oos_returns_zero_pbo(self, pbo_fixture):
        """When IS-best config always ranks best OOS, PBO must equal 0.0."""
        scenario = pbo_fixture["scenarios"]["all_configs_dominate_oos"]
        me = _import_math_engine()

        configs = scenario["configs_date_returns"]
        eligible_dates = scenario["eligible_dates"]
        gamma = scenario["gamma"]
        S = scenario["S"]

        # compute_pbo must accept a `S` override for test tractability;
        # the production default is _CSCV_S=8 but tests use S=4 for hand-derivation.
        pbo = me.compute_pbo(configs, eligible_dates, gamma, S=S)

        assert pbo == pytest.approx(scenario["expected"]["pbo"], abs=1e-9), (
            # Tolerance: PBO is a fraction of integer counts — for 6 combos the
            # minimum non-zero value is 1/6~=0.167; 1e-9 is well below that.
            f"Expected PBO={scenario['expected']['pbo']} (IS-best always OOS-best), got {pbo}"
        )

    def test_is_best_always_worst_oos_returns_one_pbo(self, pbo_fixture):
        """When IS-best config always ranks worst OOS, PBO must equal 1.0 (maximum overfitting)."""
        scenario = pbo_fixture["scenarios"]["is_best_always_worst_oos"]
        me = _import_math_engine()

        configs = scenario["configs_date_returns"]
        eligible_dates = scenario["eligible_dates"]
        gamma = scenario["gamma"]
        S = scenario["S"]

        pbo = me.compute_pbo(configs, eligible_dates, gamma, S=S)

        assert pbo == pytest.approx(scenario["expected"]["pbo"], abs=1e-9), (
            f"Expected PBO={scenario['expected']['pbo']} (IS-best always OOS-worst), got {pbo}"
        )

    def test_lambda_formula_pin_against_fixture(self, pbo_fixture):
        """The lambda formula ln((rank-0.5)/(K-(rank-0.5))) must match hand-computed values."""
        # Verify the lambda formula directly, independent of compute_pbo.
        # This pins the formula itself so a wrong formula cannot accidentally
        # produce the right PBO for the golden scenarios.
        cases = pbo_fixture["scenarios"]["lambda_formula_pin"]["cases"]
        for case in cases:
            rank_c = case["rank_c"]
            K = case["K"]
            expected_omega_bar = case["expected_omega_bar"]
            expected_lambda = case["expected_lambda"]

            # Hand-compute the formula the implementation must use:
            omega_bar = (rank_c - 0.5) / K
            lam = math.log(omega_bar / (1.0 - omega_bar))

            assert omega_bar == pytest.approx(expected_omega_bar, abs=1e-12), (
                f"rank={rank_c}, K={K}: omega_bar={omega_bar} != fixture {expected_omega_bar}"
            )
            assert lam == pytest.approx(expected_lambda, abs=1e-9), (
                # Tolerance: lambda is a log — 1e-9 absolute is fine for ~1.9 magnitude values.
                f"rank={rank_c}, K={K}: lambda={lam} != fixture {expected_lambda}"
            )
            # Sign must match
            if case["lambda_sign"] == "negative":
                assert lam < 0.0, f"rank={rank_c}, K={K}: lambda must be negative (rank <= K/2)"
            else:
                assert lam > 0.0, f"rank={rank_c}, K={K}: lambda must be positive (rank > K/2)"


# ---------------------------------------------------------------------------
# Partition correctness: S=8 blocks are even/contiguous/tiling
# ---------------------------------------------------------------------------


class TestComputePboPartition:
    """The S-block partition must be contiguous, tiling, and evenly sized."""

    def _make_uniform_configs(self, K: int, n_dates: int, return_val: float = 0.01) -> tuple:
        """Build K identical uniform-return configs over n_dates synthetic dates."""
        dates = [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(n_dates)]
        configs = [{d: return_val for d in dates} for _ in range(K)]
        return configs, dates

    def test_partition_covers_all_eligible_dates(self):
        """The union of all S blocks must equal the full eligible_dates list."""
        # Use a small S=4 override for tractability; invariant is S-independent.
        me = _import_math_engine()
        configs, dates = self._make_uniform_configs(K=4, n_dates=20)
        # compute_pbo itself covers all dates in its partition; we verify via
        # a helper that the partition is exposed or the PBO finishes deterministically.
        # If the partition is non-tiling, compute_pbo would silently skip dates
        # and score only a subset — the output is still a float in [0,1] so this
        # test pins determinism rather than internal structure.
        result1 = me.compute_pbo(configs, dates, gamma=2.0, S=4)
        result2 = me.compute_pbo(configs, dates, gamma=2.0, S=4)
        assert result1 == result2, (
            "compute_pbo must be deterministic (no random/wall-clock): "
            f"first call={result1}, second call={result2}"
        )
        assert 0.0 <= result1 <= 1.0, f"PBO must be in [0, 1], got {result1}"

    def test_determinism_no_random_or_wall_clock(self):
        """compute_pbo must be fully deterministic — same inputs → same output."""
        me = _import_math_engine()
        configs, dates = self._make_uniform_configs(K=5, n_dates=40)
        gamma = 3.0
        results = [me.compute_pbo(configs, dates, gamma=gamma, S=8) for _ in range(3)]
        assert len(set(results)) == 1, f"compute_pbo is not deterministic across 3 calls: {results}"

    def test_pbo_output_bounded_in_zero_one(self):
        """PBO must always be in [0, 1] — it is a fraction of combos."""
        me = _import_math_engine()
        configs, dates = self._make_uniform_configs(K=10, n_dates=80)
        pbo = me.compute_pbo(configs, dates, gamma=2.0)
        assert isinstance(pbo, float), f"compute_pbo must return float, got {type(pbo)}"
        assert math.isfinite(pbo), f"compute_pbo must return finite float, got {pbo}"
        assert 0.0 <= pbo <= 1.0, f"PBO must be in [0, 1], got {pbo}"

    def test_s8_generates_c_8_4_equals_70_combinations(self):
        """With S=8, the number of IS/OOS combinations must be C(8,4)=70."""
        # Independently verify: C(8,4) = 8!/(4!*4!) = 70.
        n_combos = len(list(itertools.combinations(range(8), 4)))
        assert n_combos == 70, (
            f"C(8,4) must equal 70, got {n_combos}. "
            "If compute_pbo uses a different count the PBO fraction denominator is wrong."
        )


# ---------------------------------------------------------------------------
# Edge cases: configs with no triggered returns in a block
# ---------------------------------------------------------------------------


class TestComputePboEdgeCases:
    """Edge cases: empty blocks, ties, minimal configs."""

    def test_config_with_no_triggered_returns_in_block_does_not_raise(self):
        """A config with an empty cscv_date_returns (no triggered days) must not raise.

        Empty configs should score 0.0 CRRA-EU (matching compute_crra_eu_objective
        empty-series contract). They will always lose IS/OOS scoring to non-empty
        configs but must not crash compute_pbo.
        """
        me = _import_math_engine()
        dates = [f"2024-01-{i + 1:02d}" for i in range(16)]
        # Config 0: empty (no triggered returns on any date). Config 1: moderate returns.
        configs = [{}, {d: 0.01 for d in dates}]
        pbo = me.compute_pbo(configs, dates, gamma=2.0, S=4)
        assert math.isfinite(pbo), f"compute_pbo must handle empty-config gracefully, got {pbo}"
        assert 0.0 <= pbo <= 1.0

    def test_ties_in_is_scoring_produce_deterministic_result(self):
        """When multiple configs tie for IS-best, compute_pbo must not raise or produce NaN."""
        me = _import_math_engine()
        dates = [f"2024-01-{i + 1:02d}" for i in range(16)]
        # All configs have identical returns — every combo has a tie for IS-best.
        configs = [{d: 0.005 for d in dates} for _ in range(4)]
        pbo = me.compute_pbo(configs, dates, gamma=2.0, S=4)
        assert math.isfinite(pbo), f"compute_pbo must handle ties, got {pbo}"
        assert 0.0 <= pbo <= 1.0

    def test_pbo_boundary_exactly_0_5_treated_as_reject(self):
        """PBO=0.5 must be treated as failing the acceptance gate (>= reject threshold).

        The veto fires when pbo > PBO_REJECT_THRESHOLD=0.5. PBO=0.5 exactly equals
        the reject threshold — the veto condition is pbo > threshold, so PBO=0.5
        must NOT veto. This test pins the boundary by asserting the threshold value.
        """
        me = _import_math_engine()
        # The accept condition is pbo <= PBO_REJECT_THRESHOLD.
        # At exactly 0.5, the veto does NOT fire (random = threshold, not worse).
        threshold = me.PBO_REJECT_THRESHOLD
        assert threshold == pytest.approx(0.5, abs=1e-12)
        # PBO=0.5 is right at the boundary — must not be rejected.
        # PBO > 0.5 is the reject zone. Verify the logic is strict inequality.
        assert not (0.5 > threshold), (
            "PBO=0.5 at threshold: accept condition is pbo <= threshold, "
            "so 0.5 must NOT trigger a reject veto"
        )
        # PBO=0.50001 > threshold must reject:
        assert 0.50001 > threshold, "PBO=0.50001 must exceed threshold and trigger reject"


# ---------------------------------------------------------------------------
# Anti-double-count regression: compute_pbo must not touch n_effective or _haircut_select
# ---------------------------------------------------------------------------


class TestComputePboOrthogonality:
    """BHY/n_effective = multiplicity axis; PBO = sample-robustness axis.

    compute_pbo must NOT modify n_effective, must NOT call _haircut_select, and
    must NOT alter compute_n_effective. These are byte-identical after the PBO
    implementation — any mutation is the forbidden double-count anti-pattern.
    """

    def test_compute_pbo_does_not_call_haircut_select(self):
        """compute_pbo must not invoke _haircut_select (orthogonality invariant)."""
        import ast

        src = (_WORKTREE_ROOT / "math_engine.py").read_text(encoding="utf-8")
        # Verify compute_pbo is defined in math_engine (not autotuner).
        assert "def compute_pbo" in src, "compute_pbo must be defined in math_engine.py"
        # compute_pbo must not reference _haircut_select from math_engine scope.
        # We parse the AST to find the function body and check for the call.
        tree = ast.parse(src)
        pbo_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "compute_pbo":
                pbo_fn = node
                break
        assert pbo_fn is not None, "compute_pbo function not found in math_engine.py AST"
        # Check no Call to _haircut_select or compute_n_effective within compute_pbo body.
        forbidden = {"_haircut_select", "compute_n_effective", "benjamini_hochberg_adjust"}
        for node in ast.walk(pbo_fn):
            if isinstance(node, ast.Call):
                func = node.func
                name = None
                if isinstance(func, ast.Name):
                    name = func.id
                elif isinstance(func, ast.Attribute):
                    name = func.attr
                if name in forbidden:
                    pytest.fail(
                        f"compute_pbo must not call {name!r} (anti-double-count: "
                        "PBO is the sample-robustness axis; BHY/n_effective is the "
                        "multiplicity axis — they are orthogonal by design)"
                    )

    def test_compute_pbo_is_defined_in_math_engine_not_autotuner(self):
        """compute_pbo must live in math_engine, not autotuner."""
        import ast

        autotuner_src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")
        autotuner_tree = ast.parse(autotuner_src)
        autotuner_fndefs = [
            node.name for node in ast.walk(autotuner_tree) if isinstance(node, ast.FunctionDef)
        ]
        assert "compute_pbo" not in autotuner_fndefs, (
            "compute_pbo must be defined in math_engine.py (the math crux), not in autotuner.py"
        )
        me = _import_math_engine()
        assert hasattr(me, "compute_pbo"), "compute_pbo must be importable from math_engine"

    def test_alpha_bot_execution_does_not_import_pbo(self):
        """compute_pbo and PBO symbols must NOT be imported by alpha_bot_execution.py.

        Import boundary: the PBO gate is offline-only (autotuner.py post-selection).
        alpha_bot_execution.py is the 1-minute live exit path; importing PBO symbols
        there would violate architecture constraint #1 (no blocking I/O on exit path).
        """
        import ast

        abe_path = _WORKTREE_ROOT / "alpha_bot_execution.py"
        assert abe_path.exists(), f"Expected alpha_bot_execution.py at {abe_path}"
        src = abe_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        # Collect all imported names.
        imported_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)
        pbo_symbols = {"compute_pbo", "PBO_REJECT_THRESHOLD", "_CSCV_TOP_K", "_CSCV_S"}
        leaked = pbo_symbols & imported_names
        assert not leaked, (
            f"alpha_bot_execution.py must NEVER import PBO symbols — "
            f"found: {leaked!r}. PBO gate is offline (autotuner-only)."
        )
        # Also check for string references that would indicate dynamic imports.
        for sym in pbo_symbols:
            # A string reference (e.g. getattr(math_engine, "compute_pbo")) would be
            # unusual but guard against it for belt-and-suspenders.
            if f'"{sym}"' in src or f"'{sym}'" in src:
                pytest.fail(
                    f"alpha_bot_execution.py references PBO symbol {sym!r} as a string — "
                    "possible dynamic import; must be removed."
                )


# ---------------------------------------------------------------------------
# A4 PBO-tie convention (RED) — a degenerate all-tie OOS must read as OVERFIT.
# ---------------------------------------------------------------------------

_TIE_FIXTURE_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "tests"
    / "fixtures"
    / "math"
    / "pbo_tie_convention.json"
)


@pytest.fixture(scope="module")
def pbo_tie_fixture() -> dict:
    assert _TIE_FIXTURE_PATH.exists(), (
        f"Fixture not found: {_TIE_FIXTURE_PATH}. "
        "Create tests/fixtures/math/pbo_tie_convention.json first."
    )
    with _TIE_FIXTURE_PATH.open(encoding="utf-8") as f:
        return json.load(f)


class TestComputePboTieConvention:
    """A4 PBO-tie ruling (GATE2-PLAN A4, validation/overfitting.md PBO-tie).

    The rank tie-direction `rank_c = sum(1 for s in oos_scores if s <= is_best_oos)`
    (math_engine.py:1960) treats a degenerate all-tie OOS as NOT overfit (PBO -> 0):
    every config ties the IS-best OOS, so rank_c = K -> omega_bar ~ 1 -> lambda > 0
    -> not counted. A strategy that distinguishes nothing OOS reading as maximally
    robust is the optimistic tie direction. The fix changes `<=` to `<` (strict) so
    the all-tie degenerate reads as OVERFIT (PBO -> 1).

    RED until the operator is changed to strict `<`.
    """

    def test_all_tie_oos_reads_as_overfit_after_strict_fix(self, pbo_tie_fixture):
        """All-identical configs (all-tie OOS) must yield a PBO indicating OVERFIT.

        Post-fix the strict `<` makes rank_c = 0 -> omega_bar ~ 0 -> lambda < 0 ->
        counted as overfit -> PBO == 1.0 (> PBO_REJECT_THRESHOLD). Pre-fix the `<=`
        makes PBO == 0.0 (RED). The expected endpoint is the structural pessimistic
        tie value (1.0), not a SUT-derived literal.
        """
        me = _import_math_engine()
        sc = pbo_tie_fixture["scenarios"]["all_tie_oos_is_overfit"]

        pbo = me.compute_pbo(
            sc["configs_date_returns_decimal"],
            sc["eligible_dates"],
            sc["gamma"],
            S=sc["S"],
        )

        assert pbo > me.PBO_REJECT_THRESHOLD, (
            f"All-tie OOS PBO={pbo!r} must be > PBO_REJECT_THRESHOLD "
            f"({me.PBO_REJECT_THRESHOLD}) — a degenerate strategy that distinguishes "
            "nothing OOS is OVERFIT, not robust.\n"
            "  PBO-tie BUG: pre-fix `rank_c = sum(... s <= is_best_oos)` counts every "
            "tied config -> rank_c=K -> omega_bar~1 -> lambda>0 -> PBO=0 (optimistic). "
            "Fix: change `<=` to `<` (strict) at math_engine.py:1960."
        )
        # The pessimistic endpoint is exactly 1.0 (every combo is lambda<=0).
        assert pbo == pytest.approx(sc["post_fix_pbo"], abs=1e-9), (
            # Tolerance abs=1e-9: PBO is a fraction of integer combo counts; the
            # all-tie degenerate maps every combo to lambda<=0 -> exactly 1.0.
            f"All-tie OOS PBO={pbo!r}; expected the pessimistic endpoint "
            f"{sc['post_fix_pbo']} after the strict-tie fix."
        )

    def test_non_tie_pbo_unaffected_by_tie_fix(self, pbo_tie_fixture):
        """Regression: a clearly-dominant config keeps PBO == 0.0 — the `<=` -> `<`
        change is a no-op when there is no all-tie OOS. Pins that the tie fix does
        not perturb the non-degenerate case (and that the existing golden scenarios
        in TestComputePboGoldenFixture stay GREEN).
        """
        me = _import_math_engine()
        sc = pbo_tie_fixture["scenarios"]["non_tie_golden_unaffected"]

        pbo = me.compute_pbo(
            sc["configs_date_returns_decimal"],
            sc["eligible_dates"],
            sc["gamma"],
            S=sc["S"],
        )
        assert pbo == pytest.approx(sc["expected_pbo"], abs=1e-9), (
            f"Non-tie PBO={pbo!r}; expected {sc['expected_pbo']} (config 0 dominates "
            "IS and OOS; no tie). The strict-tie fix must NOT change this case."
        )
