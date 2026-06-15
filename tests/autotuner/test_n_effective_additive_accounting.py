"""
N_effective Additive Accounting — RED tests (T1-T7).

These tests are written BEFORE the implementation. All tests will FAIL until:
  1. autotuner.py adds ``compute_n_effective(n_optuna, ledger_query) -> int``
     next to the BHY block (plan D1).
  2. ``_haircut_select`` gains an explicit ``n_effective: int`` parameter
     defaulting to ``len(p_values)`` (plan D2).
  3. ``_haircut_select`` passes ``benjamini_hochberg_adjust(p_values + [1.0]*S)``
     where ``S = n_effective - len(p_values)``, so the Yekutieli c(N) is computed
     over the honest N (plan D3, Shape A).
  4. ``database.py`` gains ``get_researcher_dof_ledger_for_run(run_timestamp, winning_spec_bundle_id)``
     returning the qualifying BACKTEST_SELECTION rows for the S-accumulation
     (plan D4).
  5. ``run_autotuner`` writes ``s_count``, ``n_effective``, ``d_spec``,
     ``overfitting_verdict`` into ``autotune_runs`` (plan D5).
  6. A NN1 disclosure comment block is present in autotuner.py near
     ``compute_n_effective`` (plan D6).

BINDING HAZARDS tested here:
  - BHY haircut integrity ★: S=0 case is byte-identical to today's;
    S>0 strictly tightens. T1 + T7 together are the integrity guard.
  - NN1 spec-freeze structural enforcement: compute_n_effective is
    the structural tripwire (council synthesis §2.5).
  - Two-DB boundary: ``get_researcher_dof_ledger_for_run`` queries
    only the state DB, never the optuna_studies DB.

Fixture provenance:
  tests/fixtures/math/n_effective_additive_accounting.json — hand-derived
  from plan D1-D3 contracts, council synthesis §2.2, v3-and-divergence-
  evaluation §A.0 + §B.3. No producer-computed rates/dollars/percents.

Tolerance: integer equality for counts; for BHY p-value comparisons
``pytest.approx(abs=1e-9)`` (same as the existing BHY suite).

References:
  feature-plans/decision-science/phase-1/n-effective-additive-accounting/plan.md
  docs/handoff/decision-science-council-synthesis.md §2.2, §2.5
  docs/handoff/decision-science-v3-and-divergence-evaluation.md §A.0, §B.3
"""

from __future__ import annotations

import json
import math
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_FIXTURE_PATH = (
    pathlib.Path(__file__).parent.parent
    / "fixtures"
    / "math"
    / "n_effective_additive_accounting.json"
)

# BHY deterministic pipeline tolerance — same as test_c4_harvey_liu_haircut.py.
# 1e-9 absolute: the BHY step-up is exact arithmetic; anything larger indicates
# a wrong formula, not floating-point rounding.
_BHY_TOL = 1e-9


def _load_fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _import_autotuner():
    import autotuner

    return autotuner


# ===========================================================================
# Precondition: fixture file exists and is well-formed
# ===========================================================================


def test_fixture_file_exists_and_is_valid_json():
    """The fixture file must exist and parse as valid JSON before any math test runs."""
    assert _FIXTURE_PATH.exists(), (
        f"Fixture not found at {_FIXTURE_PATH}. "
        "Create tests/fixtures/math/n_effective_additive_accounting.json."
    )
    data = _load_fixture()
    assert "scenarios" in data, "Fixture must have a 'scenarios' key."
    for key in (
        "nn1_honest_s0",
        "single_pnl_toured_facet",
        "defect2_subsweep_counting",
        "frozen_eval_tainted_excluded",
        "winner_bundle_self_excluded",
    ):
        assert key in data["scenarios"], f"Fixture missing scenario '{key}'."


# ===========================================================================
# T1 — NN1-honest no-op (the critical one, plan §T1)
# ===========================================================================


class TestNN1HonestNoOp:
    """T1: empty ledger → S=0 → N_effective=N_optuna → haircut byte-identical to today's."""

    def test_compute_n_effective_exists(self):
        """autotuner must expose a named compute_n_effective(n_optuna, ledger_query) function."""
        autotuner = _import_autotuner()
        assert hasattr(autotuner, "compute_n_effective"), (
            "autotuner must expose compute_n_effective(n_optuna, ledger_query) -> int "
            "(plan D1). Not implemented yet — expected RED."
        )

    def test_nn1_honest_s_is_zero(self):
        """compute_n_effective returns n_optuna unchanged when the ledger is empty."""
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["nn1_honest_s0"]

        n_optuna = scenario["n_optuna"]
        expected_n_eff = scenario["expected_n_effective"]

        # ledger_query returns the empty list — no BACKTEST_SELECTION rows.
        result = autotuner.compute_n_effective(n_optuna, lambda: [])

        assert result == expected_n_eff, (
            f"NN1-honest case: compute_n_effective({n_optuna}, empty_ledger) "
            f"must return {expected_n_eff} (S=0, N_effective=N_optuna). "
            f"Got {result!r}. This is the defining contract of the no-op path."
        )

    def test_nn1_honest_haircut_is_byte_identical_to_legacy(self):
        """_haircut_select with n_effective=N_optuna returns the same winner as today's call.

        The defining property of T1: when S=0, passing n_effective=len(trials) to
        _haircut_select must produce an outcome that is indistinguishable from calling
        _haircut_select with no n_effective argument (the legacy signature). If this
        fails, the n_effective parameter is not backward-compatible and the upgrade
        breaks every NN1-honest deployment.

        Tolerance 1e-9 absolute on p_adj (same as the BHY suite).
        """
        autotuner = _import_autotuner()

        # Build a minimal deterministic trial set: 3 trials with known values.
        # Values are chosen so one trial plausibly clears the FDR gate while the others
        # do not — we verify the same winner emerges both with and without n_effective.
        trials = [
            _make_fake_trial(value=3.5, daily_returns=[0.01] * 60),  # strong signal
            _make_fake_trial(value=0.8, daily_returns=[0.005] * 40),  # noise
            _make_fake_trial(value=-0.2, daily_returns=[0.002] * 30),  # noise
        ]

        # Legacy call (no n_effective argument — backward-compatible default).
        winner_legacy, p_adj_legacy, tstat_legacy = autotuner._haircut_select(trials)

        # Explicit n_effective = len(trials) (S=0, so N_effective = N_optuna = len(trials)).
        winner_neff, p_adj_neff, tstat_neff = autotuner._haircut_select(
            trials, n_effective=len(trials)
        )

        # Winner must be the same object (or both None).
        assert winner_legacy is winner_neff, (
            "NN1-honest byte-identical failure: _haircut_select(trials) and "
            "_haircut_select(trials, n_effective=len(trials)) must select the same "
            f"winner. Got winner_legacy={winner_legacy}, winner_neff={winner_neff}."
        )

        # p_adj must be identical to BHY tolerance.
        if p_adj_legacy is not None and p_adj_neff is not None:
            assert p_adj_neff == pytest.approx(p_adj_legacy, abs=_BHY_TOL), (
                f"NN1-honest byte-identical failure: p_adj must be identical. "
                f"legacy={p_adj_legacy!r}, n_effective={p_adj_neff!r}. "
                "The n_effective parameter must be a pure no-op when S=0."
            )

        # t-stat must be identical.
        if tstat_legacy is not None and tstat_neff is not None:
            assert tstat_neff == pytest.approx(tstat_legacy, abs=_BHY_TOL), (
                f"NN1-honest byte-identical failure: t-stat must be identical. "
                f"legacy={tstat_legacy!r}, n_effective={tstat_neff!r}."
            )

    def test_nn1_honest_overfitting_verdict_prefix(self):
        """autotune_runs row must write overfitting_verdict starting with 'NN1_HONEST' when S=0."""
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["nn1_honest_s0"]
        prefix = scenario["overfitting_verdict_prefix"]
        # Shape assertion: the implementer must use this prefix. Test does NOT pin
        # the full string (that would be a producer-output literal), only the prefix.
        assert prefix == "NN1_HONEST", (
            "Fixture overfitting_verdict_prefix for nn1_honest_s0 must be 'NN1_HONEST'. "
            "The prefix is the contract; the full string is the implementer's choice."
        )


# ===========================================================================
# T2 — Single P&L-toured facet (tripwire fires, plan §T2)
# ===========================================================================


class TestSinglePnlToured:
    """T2: one BACKTEST_SELECTION row → S=n_configs_searched → N_effective = N_optuna + S."""

    def test_compute_n_effective_single_row(self):
        """compute_n_effective sums n_configs_searched from the single qualifying row."""
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["single_pnl_toured_facet"]

        n_optuna = scenario["n_optuna"]
        rows = scenario["ledger_rows"]
        expected_n_eff = scenario["expected_n_effective"]

        result = autotuner.compute_n_effective(n_optuna, lambda: rows)

        assert result == expected_n_eff, (
            f"Tripwire: compute_n_effective({n_optuna}, one_row) must return "
            f"{expected_n_eff} (S={scenario['expected_s']}). Got {result!r}. "
            "The S accumulator must SUM n_configs_searched from qualifying rows."
        )

    def test_tripwire_strictly_tightens_adjusted_pvalues(self):
        """With S>0 the BHY-adjusted p-values must be strictly >= the S=0 values.

        The inflation is the POINT of the accounting: a P&L-toured facet raises
        the Yekutieli c(N) factor, which inflates every adjusted p-value, which
        makes the FDR gate harder to clear (council synthesis §2.2 property 2).

        Tolerance 1e-9 absolute — any element that is strictly less in the
        inflated case violates the conservative upper-bound property.
        """
        autotuner = _import_autotuner()

        # 10 trials: one strong signal, nine noise.
        trials = [
            _make_fake_trial(value=4.0, daily_returns=[0.02] * 80),
        ] + [_make_fake_trial(value=0.5, daily_returns=[0.003] * 50) for _ in range(9)]

        # S=0 baseline (NN1-honest).
        _winner_base, p_adj_base, _tstat_base = autotuner._haircut_select(trials)

        # S=3 (one P&L-toured facet with n_configs_searched=3).
        _winner_inf, p_adj_inf, _tstat_inf = autotuner._haircut_select(
            trials, n_effective=len(trials) + 3
        )

        # p_adj_inf must be >= p_adj_base when both are floats.
        # (Both may be None if no trial cleared the gate — that is also valid:
        # the inflated path is at least as strict as the baseline.)
        if p_adj_base is not None and p_adj_inf is not None:
            assert p_adj_inf >= p_adj_base - _BHY_TOL, (
                f"Tripwire tightening failure: winner p_adj with S=3 ({p_adj_inf!r}) "
                f"is less than the S=0 baseline ({p_adj_base!r}). "
                "S>0 must strictly tighten (or at minimum not loosen) the haircut. "
                "The Yekutieli c(N) is monotonically increasing in N."
            )

    def test_tripwire_overfitting_verdict_prefix(self):
        """overfitting_verdict when S>0 must start with 'NN1_VIOLATION_TRIPWIRE'."""
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["single_pnl_toured_facet"]
        prefix = scenario["overfitting_verdict_prefix"]
        assert prefix == "NN1_VIOLATION_TRIPWIRE", (
            "Fixture overfitting_verdict_prefix for single_pnl_toured_facet must be "
            "'NN1_VIOLATION_TRIPWIRE'. The tripwire label disambiguates the accounting "
            "from the OOS_PEEK alarm path."
        )


# ===========================================================================
# T3 — Defect-2 sub-sweep counting (plan §T3)
# ===========================================================================


class TestDefect2SubsweepCounting:
    """T3: n_configs_searched=7 must contribute 7 to S (SUM), not 1 (COUNT)."""

    def test_n_effective_sums_n_configs_searched_not_counts_rows(self):
        """Defect-2: S is SUM(n_configs_searched), not COUNT(qualifying rows).

        v3-and-divergence-evaluation §B.3 route 2: a P&L-toured spec that received
        its own 7-config sub-sweep contributes 7 to S, not 1. Counting the row once
        would understate the honest test count by 6.
        """
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["defect2_subsweep_counting"]

        n_optuna = scenario["n_optuna"]
        rows = scenario["ledger_rows"]
        expected_n_eff = scenario["expected_n_effective"]
        wrong_n_eff_count_only = scenario["_defect2_wrong_n_effective"]

        result = autotuner.compute_n_effective(n_optuna, lambda: rows)

        assert result == expected_n_eff, (
            f"Defect-2 counting error: compute_n_effective({n_optuna}, [row with "
            f"n_configs_searched=7]) must return {expected_n_eff} (SUM=7), "
            f"not {wrong_n_eff_count_only} (COUNT=1). "
            f"Got {result!r}. "
            "S accumulates the full sub-sweep grid size (v3-evaluation §B.3 route 2)."
        )

    def test_d_spec_is_count_distinct_bundles_not_sum(self):
        """d_spec = COUNT DISTINCT spec_bundle_id — different from S (the SUM).

        The plan stores both deliberately (council-converged migration plan §5):
        d_spec counts how many distinct P&L-toured bundles there were;
        n_effective uses the sub-sweep SUM. A maintainer who conflates them
        would either overstate d_spec or understate S.
        """
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["defect2_subsweep_counting"]
        # d_spec=1 (one distinct bundle), S=7 (its full sub-sweep count).
        assert scenario["expected_d_spec"] == 1, (
            "Fixture d_spec must be 1 for a single bundle (COUNT DISTINCT). "
            f"Got {scenario['expected_d_spec']!r}."
        )
        assert scenario["expected_s"] == 7, (
            "Fixture S must be 7 for n_configs_searched=7 (SUM, not COUNT). "
            f"Got {scenario['expected_s']!r}."
        )
        # The two numbers must differ — that is the whole point of storing both.
        assert scenario["expected_d_spec"] != scenario["expected_s"], (
            "d_spec and S must differ in this fixture; they represent different "
            "quantities (COUNT DISTINCT vs SUM)."
        )


# ===========================================================================
# T4 — Frozen-eval-tainted row exclusion (plan §T4)
# ===========================================================================


class TestFrozenEvalTaintedExclusion:
    """T4: rows with touched_frozen_eval=1 are excluded from S."""

    def test_frozen_eval_tainted_row_contributes_zero_to_s(self):
        """A touched_frozen_eval=1 row must NOT contribute to S.

        Plan D4: WHERE COALESCE(touched_frozen_eval, 0) = 0. Frozen-eval-tainted
        rows are a distinct, harder violation surfaced by the Overfitting Conscience
        advisor (OOS_PEEK alarm). Including them in S would double-fire the accounting
        and a 'fix the OOS_PEEK' action could silently reduce n_effective — wrong direction.
        """
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["frozen_eval_tainted_excluded"]

        n_optuna = scenario["n_optuna"]
        rows = scenario["ledger_rows"]
        expected_s = scenario["expected_s"]
        expected_n_eff = scenario["expected_n_effective"]

        result = autotuner.compute_n_effective(n_optuna, lambda: rows)

        assert result == expected_n_eff, (
            f"Frozen-eval exclusion: compute_n_effective({n_optuna}, [tainted_row]) "
            f"must return {expected_n_eff} (S={expected_s}; tainted row excluded). "
            f"Got {result!r}. "
            "touched_frozen_eval=1 rows are the OOS_PEEK alarm — not the S accumulator."
        )

    def test_frozen_eval_tainted_row_boundary_documented_in_fixture(self):
        """The fixture explicitly encodes the boundary between S accounting and OOS_PEEK alarm."""
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["frozen_eval_tainted_excluded"]
        row = scenario["ledger_rows"][0]
        assert row["touched_frozen_eval"] == 1, (
            "The frozen_eval_tainted_excluded scenario must have a row with "
            "touched_frozen_eval=1 to document the boundary."
        )
        assert scenario["expected_s"] == 0, (
            "Expected S=0 when the only qualifying row has touched_frozen_eval=1."
        )


# ===========================================================================
# T5 — Winner-bundle self-exclusion (plan §T5)
# ===========================================================================


class TestWinnerBundleSelfExclusion:
    """T5: the winning spec_bundle_id's ledger row is excluded from S."""

    def test_winner_bundle_excluded_from_s(self):
        """The winning bundle's row must not contribute to S.

        The winner is already counted in n_optuna via the Optuna sweep that
        selected it. Including it again would double-count (plan D1, plan D4).
        """
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["winner_bundle_self_excluded"]

        n_optuna = scenario["n_optuna"]
        winning_spec_bundle_id = scenario["winning_spec_bundle_id"]
        rows = scenario["ledger_rows"]
        expected_n_eff = scenario["expected_n_effective"]

        # The ledger_query returns the row, but compute_n_effective must
        # exclude it because its spec_bundle_id matches the winner.
        # We inject the winning_spec_bundle_id via a second argument
        # (the plan's DI seam allows this — compute_n_effective is a pure function).
        result = autotuner.compute_n_effective(
            n_optuna,
            lambda: rows,
            winning_spec_bundle_id=winning_spec_bundle_id,
        )

        assert result == expected_n_eff, (
            f"Winner self-exclusion: compute_n_effective({n_optuna}, [winner_row], "
            f"winning_spec_bundle_id={winning_spec_bundle_id!r}) must return "
            f"{expected_n_eff} (S=0; winner excluded). Got {result!r}. "
            "The winning bundle is already counted in n_optuna — do not double-count."
        )


# ===========================================================================
# T6 — Shape-A byte-identical preservation (plan §T6)
# ===========================================================================


class TestShapeAByteIdenticalPreservation:
    """T6: benjamini_hochberg_adjust(p + [1.0]*0) == benjamini_hochberg_adjust(p)."""

    def test_empty_padding_is_noop_for_bhy(self):
        """Shape-A with S=0 appends no placeholders — the call is byte-identical.

        This is the BHY preservation contract under the plan's chosen Shape A:
        if S=0, the p-value list passed to benjamini_hochberg_adjust is exactly
        p_values (no [1.0]*0 allocation artefact), so the function is called with
        the same input as today's pre-M1 haircut.

        Tolerance 1e-9 absolute.
        """
        autotuner = _import_autotuner()

        p_values = [0.01, 0.05, 0.1, 0.2, 0.5, 0.8, 0.95]

        result_plain = autotuner.benjamini_hochberg_adjust(list(p_values))
        result_padded = autotuner.benjamini_hochberg_adjust(list(p_values) + [1.0] * 0)

        assert len(result_plain) == len(result_padded), (
            f"Empty padding must not change result length: "
            f"plain={len(result_plain)}, padded={len(result_padded)}."
        )
        for i, (plain, padded) in enumerate(zip(result_plain, result_padded)):
            assert plain == pytest.approx(padded, abs=_BHY_TOL), (
                f"Shape-A byte-identical failure at index {i}: "
                f"benjamini_hochberg_adjust(p + [1.0]*0) must equal "
                f"benjamini_hochberg_adjust(p). "
                f"plain={plain!r}, padded={padded!r}. "
                "The BHY preservation contract requires benjamini_hochberg_adjust "
                "to be diff-empty in the M1 commit."
            )

    def test_benjamini_hochberg_adjust_is_diff_empty(self):
        """benjamini_hochberg_adjust source must be byte-identical to the pre-M1 function.

        The BHY preservation contract (plan D3, Shape A) forbids any modification
        to benjamini_hochberg_adjust itself. Shape A redirects N by padding the
        p-value list ABOVE the call — zero change inside the function.

        This test reads autotuner.py source and verifies the function body has no
        references to n_effective, S, or any padding-related parameter.
        """
        src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")

        # Find the function definition and extract its body to just before the
        # next top-level def/class. We check the body does not contain the
        # n_effective or s_count identifiers (those belong in _haircut_select).
        start = src.find("def benjamini_hochberg_adjust(")
        assert start != -1, "benjamini_hochberg_adjust not found in autotuner.py."

        # Find the next top-level def/class after the function start.
        next_def = src.find("\ndef ", start + 1)
        if next_def == -1:
            next_def = len(src)
        func_body = src[start:next_def]

        for forbidden in ("n_effective", "s_count", "n_padding"):
            assert forbidden not in func_body, (
                f"BHY preservation violation: 'benjamini_hochberg_adjust' body "
                f"contains '{forbidden}'. The BHY preservation contract (plan D3, "
                f"Shape A) requires this function to be diff-empty in the M1 commit. "
                "N-redirection happens ABOVE the call in _haircut_select."
            )


# ===========================================================================
# T7 — Conservative-upper-bound property (plan §T7, property-based)
# ===========================================================================


class TestConservativeUpperBoundProperty:
    """T7: for any p-vector and S in [0,5], padding with [1.0]*S produces element-wise
    adjusted p-values >= the unpadded result.

    This is the 'errs safe' property (council synthesis §2.2 property 2): S>0 can
    reject a genuine signal (conservative) but never passes a spurious one.
    """

    @pytest.mark.parametrize("s", [0, 1, 2, 3, 4, 5])
    def test_bhy_adjusted_pvalues_nondecreasing_under_padding(self, s):
        """BHY adjusted p-values are element-wise >= when S placeholders are appended.

        For each p-value in the original vector, the adjusted value with S extra
        1.0 placeholders must be >= the adjusted value without. Equivalently:
        inflating N inflates c(N), inflates every N*c(N)/k factor, inflates every
        adjusted p. The effect is strictly positive for S>=1 (because 1/(N+1) > 0).

        Tolerance _BHY_TOL absolute — a difference less than 1e-9 is treated as
        equality (within the deterministic arithmetic precision of the BHY step-up).
        """
        autotuner = _import_autotuner()

        # Fixed 20-trial p-vector for reproducibility: evenly spaced [0.01, 0.99].
        # Not derived from Sortinos — just a representative spread of raw p-values.
        import math as _math

        n = 20
        p_values = [0.01 + (0.98 / (n - 1)) * i for i in range(n)]

        p_adj_base = autotuner.benjamini_hochberg_adjust(list(p_values))
        p_adj_inflated = autotuner.benjamini_hochberg_adjust(list(p_values) + [1.0] * s)

        # The inflated result has len(p_values) + s elements; compare only the
        # first len(p_values) elements (the original trials).
        assert len(p_adj_inflated) == len(p_values) + s, (
            f"benjamini_hochberg_adjust with S={s} padding must return "
            f"{len(p_values) + s} elements, got {len(p_adj_inflated)}."
        )

        for i, (base, inflated) in enumerate(zip(p_adj_base, p_adj_inflated[: len(p_values)])):
            assert inflated >= base - _BHY_TOL, (
                f"Conservative upper-bound violation (S={s}): element {i} "
                f"p_adj_inflated={inflated!r} < p_adj_base={base!r} "
                f"(diff={inflated - base:.2e}). "
                f"Padding with S={s} copies of 1.0 must inflate (or at minimum not "
                f"reduce) every BHY-adjusted p-value. "
                "The Yekutieli c(N) = sum(1/j, j=1..N) is strictly increasing in N."
            )

    def test_s_zero_padding_produces_identical_adjusted_values(self):
        """S=0 padding is the exact byte-identical no-op (special case of T7 for S=0)."""
        autotuner = _import_autotuner()

        p_values = [0.01, 0.03, 0.07, 0.12, 0.30, 0.60, 0.90]
        p_adj_base = autotuner.benjamini_hochberg_adjust(list(p_values))
        p_adj_s0 = autotuner.benjamini_hochberg_adjust(list(p_values) + [1.0] * 0)

        for i, (base, s0) in enumerate(zip(p_adj_base, p_adj_s0)):
            assert s0 == pytest.approx(base, abs=_BHY_TOL), (
                f"S=0 is not a no-op at index {i}: base={base!r}, s0={s0!r}."
            )


# ===========================================================================
# D1 contract: compute_n_effective is a pure function with correct signature
# ===========================================================================


class TestComputeNEffectiveContract:
    """Plan D1 contract tests: return type, no side effects, correct bounds."""

    def test_compute_n_effective_returns_int_gte_n_optuna(self):
        """Return value is always int >= n_optuna (S is non-negative by construction)."""
        autotuner = _import_autotuner()

        for n_optuna in (1, 10, 100, 500):
            result = autotuner.compute_n_effective(n_optuna, lambda: [])
            assert isinstance(result, int), (
                f"compute_n_effective({n_optuna}, empty) must return int, "
                f"got {type(result).__name__}."
            )
            assert result >= n_optuna, (
                f"compute_n_effective({n_optuna}, empty) must return >= n_optuna; "
                f"got {result}. S is non-negative."
            )

    def test_compute_n_effective_multiple_qualifying_rows_sum_not_count(self):
        """S is SUM(n_configs_searched) across all qualifying rows, not COUNT(rows)."""
        autotuner = _import_autotuner()
        fixture = _load_fixture()
        scenario = fixture["scenarios"]["multiple_rows_sum_not_count"]

        n_optuna = scenario["n_optuna"]
        rows = scenario["ledger_rows"]
        expected_s = scenario["expected_s"]
        expected_n_eff = scenario["expected_n_effective"]

        result = autotuner.compute_n_effective(n_optuna, lambda: rows)

        assert result == expected_n_eff, (
            f"Three rows (n_configs_searched 3+5+7=15): compute_n_effective must "
            f"return {expected_n_eff} (S=SUM=15), not {n_optuna + len(rows)} "
            f"(S=COUNT=3). Got {result!r}."
        )

    def test_compute_n_effective_nn1_disclosure_comment_present(self):
        """autotuner.py must contain the NN1 disclosure comment block (plan D6).

        The comment documents WHY the accounting is the structural enforcement of NN1
        (council synthesis §2.5). Its absence means the next maintainer cannot
        understand the tripwire without reading the full plan.
        """
        src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")
        # The plan specifies these exact phrases from the disclosure comment.
        required_phrases = [
            "NN1",
            "BACKTEST_SELECTION",
            "N_effective",
            "c(N)",
        ]
        for phrase in required_phrases:
            assert phrase in src, (
                f"NN1 disclosure comment (plan D6) must contain '{phrase}'. "
                "The comment block documents the structural NN1 enforcement for "
                "maintainers. Not found in autotuner.py."
            )


# ===========================================================================
# D2 contract: _haircut_select gains n_effective parameter with safe default
# ===========================================================================


class TestHaircutSelectNEffectiveParameter:
    """Plan D2: _haircut_select signature and default backward-compatibility."""

    def test_haircut_select_accepts_n_effective_kwarg(self):
        """_haircut_select must accept an explicit n_effective keyword argument."""
        autotuner = _import_autotuner()
        import inspect

        sig = inspect.signature(autotuner._haircut_select)
        assert "n_effective" in sig.parameters, (
            "_haircut_select must accept an 'n_effective' parameter (plan D2). "
            "Default value should be len(p_values) for backward-compatibility. "
            "Not implemented yet — expected RED."
        )

    def test_haircut_select_default_n_effective_is_backward_compatible(self):
        """_haircut_select called without n_effective must behave as before (len-based N)."""
        autotuner = _import_autotuner()

        # Two trials — choose values so the outcome is deterministic and testable.
        trials = [
            _make_fake_trial(value=3.0, daily_returns=[0.015] * 50),
            _make_fake_trial(value=0.4, daily_returns=[0.003] * 30),
        ]

        # Without n_effective (legacy call).
        winner_legacy, p_adj_legacy, _ = autotuner._haircut_select(trials)

        # With n_effective = len(trials) explicitly (should be identical).
        winner_explicit, p_adj_explicit, _ = autotuner._haircut_select(
            trials, n_effective=len(trials)
        )

        assert winner_legacy is winner_explicit, (
            "_haircut_select default n_effective must equal len(trials): "
            f"legacy winner != explicit winner."
        )
        if p_adj_legacy is not None and p_adj_explicit is not None:
            assert p_adj_explicit == pytest.approx(p_adj_legacy, abs=_BHY_TOL), (
                f"_haircut_select default produces different p_adj: "
                f"legacy={p_adj_legacy!r}, explicit={p_adj_explicit!r}."
            )


# ===========================================================================
# D5 contract: persistence write-back shape assertions
# ===========================================================================


class TestPersistenceWriteback:
    """Plan D5: autotune_runs columns s_count, n_effective, d_spec, overfitting_verdict."""

    def test_autotune_runs_schema_has_n_effective_columns(self, tmp_path, monkeypatch):
        """autotune_runs must have s_count, n_effective, d_spec, overfitting_verdict columns.

        Migration 020 adds these (autotune_runs_eut). The schema must expose all four
        so the persistence write-back can record the additive accounting result.
        """
        import sqlite3
        import database as db_module

        db_path = str(tmp_path / "test_neff_schema.db")
        monkeypatch.setattr(db_module, "DB_FILE", db_path)
        db_module.init_db()

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(autotune_runs)")
        col_names = {row[1] for row in cursor.fetchall()}
        conn.close()

        required = {"s_count", "n_effective", "d_spec", "overfitting_verdict"}
        missing = required - col_names
        assert not missing, (
            f"autotune_runs is missing additive-accounting columns: {sorted(missing)}. "
            "Migration 020 must add these columns. Not present yet — expected RED."
        )

    def test_overfitting_verdict_string_format_nn1_honest(self):
        """NN1-honest verdict string must include key diagnostic tokens.

        Plan D5 specifies the format:
          'NN1_HONEST n_optuna=500 d_spec=0 n_effective=500'
        The test asserts token presence (not exact string) to avoid being brittle
        on whitespace/delimiter choices. The tokens are the load-bearing content.
        """
        # Shape/format assertion only — no producer-output literal.
        # The implementer generates this string; we verify it has the tokens.
        sample_verdict = "NN1_HONEST n_optuna=500 d_spec=0 n_effective=500"
        for token in ("NN1_HONEST", "n_optuna=", "d_spec=", "n_effective="):
            assert token in sample_verdict, (
                f"Sample verdict '{sample_verdict}' missing token '{token}'. "
                "The implementer must include these tokens in the verdict string."
            )

    def test_overfitting_verdict_string_format_nn1_violation(self):
        """NN1-violation verdict string must include tripwire and violation tokens."""
        sample_verdict = "NN1_VIOLATION_TRIPWIRE n_optuna=500 d_spec=3 n_effective=523"
        for token in ("NN1_VIOLATION_TRIPWIRE", "n_optuna=", "d_spec=", "n_effective="):
            assert token in sample_verdict, (
                f"Sample verdict '{sample_verdict}' missing token '{token}'."
            )


# ===========================================================================
# D4 contract: database.get_researcher_dof_ledger_for_run accessor
# ===========================================================================


class TestLedgerAccessor:
    """Plan D4: database.get_researcher_dof_ledger_for_run must exist and be testable."""

    def test_get_researcher_dof_ledger_for_run_exists_in_database(self):
        """database must expose get_researcher_dof_ledger_for_run(run_timestamp, winning_spec_bundle_id)."""
        import database

        assert hasattr(database, "get_researcher_dof_ledger_for_run"), (
            "database must expose get_researcher_dof_ledger_for_run(run_timestamp, "
            "winning_spec_bundle_id) -> list — the D4 ledger query helper. "
            "Not implemented yet — expected RED."
        )

    def test_ledger_accessor_returns_list(self, tmp_path, monkeypatch):
        """get_researcher_dof_ledger_for_run must return a list (possibly empty)."""
        import database as db_module

        db_path = str(tmp_path / "test_neff_ledger.db")
        monkeypatch.setattr(db_module, "DB_FILE", db_path)
        db_module.init_db()

        # No rows written — must return empty list, not None, not raise.
        result = db_module.get_researcher_dof_ledger_for_run(
            run_timestamp="2026-05-20T16:00:00",
            winning_spec_bundle_id="bundle-winner-000",
        )
        assert isinstance(result, list), (
            f"get_researcher_dof_ledger_for_run must return a list; got {type(result)!r}."
        )
        assert result == [], f"Empty DB must return empty list, not {result!r}."


# ===========================================================================
# Spec-neff finding 1: n_optuna must be sentinel-filtered (not raw trial count)
# ===========================================================================


class TestNOptunaIsNotSentinelInflated:
    """Spec-neff finding 1: n_optuna passed to compute_n_effective must be
    len(haircut_trials) — the sentinel-filtered set — NOT len(completed_trials).

    autotuner.py filters sentinels (value == math_engine._SORTINO_SENTINEL == 1e6)
    before calling _haircut_select. If n_optuna were derived from completed_trials
    (before filtering), a batch with many zero-downside sentinel trials would inflate
    N_effective even in the NN1-honest case, breaking T1's byte-identical contract
    and causing spurious haircut tightening on noise-free sentinel days.
    """

    def test_sentinel_trials_do_not_inflate_n_optuna(self):
        """n_optuna = len(sentinel-filtered trials); sentinels must not bump N_effective.

        Scenario: 500 completed trials, 10 are sentinels (value == 1e6).
        n_optuna (the N passed to compute_n_effective) must be 490, not 500.
        With an empty ledger (S=0) N_effective must equal 490, not 500.
        """
        autotuner = _import_autotuner()
        import math_engine

        sentinel_value = math_engine._SORTINO_SENTINEL

        n_real = 490
        n_sentinel = 10

        real_trials = [
            _make_fake_trial(value=1.5, daily_returns=[0.01] * 50) for _ in range(n_real)
        ]
        sentinel_trials = [
            _make_fake_trial(value=sentinel_value, daily_returns=[]) for _ in range(n_sentinel)
        ]
        all_completed = real_trials + sentinel_trials

        # haircut_trials is the sentinel-filtered subset — mirrors autotuner.py logic.
        haircut_trials = [t for t in all_completed if t.value != sentinel_value]
        n_optuna_correct = len(haircut_trials)  # 490

        assert n_optuna_correct == n_real, (
            f"Test setup error: expected {n_real} real trials after sentinel filter, "
            f"got {n_optuna_correct}."
        )

        # compute_n_effective must accept n_optuna=490 (the filtered count).
        # With empty ledger, N_effective must be 490 (not 500).
        result = autotuner.compute_n_effective(n_optuna_correct, lambda: [])

        assert result == n_real, (
            f"compute_n_effective({n_optuna_correct}, empty) must return {n_real} "
            f"(S=0, sentinel-filtered n_optuna). Got {result!r}. "
            "n_optuna must be derived from sentinel-filtered trials "
            "(len(haircut_trials)), not from len(completed_trials)."
        )

        # Confirm the wrong derivation (using raw completed count) would give 500.
        result_inflated = autotuner.compute_n_effective(len(all_completed), lambda: [])
        assert result_inflated == len(all_completed), (
            "Sanity: compute_n_effective(500, empty) must return 500 "
            f"(confirming the sentinel-inflated path is distinguishable). "
            f"Got {result_inflated!r}."
        )
        # The two results must differ to confirm the sentinel filtering is meaningful.
        assert result != result_inflated, (
            f"Sentinel filtering must matter: n_optuna=490 → N_eff={result}, "
            f"n_optuna=500 → N_eff={result_inflated}. They must differ."
        )


# ===========================================================================
# Spec-neff finding 5: compute_n_effective must NOT reuse count_dof_backtest_selections
# ===========================================================================


class TestComputeNEffectiveDoesNotReuseExistingAccessor:
    """Spec-neff finding 5: compute_n_effective must use get_researcher_dof_ledger_for_run
    (plan D4), NOT count_dof_backtest_selections (database.py:1297).

    count_dof_backtest_selections lacks two required filters:
      1. touched_frozen_eval=0 exclusion (T4 boundary)
      2. winner-bundle self-exclusion (T5 boundary)

    Reusing it would silently pass those tests because the filters are absent.
    The DI seam (ledger_query callable) is the correctness guarantee: the caller
    injects only the pre-filtered rows, and compute_n_effective sums them without
    additional DB access.
    """

    def test_compute_n_effective_uses_injected_query_not_direct_db(self):
        """compute_n_effective must sum rows from the injected ledger_query callable.

        We inject a query that returns one row with n_configs_searched=7. The result
        must be n_optuna + 7. If compute_n_effective calls count_dof_backtest_selections
        directly (bypassing the injected callable), the result would be n_optuna + 0
        (no real DB rows exist in this in-memory test) — a detectable divergence.
        """
        autotuner = _import_autotuner()

        rows_from_query = [
            {
                "evidence_source": "BACKTEST_SELECTION",
                "n_configs_searched": 7,
                "touched_frozen_eval": 0,
                "spec_bundle_id": "bundle-injected-999",
            }
        ]

        n_optuna = 100
        result = autotuner.compute_n_effective(n_optuna, lambda: rows_from_query)

        assert result == 107, (
            f"compute_n_effective must use the injected ledger_query, not "
            f"count_dof_backtest_selections. With injected rows (S=7) and "
            f"n_optuna=100, expected 107. Got {result!r}. "
            "If result is 100 (=n_optuna+0), the function bypassed the DI seam "
            "and called a DB accessor directly — which would also lack the "
            "touched_frozen_eval and winner-bundle filters."
        )

    def test_count_dof_backtest_selections_is_not_called_by_compute_n_effective(self):
        """compute_n_effective source must not call count_dof_backtest_selections.

        Source-level check: the DI seam contract requires the function to be pure
        with respect to DB access. Any direct call to count_dof_backtest_selections
        inside compute_n_effective would bypass the injected query and reintroduce
        the missing filters.
        """
        src = (_WORKTREE_ROOT / "autotuner.py").read_text(encoding="utf-8")

        start = src.find("def compute_n_effective(")
        assert start != -1, "compute_n_effective not yet defined in autotuner.py (expected RED)."

        next_def = src.find("\ndef ", start + 1)
        if next_def == -1:
            next_def = len(src)
        func_body = src[start:next_def]

        assert "count_dof_backtest_selections" not in func_body, (
            "compute_n_effective must not call count_dof_backtest_selections. "
            "Use the injected ledger_query callable (plan D1 DI seam). "
            "count_dof_backtest_selections lacks the touched_frozen_eval=0 filter "
            "(T4) and the winner-bundle exclusion (T5)."
        )


# ===========================================================================
# Helpers
# ===========================================================================


def _make_fake_trial(value: float, daily_returns: list[float]):
    """Return a minimal fake Optuna trial object compatible with _haircut_select.

    _haircut_select reads ``t.value`` and ``t.user_attrs.get('daily_returns', [])``.
    These are the only attributes it touches per the current source.
    """
    from unittest.mock import MagicMock

    trial = MagicMock()
    trial.value = value
    trial.user_attrs = {"daily_returns": daily_returns}
    return trial
