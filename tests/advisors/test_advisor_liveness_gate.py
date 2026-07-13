"""Cycle C — RED: AI Advisor liveness, decision-gate layer (H6 / H5 / L1).

These RED tests pin the math/decision-layer root causes of "the AI advisor does
nothing" identified in `.design-handoff/advisor-liveness/DIAGNOSIS.md` and the
falsification synthesis (`validation/synthesis-verdict:validation/VERDICT.md`).

Modules under test (real producers — NEVER mocked):
  - acceptance_gate.evaluate_acceptance_gate            (the adopt/keep gate)
  - advisors.backtest_gate_engine._fold_transform_single (the fold split)
  - advisors.backtest_gate_engine.evaluate_candidate_batch (the M2 spine)
  - advisors.asset_swap_engine.propose_operator_swap     (the wired-route entry)
  - advisors.logic_change_engine.extract_numeric_params  (the param parser, L1)

Scoped RED items (per the team brief — NOTHING else here):
  * H6 / RC-1 (the root cause): the gate compares a ~25-day candidate validation
    FOLD alpha against a ~125-day FULL-history baseline sum -> systematic
    KEEP_INCUMBENT.  A candidate genuinely better ON THE VALIDATION FOLD must
    ADOPT, not KEEP, once the baseline is fold-matched.
  * H5: an explicit incumbent_oos_alpha=0.0 (a real break-even) must NOT be
    replaced by the baseline full-history fallback (the falsy `or` bug).
  * L1: a CURRENT numeric (non-bool) param valued 0 or 1 must be surfaced as
    tweakable; a real bool stays excluded.

Provenance discipline (feedback_no_hardcoded_test_values):
  Daily return series are constructed by the test; every expected gate decision
  is DERIVED from the fold math (sum of the validation slice), never pasted as a
  producer output.  Floats use pytest.approx with explicit tolerances.

Mocking strategy:
  - The math engine and acceptance_gate are NEVER mocked — tested directly.
  - Only the Composer network (advisors.composer_backtest_client.run_backtest)
    and database persistence are mocked at the engine-integration layer.
  - No live API calls; no live DB.
"""

from __future__ import annotations

import importlib
import json
import pathlib
import sys
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_H6_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "advisors" / "h6_fold_baseline_window_match.json"


def _ensure_repo_on_path() -> None:
    repo = str(_REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)


@pytest.fixture(scope="module")
def gate_engine():
    """Import the real backtest_gate_engine (fold transform + batch spine)."""
    _ensure_repo_on_path()
    return importlib.import_module("advisors.backtest_gate_engine")


@pytest.fixture(scope="module")
def acceptance():
    """Import the real acceptance_gate."""
    _ensure_repo_on_path()
    return importlib.import_module("acceptance_gate")


@pytest.fixture(scope="module")
def swap_engine():
    """Import the real asset_swap_engine (wired-route entry point)."""
    _ensure_repo_on_path()
    return importlib.import_module("advisors.asset_swap_engine")


@pytest.fixture(scope="module")
def logic_engine():
    """Import the real logic_change_engine (param parser, L1)."""
    _ensure_repo_on_path()
    return importlib.import_module("advisors.logic_change_engine")


@pytest.fixture(scope="module")
def h6_fixture() -> dict:
    assert _H6_FIXTURE.exists(), (
        f"Fixture not found: {_H6_FIXTURE}. "
        "Create tests/fixtures/advisors/h6_fold_baseline_window_match.json."
    )
    with _H6_FIXTURE.open(encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Helpers — derive fold sums from a series exactly as _fold_transform_single does.
# ---------------------------------------------------------------------------


def _validation_fold_slice(series: list, train_ratio: float, val_end_ratio: float) -> list:
    """Reproduce backtest_gate_engine._fold_transform_single's validation slice.

    val_start_idx = int(n * TRAIN_RATIO); frozen_start_idx = int(n * (TRAIN+VAL)).
    The validation fold = series[val_start_idx:frozen_start_idx].
    """
    n = len(series)
    val_start_idx = int(n * train_ratio)
    frozen_start_idx = int(n * val_end_ratio)
    return series[val_start_idx:frozen_start_idx]


# ===========================================================================
# H6 / RC-1 — the window-mismatch root cause, at the GATE layer.
# ===========================================================================


class TestH6GateWindowMismatch:
    """The decision flips on the window of the baseline — proven deterministically.

    These tests call acceptance_gate.evaluate_acceptance_gate directly with a
    candidate that wins OOS-superiority and a matching panel (candidate==incumbent
    params so the panel never withholds), isolating the OOS-comparison branch that
    H6 corrupts.  The ONLY difference between the two assertions is the baseline's
    window — proving the bug is the window, not the candidate.
    """

    def _adopt_kwargs(self, *, oos_alpha, fallback_oos_alpha, default_oos_alpha):
        """Gate kwargs where every veto passes and the panel cannot withhold.

        Identical candidate/incumbent panel sub-scores (1.0 each) so
        candidate_panel_score == incumbent_panel_score >= incumbent + 0.0 MARGIN.
        This isolates the OOS-superiority branch (the H6 surface).
        """
        return dict(
            winner_trial_is_none=False,  # BHY winner present (veto passes)
            winner_p_adj=0.01,
            nn1_compliant=True,
            purge_integrity_ok=True,
            oos_alpha=oos_alpha,
            fallback_oos_alpha=fallback_oos_alpha,
            default_oos_alpha=default_oos_alpha,
            candidate_stability_score=1.0,
            candidate_prior_anchor_score=1.0,
            incumbent_stability_score=1.0,
            incumbent_prior_anchor_score=1.0,
        )

    def test_candidate_fold_alpha_beats_fold_baseline_adopts(self, acceptance, h6_fixture):
        """A candidate fold-alpha that beats the SAME-window (fold) baseline ADOPTS.

        DERIVED from the fixture series via the fold-slice math — no hardcoded sums.
        """
        from autotuner import TRAIN_RATIO, VALIDATION_RATIO

        n = h6_fixture["series_length"]
        cand_series = [h6_fixture["candidate"]["per_day_pct"]] * n
        inc_series = [h6_fixture["incumbent"]["per_day_pct"]] * n

        cand_fold_alpha = sum(
            _validation_fold_slice(cand_series, TRAIN_RATIO, TRAIN_RATIO + VALIDATION_RATIO)
        )
        inc_fold_alpha = sum(
            _validation_fold_slice(inc_series, TRAIN_RATIO, TRAIN_RATIO + VALIDATION_RATIO)
        )

        # Sanity on the fixture construction: candidate strictly beats incumbent on the fold.
        assert cand_fold_alpha > inc_fold_alpha, (
            "Fixture mis-constructed: the candidate must beat the incumbent on the "
            "validation fold. Adjust per_day_pct values."
        )

        verdict = acceptance.evaluate_acceptance_gate(
            **self._adopt_kwargs(
                oos_alpha=cand_fold_alpha,
                fallback_oos_alpha=inc_fold_alpha,  # FOLD-matched baseline (the fix)
                default_oos_alpha=h6_fixture["default_oos_alpha_pct"],
            )
        )
        assert verdict.decision == "ADOPT_CANDIDATE", (
            f"Gate returned {verdict.decision!r} when the candidate's fold alpha "
            f"({cand_fold_alpha:.4f}) beats the fold-matched baseline "
            f"({inc_fold_alpha:.4f}). H6: a genuinely-superior candidate must ADOPT "
            "when compared apples-to-apples on the same validation fold."
        )

    def test_same_candidate_keeps_against_full_history_baseline_documents_bug(
        self, acceptance, h6_fixture
    ):
        """The SAME candidate KEEPS when compared against the FULL-history baseline.

        This documents the bug: the only thing that changed is the baseline window
        (full-history sum instead of the fold sum). The fold-matched test above
        ADOPTS the identical candidate; this one KEEPS it. The window is the bug.
        """
        from autotuner import TRAIN_RATIO, VALIDATION_RATIO

        n = h6_fixture["series_length"]
        cand_series = [h6_fixture["candidate"]["per_day_pct"]] * n
        inc_series = [h6_fixture["incumbent"]["per_day_pct"]] * n

        cand_fold_alpha = sum(
            _validation_fold_slice(cand_series, TRAIN_RATIO, TRAIN_RATIO + VALIDATION_RATIO)
        )
        inc_full_history_alpha = sum(inc_series)  # the buggy full-history fallback

        # The fixture is constructed so the full-history baseline sum exceeds the
        # candidate's short fold sum (the window-mismatch hurdle).
        assert inc_full_history_alpha > cand_fold_alpha, (
            "Fixture mis-constructed: the incumbent FULL-history sum must exceed the "
            "candidate FOLD sum to reproduce the H6 window-mismatch hurdle."
        )

        verdict = acceptance.evaluate_acceptance_gate(
            **self._adopt_kwargs(
                oos_alpha=cand_fold_alpha,
                fallback_oos_alpha=inc_full_history_alpha,  # FULL-history baseline (the bug)
                default_oos_alpha=h6_fixture["default_oos_alpha_pct"],
            )
        )
        assert verdict.decision == "KEEP_INCUMBENT", (
            "Expected KEEP_INCUMBENT under the window-MISMATCHED compare (this is the "
            "bug being documented). If this now ADOPTs, the fixture no longer "
            "reproduces the hurdle — revisit the series construction."
        )


class TestH6EngineFeedsFoldMatchedBaseline:
    """The wired engine entry point must NOT feed a FULL-history baseline to the gate.

    The live routes call propose_operator_swap WITHOUT incumbent_oos_alpha, so the
    engine derives the baseline. Today it derives `sum(full-history)`
    (asset_swap_engine.py:676). The fix must derive a FOLD-matched baseline so the
    gate compares fold-to-fold. We assert on the value the engine hands the gate.
    """

    def test_propose_swap_default_baseline_is_fold_matched_not_full_history(
        self, swap_engine, h6_fixture
    ):
        """When no incumbent_oos_alpha is supplied, the gate's fallback_oos_alpha
        must equal the baseline's VALIDATION-FOLD sum, not its full-history sum.

        We spy on the real gate (evaluate_candidate_batch) to capture the
        incumbent_oos_alpha the engine passes, and assert it is fold-scaled.
        run_backtest is mocked to return a known deterministic baseline series.
        """
        from autotuner import TRAIN_RATIO, VALIDATION_RATIO

        n = h6_fixture["series_length"]
        baseline_series_pct = [h6_fixture["incumbent"]["per_day_pct"]] * n  # percent/day
        baseline_full_history_sum = sum(baseline_series_pct)
        baseline_fold_sum = sum(
            _validation_fold_slice(baseline_series_pct, TRAIN_RATIO, TRAIN_RATIO + VALIDATION_RATIO)
        )

        # The engine reads run_backtest(...).daily_returns.values() as FRACTIONS
        # (_backtest_returns_from_tree multiplies by 100 to get percent). So feed
        # fractions whose *100 reproduces the percent series.
        daily_returns_fraction = {
            str(19000 + i): (baseline_series_pct[i] / 100.0) for i in range(n)
        }

        fake_bt = MagicMock()
        fake_bt.error = None
        fake_bt.daily_returns = daily_returns_fraction
        fake_bt.stats = {"sharpe_ratio": None, "sortino_ratio": None, "max_drawdown": None}

        captured = {"incumbent_oos_alpha": None}

        real_batch = swap_engine.evaluate_candidate_batch

        def _batch_spy(candidates, *, incumbent_oos_alpha=0.0, default_oos_alpha=0.0, spy_returns_fn=None):
            captured["incumbent_oos_alpha"] = incumbent_oos_alpha
            return real_batch(
                candidates,
                incumbent_oos_alpha=incumbent_oos_alpha,
                default_oos_alpha=default_oos_alpha,
                spy_returns_fn=spy_returns_fn,
            )

        score_tree = {
            "id": "sym-h6",
            "type": "root",
            "children": [{"type": "asset", "ticker": "SPY", "weight": 1.0}],
        }
        objective = swap_engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("sym-h6", "sym-other"),
            measured_value=0.85,
        )

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=fake_bt),
            patch("advisors.asset_swap_engine.evaluate_candidate_batch", side_effect=_batch_spy),
            patch("database.insert_advisor_observation", return_value=1),
        ):
            swap_engine.propose_operator_swap(
                symphony_id="sym-h6",
                score_tree=score_tree,
                incumbent_asset="SPY",
                candidate_asset="IALT",
                objective=objective,
            )

        captured_baseline = captured["incumbent_oos_alpha"]
        assert captured_baseline is not None, (
            "evaluate_candidate_batch was never called — the engine errored before "
            "gating. The fold-baseline path is unreachable."
        )
        # The bug: captured_baseline == full-history sum (~20.0).
        # The fix: captured_baseline == fold sum (~4.0).
        assert captured_baseline == pytest.approx(baseline_fold_sum, abs=1e-6), (
            f"propose_operator_swap fed the gate a baseline of {captured_baseline:.4f}; "
            f"it must be the VALIDATION-FOLD sum {baseline_fold_sum:.4f} (window-matched "
            f"to the candidate's fold alpha), NOT the full-history sum "
            f"{baseline_full_history_sum:.4f}. H6/RC-1: feeding the full-history sum "
            "biases the gate to systematic KEEP_INCUMBENT."
        )
        # Belt-and-suspenders: it must specifically NOT be the full-history sum.
        assert captured_baseline != pytest.approx(baseline_full_history_sum, abs=1e-6), (
            f"The gate baseline equals the FULL-history sum {baseline_full_history_sum:.4f}. "
            "H6/RC-1: the `or sum(baseline_returns_pct)` full-history fallback must be "
            "replaced by a fold-transformed baseline."
        )


# ===========================================================================
# H5 — explicit incumbent_oos_alpha=0.0 must NOT be replaced by the fallback.
# ===========================================================================


class TestH5ExplicitZeroNotReplaced:
    """A real break-even incumbent (0.0) is falsy; the `or` bug replaces it with
    the full-history baseline sum. An explicit 0.0 must survive as 0.0.
    """

    def test_explicit_zero_incumbent_alpha_is_used_as_zero_not_baseline(
        self, swap_engine, h6_fixture
    ):
        """When the caller passes incumbent_oos_alpha=0.0 explicitly, the gate's
        fallback_oos_alpha must be 0.0 — NOT the derived baseline sum.

        We capture the value the engine forwards to evaluate_candidate_batch.
        """
        n = h6_fixture["series_length"]
        baseline_series_pct = [h6_fixture["incumbent"]["per_day_pct"]] * n
        baseline_full_history_sum = sum(baseline_series_pct)
        daily_returns_fraction = {
            str(19000 + i): (baseline_series_pct[i] / 100.0) for i in range(n)
        }

        fake_bt = MagicMock()
        fake_bt.error = None
        fake_bt.daily_returns = daily_returns_fraction
        fake_bt.stats = {"sharpe_ratio": None, "sortino_ratio": None, "max_drawdown": None}

        captured = {"incumbent_oos_alpha": "__unset__"}
        real_batch = swap_engine.evaluate_candidate_batch

        def _batch_spy(candidates, *, incumbent_oos_alpha=0.0, default_oos_alpha=0.0, spy_returns_fn=None):
            captured["incumbent_oos_alpha"] = incumbent_oos_alpha
            return real_batch(
                candidates,
                incumbent_oos_alpha=incumbent_oos_alpha,
                default_oos_alpha=default_oos_alpha,
                spy_returns_fn=spy_returns_fn,
            )

        score_tree = {
            "id": "sym-h5",
            "type": "root",
            "children": [{"type": "asset", "ticker": "SPY", "weight": 1.0}],
        }
        objective = swap_engine.SwapObjective(
            objective_type="reduce_correlation",
            target_pair=("sym-h5", "sym-other"),
            measured_value=0.85,
        )

        with (
            patch("advisors.asset_swap_engine.run_backtest", return_value=fake_bt),
            patch("advisors.asset_swap_engine.evaluate_candidate_batch", side_effect=_batch_spy),
            patch("database.insert_advisor_observation", return_value=1),
        ):
            swap_engine.propose_operator_swap(
                symphony_id="sym-h5",
                score_tree=score_tree,
                incumbent_asset="SPY",
                candidate_asset="IALT",
                objective=objective,
                incumbent_oos_alpha=0.0,  # explicit real break-even
            )

        captured_baseline = captured["incumbent_oos_alpha"]
        assert captured_baseline == pytest.approx(0.0, abs=1e-9), (
            f"propose_operator_swap replaced an explicit incumbent_oos_alpha=0.0 with "
            f"{captured_baseline!r}. H5: a falsy-but-meaningful 0.0 must be used AS 0.0, "
            f"not swapped for the baseline fallback ({baseline_full_history_sum:.4f}). "
            "Bug: `incumbent_oos_alpha or sum(...)` treats 0.0 as missing — use "
            "`x if x is not None else <fold-baseline>` with a None default."
        )


# ===========================================================================
# L1 — a CURRENT numeric (non-bool) param valued 0 or 1 must be tweakable.
# ===========================================================================


class TestL1ZeroOrOneNumericParamSurfaced:
    """extract_numeric_params currently drops any numeric whose CURRENT value is
    0 or 1 (logic_change_engine.py:421). A real numeric param at 0 or 1 is still
    tweakable; only genuine booleans (True/False) should be excluded.
    """

    def test_current_numeric_value_one_is_surfaced(self, logic_engine):
        """A numeric param valued 1 must appear in the extracted params."""
        tree = {
            "id": "sym-l1",
            "type": "root",
            "children": [
                {"type": "group", "window": 1, "lookback": 20},
            ],
        }
        params = logic_engine.extract_numeric_params(tree)
        keys = {p.get("param_key") for p in params}
        assert "window" in keys, (
            f"extract_numeric_params dropped the numeric param 'window' valued 1. "
            f"Found keys: {sorted(k for k in keys if k)}. "
            "L1: a CURRENT numeric value of 0 or 1 does not make a param non-tweakable — "
            "only genuine booleans (True/False) are flags."
        )

    def test_current_numeric_value_zero_is_surfaced(self, logic_engine):
        """A numeric param valued 0 must appear in the extracted params."""
        tree = {
            "id": "sym-l1",
            "type": "root",
            "children": [
                {"type": "group", "threshold": 0, "lookback": 20},
            ],
        }
        params = logic_engine.extract_numeric_params(tree)
        keys = {p.get("param_key") for p in params}
        assert "threshold" in keys, (
            f"extract_numeric_params dropped the numeric param 'threshold' valued 0. "
            f"Found keys: {sorted(k for k in keys if k)}. "
            "L1: a CURRENT numeric value of 0 is still a tweakable parameter."
        )

    def test_genuine_boolean_param_is_still_excluded(self, logic_engine):
        """A real bool (True/False) must NOT be surfaced as a numeric param.

        This guards against an over-broad fix that simply removes the 0/1 filter
        and starts surfacing booleans as numeric tweakables.
        """
        tree = {
            "id": "sym-l1",
            "type": "root",
            "children": [
                {"type": "group", "enabled": True, "disabled": False, "window": 1},
            ],
        }
        params = logic_engine.extract_numeric_params(tree)
        keys = {p.get("param_key") for p in params}
        assert "enabled" not in keys and "disabled" not in keys, (
            f"extract_numeric_params surfaced a genuine boolean as a numeric param. "
            f"Found keys: {sorted(k for k in keys if k)}. "
            "L1 fix must NOT over-reach: True/False are flags, never numeric tweakables "
            "(isinstance(val, bool) excludes them even though bool is an int subclass)."
        )
        # And the numeric 1 alongside the booleans is still surfaced.
        assert "window" in keys, (
            "The numeric param 'window'=1 must still be surfaced alongside excluded booleans."
        )
