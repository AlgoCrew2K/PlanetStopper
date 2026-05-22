"""
Cluster 4 — AC-1 (audit H-5 / methodology M-2): the DSR non-normality moments
γ3 / γ4 are computed from the strategy's OWN per-period return series, not from
the cross-trial distribution of trial scores.

RED tests, written before the implementation.

Audit H-5 / M-2: the live code computes γ3 / γ4 from `trial_values` — the
cross-trial distribution of Sortino ratios across the Optuna trials
(autotuner.py:899-900 per-symphony, :1188-1189 calibration sweep) — and passes
them into `compute_deflated_sharpe_ratio`'s denominator. This is a category
error. In Bailey & López de Prado 2014 Eq. 9, γ3 / γ4 in the DSR denominator are
the skewness and kurtosis of the STRATEGY'S OWN RETURN SERIES — they correct for
non-normality of the return stream being evaluated. The cross-trial moments
belong ONLY in `SR_0` (the expected-maximum-Sharpe selection-bias term).

AC-1: γ3 / γ4 must be sourced from the per-period return series the trial's
score was computed over (the `daily_returns` list `_collect_sim_returns`
produces). The cross-trial mean / std remain ONLY in the `SR_0` term.

MECHANISM: the trials currently carry no return series — the objective discards
`daily_returns` and keeps only the scalar Sortino. The implementer must persist
each trial's return series (e.g. `trial.set_user_attr("daily_returns", ...)` in
the objective; confirmed to round-trip) and read it back per trial in the
re-ranking loop. These tests are written against that mechanism but assert
BEHAVIOUR (which distribution the denominator moments come from), not the exact
attribute name — adjust `_RETURN_SERIES_ATTR` here if the implementer's name
differs.

The metric-CONSISTENCY of the denominator FORMULA (Sortino vs Sharpe Eq. 9) is
AC-2, a separate file pending the Decision-D3 methodology choice. AC-1 is only
about WHERE γ3 / γ4 come from.

Reference: Bailey & López de Prado 2014, "The Deflated Sharpe Ratio", JPM 40(5),
DOI 10.3905/jpm.2014.40.5.094, Eq. 9 — γ3 / γ4 are the strategy return series'
skewness / kurtosis.
"""

from __future__ import annotations

import ast
import contextlib
import io
import math
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"

# The trial user-attr key under which the implementer persists the per-trial
# return series. If the implementer chooses a different key, update this one
# constant — every behavioural test below derives from it.
_RETURN_SERIES_ATTR = "daily_returns"


def _import_autotuner():
    import autotuner
    return autotuner


# ---------------------------------------------------------------------------
# Independent moment helpers — population skewness / kurtosis. These are the
# reference computation; the test derives expecteds from them, never from the
# producer. Population (divisor N) convention matches the live code's existing
# cross-trial moment computation (autotuner.py:896-900).
# ---------------------------------------------------------------------------


def _population_skew_kurt(series: list[float]) -> tuple[float, float]:
    """Return (skewness, kurtosis) of a series, population convention (divisor N).

    skewness = (1/N) Σ (x-μ)^3 / σ^3
    kurtosis = (1/N) Σ (x-μ)^4 / σ^4   (non-excess: a normal series gives ~3.0)
    """
    n = len(series)
    mean = sum(series) / n
    var = sum((x - mean) ** 2 for x in series) / n
    std = math.sqrt(var)
    if std == 0.0:
        return 0.0, 3.0
    skew = sum((x - mean) ** 3 for x in series) / (n * std ** 3)
    kurt = sum((x - mean) ** 4 for x in series) / (n * std ** 4)
    return skew, kurt


# ===========================================================================
# Source-level — γ3 / γ4 are not computed from trial_values
# ===========================================================================


def test_gamma_moments_not_computed_from_trial_values():
    """
    AC-1: γ3 / γ4 must NOT be computed from `trial_values` (the cross-trial
    score distribution).

    AST inspection: find every assignment to a name containing "gamma" and reject
    it if its RHS sums powers (**3 / **4) over `trial_values`. The live code does
    exactly `gamma3 = sum((v - mean_v) ** 3 for v in trial_values) / ...` — this
    test pins that the cross-trial-derived γ assignment is gone.

    `mean_v` / `std_v` derived from `trial_values` are STILL permitted — they
    legitimately feed SR_0. Only the γ3 / γ4 (skew / kurtosis) moments must move.
    """
    tree = ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))

    def _iterates_trial_values(node: ast.AST) -> bool:
        """True if a comprehension in `node` iterates over `trial_values`."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.comprehension):
                it = sub.iter
                if isinstance(it, ast.Name) and it.id == "trial_values":
                    return True
        return False

    offending: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            is_gamma = any("gamma" in n.lower() for n in names)
            if is_gamma and _iterates_trial_values(node.value):
                offending.append(
                    f"line {node.lineno}: `{', '.join(names)}` computed by "
                    f"iterating `trial_values` (the cross-trial score distribution)"
                )

    assert not offending, (
        "autotuner.py still computes the DSR γ3 / γ4 from `trial_values` — the "
        "cross-trial score distribution. AC-1 (audit H-5) requires γ3 / γ4 to be "
        "the skewness / kurtosis of the candidate trial's OWN return series. The "
        "cross-trial moments belong only in SR_0. Offending assignments:\n  "
        + "\n  ".join(offending)
    )


def test_objective_persists_the_per_trial_return_series():
    """
    AC-1 mechanism: the objective closure must persist each trial's per-period
    return series so the DSR re-ranking loop can compute that trial's own γ3 / γ4.

    The trials carry only the scalar Sortino today. AST check: the autotuner
    source must call `trial.set_user_attr(...)` (or `.set_user_attr` on whatever
    the trial parameter is named) — the mechanism that makes the return series
    survive the objective.

    If the implementer chooses a different persistence mechanism, this test is the
    place to reconcile it — but the per-trial return series MUST be retrievable in
    the re-ranking loop, or AC-1's "the trial's own return series" is impossible.
    """
    src = _AUTOTUNER_SRC.read_text(encoding="utf-8")

    assert "set_user_attr" in src, (
        "autotuner.py does not call `set_user_attr`. AC-1 requires each trial's "
        "per-period return series to be persisted on the trial so the DSR "
        "re-ranking loop can compute that trial's OWN γ3 / γ4. The objective "
        "currently discards `daily_returns` after computing the scalar Sortino."
    )


# ===========================================================================
# Behavioural — the DSR denominator moments come from the return series
# ===========================================================================


def test_dsr_gamma_moments_match_the_winning_trials_return_series():
    """
    AC-1 (behavioural): the γ3 / γ4 fed into the DSR for a given trial must be the
    skewness / kurtosis of THAT TRIAL'S OWN return series — not the moments of the
    cross-trial score distribution.

    Construction — the two distributions are deliberately made to disagree:
      - Each trial carries a return series with a KNOWN, strongly NEGATIVE skew.
      - The cross-trial distribution of trial `value`s is built SYMMETRIC
        (near-zero skew).
    So the return-series skew and the cross-trial skew are far apart. The test
    asserts the DSR received the RETURN-SERIES skew/kurtosis.

    Capture: `compute_deflated_sharpe_ratio` is patched to record the (γ3, γ4) it
    is called with. The recorded moments must match `_population_skew_kurt` of the
    winning trial's persisted return series — and must NOT match the cross-trial
    score moments.

    Tolerance: 1e-6 absolute — the moments are a deterministic finite-sum
    computation; the band only absorbs float summation order differences between
    the test's reference helper and the implementation.
    """
    autotuner = _import_autotuner()

    default_params = {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }

    # A strongly LEFT-skewed return series — many small gains, one large loss.
    # The same series is attached to every trial so the winning trial's moments
    # are unambiguous regardless of which trial the re-ranking picks.
    left_skewed_returns = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, -9.0]
    rs_skew, rs_kurt = _population_skew_kurt(left_skewed_returns)
    # Sanity: this series really is strongly negatively skewed.
    assert rs_skew < -1.5, (
        f"Test construction: return series must be strongly left-skewed; "
        f"got skew {rs_skew}."
    )

    # Cross-trial score distribution — deliberately SYMMETRIC (near-zero skew),
    # so it cannot be confused with the return-series moments.
    trial_scores = [0.6, 0.8, 1.0, 1.2, 1.4]  # symmetric about 1.0
    cross_skew, cross_kurt = _population_skew_kurt(trial_scores)
    assert abs(cross_skew) < 0.1, (
        f"Test construction: cross-trial scores must be near-symmetric; "
        f"got skew {cross_skew}."
    )
    assert abs(cross_skew - rs_skew) > 1.0, (
        "Test construction: return-series and cross-trial skew must differ "
        "enough to tell which one the DSR used."
    )

    trials = []
    for s in trial_scores:
        t = MagicMock()
        t.value = s
        t.params = default_params.copy()
        t.user_attrs = {_RETURN_SERIES_ATTR: list(left_skewed_returns)}
        trials.append(t)

    fake_study = MagicMock()
    fake_study.trials = trials
    fake_study.best_trial = trials[-1]
    fake_study.best_params = default_params.copy()
    fake_study.best_value = trial_scores[-1]
    fake_study.optimize = MagicMock(return_value=None)

    captured: list[tuple[float, float]] = []
    real_dsr = autotuner.compute_deflated_sharpe_ratio

    def capturing_dsr(SR_obs, SR_0, gamma3, gamma4, T):
        captured.append((gamma3, gamma4))
        return real_dsr(SR_obs=SR_obs, SR_0=SR_0, gamma3=gamma3,
                        gamma4=gamma4, T=T)

    bot_state = {"sym-A": {"name": "Moment Provenance Test", "account_uuid": "acc-1"}}
    history = {
        "sym-A": {
            "2026-04-01": [{"return": 2.0, "mc_prob": 50.0, "vol": 1.0,
                            "vwap_diff": -2.0, "base_atr_pct": 1.0,
                            "valid_vwap_weight": 1.0}],
            "2026-04-02": [{"return": 2.0, "mc_prob": 50.0, "vol": 1.0,
                            "vwap_diff": -2.0, "base_atr_pct": 1.0,
                            "valid_vwap_weight": 1.0}],
        }
    }

    with (
        patch("autotuner.optuna.create_study", return_value=fake_study),
        patch("autotuner.optuna.storages.RDBStorage", return_value=MagicMock()),
        patch("autotuner.synthetic_history.generate_synthetic_history",
              return_value=history),
        patch("autotuner.database.load_chart_history", return_value={}),
        patch("autotuner.database.save_chart_archive"),
        patch("autotuner.database.get_symphony_strategy",
              return_value={"params": default_params.copy(), "locked_vars": []}),
        patch("autotuner.database.save_symphony_strategy"),
        patch("autotuner.database.DEFAULT_STRATEGY", default_params),
        patch("autotuner.database.save_autotune_run"),
        patch("autotuner.compute_deflated_sharpe_ratio", side_effect=capturing_dsr),
        patch("autotuner.math_engine.compute_para_arm_decision",
              side_effect=lambda **kw: (0.0, False)),
        patch("autotuner.math_engine.compute_time_squeeze_decay",
              side_effect=lambda tr: (1.5, 0.5)),
        patch("autotuner.math_engine.compute_active_trailing_stop",
              side_effect=lambda *a, **kw: 5.0),
        patch("autotuner.math_engine.compute_breakeven_update",
              side_effect=lambda *a, **kw: (a[3], a[4], a[2])),
        patch("autotuner.math_engine.compute_vwap_bleed_arm_threshold",
              side_effect=lambda *a, **kw: -10.0),
        patch("autotuner.math_engine.compute_vwap_breakdown_update",
              side_effect=lambda **kw: (0, 0, True, False)),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        autotuner.run_autotuner(bot_state, "2026-05-10", ["acc-1"])

    # The re-ranking loop must have run the DSR at least once per trial.
    assert captured, (
        "compute_deflated_sharpe_ratio was never called — the DSR re-ranking "
        "loop did not execute. Test scenario is not exercising AC-1."
    )

    for gamma3, gamma4 in captured:
        # Must match the RETURN SERIES moments.
        assert gamma3 == pytest.approx(rs_skew, abs=1e-6), (
            f"AC-1 VIOLATED: DSR γ3 = {gamma3!r}.\n"
            f"  return-series skew (AC-1 target)  = {rs_skew!r}\n"
            f"  cross-trial score skew (the H-5 bug) = {cross_skew!r}\n"
            f"γ3 must be the skewness of the trial's OWN return series, not the "
            f"cross-trial score distribution."
        )
        assert gamma4 == pytest.approx(rs_kurt, abs=1e-6), (
            f"AC-1 VIOLATED: DSR γ4 = {gamma4!r}.\n"
            f"  return-series kurtosis (AC-1 target)  = {rs_kurt!r}\n"
            f"  cross-trial score kurtosis (the H-5 bug) = {cross_kurt!r}\n"
            f"γ4 must be the kurtosis of the trial's OWN return series."
        )
        # And must NOT match the cross-trial moments (the bug's signature).
        assert gamma3 != pytest.approx(cross_skew, abs=1e-3), (
            f"AC-1 VIOLATED: DSR γ3 = {gamma3!r} matches the cross-trial score "
            f"skew {cross_skew!r} — γ3 is still sourced from `trial_values`."
        )
