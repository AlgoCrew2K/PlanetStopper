"""
Cluster 4 — AC-5 (audit M-3 / M-6): the DSR `SR_0` benchmark is evaluated under
the zero-skill null — the mean term is 0, the trial-spread term is kept.

RED tests, written before the implementation.

Audit finding M-6: `compute_expected_max_sharpe` returns
    SR_0 = sr_mean + sr_std * ((1-γ_E)·Φ⁻¹(1-1/N) + γ_E·Φ⁻¹(1-1/(N·e)))
The general expected-maximum expression DOES include the additive `E[SR_n]` term,
so the function itself is not wrong. But when this expression is consumed as the
DSR benchmark `SR_0`, Bailey & López de Prado 2014 evaluate it under the null
hypothesis of zero skill — `E[SR_n] = 0`. `SR_0` answers "what is the largest
Sharpe you would expect from N trials of a SKILL-LESS strategy?"; that benchmark
must be built on a zero-mean null.

The live autotuner passes the EMPIRICAL cross-trial mean (`mean_v`) as `sr_mean`
(autotuner.py:904-908 per-symphony, :1193-1197 calibration sweep). AC-5 requires
the DSR `SR_0` to be evaluated with the mean term set to 0 while keeping the
spread term from the trial std.

These tests pin BEHAVIOUR, not a mechanism — the implementer may pass
`sr_mean=0.0` to the existing function, add a dedicated null-benchmark helper, or
otherwise; what is fixed is that the resulting `SR_0` does not carry the empirical
trial mean.

Reference: Bailey & López de Prado 2014, "The Deflated Sharpe Ratio", JPM 40(5),
DOI 10.3905/jpm.2014.40.5.094 — `SR_0` as the null benchmark.
"""

from __future__ import annotations

import ast
import math
import pathlib

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent
_AUTOTUNER_SRC = _WORKTREE_ROOT / "autotuner.py"


def _import_autotuner():
    import autotuner
    return autotuner


def _import_math_engine():
    import math_engine
    return math_engine


# ===========================================================================
# Behavioural — the SR_0 the DSR consumes is mean-invariant
# ===========================================================================


def test_expected_max_sharpe_spread_term_is_independent_of_mean():
    """
    `compute_expected_max_sharpe` is the underlying primitive. The SPREAD term —
    everything except the additive mean — must be exactly recoverable so a
    null-mean SR_0 (mean term = 0) can be derived from it.

    Property: for fixed sr_std and n_trials,
        compute_expected_max_sharpe(m, sr_std, N) - m
    must be CONSTANT across all `m`. That constant is the pure expected-max
    spread term — the value AC-5 wants for SR_0 (i.e. the result with mean=0).

    Tolerance: 1e-9 absolute — the formula is `m + spread`, so the difference is
    analytically exact; only floating-point addition noise is allowed.
    """
    me = _import_math_engine()

    sr_std = 0.3
    n_trials = 500
    means = [-2.0, -0.4, 0.0, 0.5, 1.7, 4.0]

    spreads = [
        me.compute_expected_max_sharpe(m, sr_std, n_trials) - m
        for m in means
    ]

    reference = spreads[0]
    for m, spread in zip(means, spreads):
        assert spread == pytest.approx(reference, abs=1e-9), (
            f"compute_expected_max_sharpe spread term is not mean-invariant.\n"
            f"  mean={m}: result-mean spread = {spread!r}\n"
            f"  reference spread (mean={means[0]}) = {reference!r}\n"
            f"The expected-max expression must be `mean + spread`; the spread "
            f"must not depend on the mean, so a null-mean SR_0 is recoverable."
        )

    # The null-mean SR_0 (mean term = 0) equals exactly that spread term, and is
    # strictly positive for N>1, sr_std>0 (selection bias raises the benchmark).
    sr0_null = me.compute_expected_max_sharpe(0.0, sr_std, n_trials)
    assert sr0_null == pytest.approx(reference, abs=1e-9), (
        f"compute_expected_max_sharpe(0.0, sr_std, N) must equal the pure spread "
        f"term ({reference!r}); got {sr0_null!r}."
    )
    assert sr0_null > 0.0, (
        f"Null-mean SR_0 must be strictly positive for N>1 and sr_std>0 "
        f"(selection bias across N trials raises the no-skill benchmark); "
        f"got {sr0_null!r}."
    )


def test_dsr_re_ranking_consumes_a_null_mean_sr0():
    """
    AC-5 (behavioural): the `SR_0` actually fed into the per-trial DSR re-ranking
    must be the null-mean expected-max value — equal to a mean-zero
    compute_expected_max_sharpe call, NOT the mean-inclusive one.

    This drives the REAL DSR re-ranking block inside run_autotuner with a study
    of >=2 completed trials whose Sortino values have a deliberately LARGE
    positive mean and modest spread, so the null-mean SR_0 and the buggy
    mean-inclusive SR_0 differ by ~5.0 — far outside any rounding band.

    Capture: compute_deflated_sharpe_ratio is patched to record every `SR_0` it
    is called with in the re-ranking loop. The recorded SR_0 must equal the
    null-mean expected-max value computed independently here.

    The buggy code passes `sr_mean=mean_v` and the recorded SR_0 would be
    ~mean+spread; AC-5 makes it ~spread. The test fails RED on the buggy code.
    """
    import contextlib
    import io
    from unittest.mock import MagicMock, patch

    autotuner = _import_autotuner()
    me = _import_math_engine()

    default_params = {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }

    # Cross-trial Sortino distribution: large mean (~5.0), modest spread.
    trial_sortinos = [4.6, 4.8, 5.0, 5.2, 5.4]
    n = len(trial_sortinos)
    mean_v = sum(trial_sortinos) / n
    var_v = sum((v - mean_v) ** 2 for v in trial_sortinos) / n
    std_v = math.sqrt(var_v)

    # Independently derived expectations.
    sr0_null = me.compute_expected_max_sharpe(0.0, std_v, n)
    sr0_with_mean = me.compute_expected_max_sharpe(mean_v, std_v, n)
    assert (sr0_with_mean - sr0_null) == pytest.approx(mean_v, abs=1e-9), (
        "Test construction error: null-mean and mean-inclusive SR_0 must differ "
        f"by exactly mean_v={mean_v}."
    )

    # Build >=2 completed trials carrying the trial Sortino values.
    trials = []
    for s in trial_sortinos:
        t = MagicMock()
        t.value = s
        t.params = default_params.copy()
        trials.append(t)

    fake_study = MagicMock()
    fake_study.trials = trials
    fake_study.best_trial = trials[-1]
    fake_study.best_params = default_params.copy()
    fake_study.best_value = trial_sortinos[-1]
    fake_study.optimize = MagicMock(return_value=None)

    captured_sr0: list[float] = []

    real_dsr = autotuner.compute_deflated_sharpe_ratio

    def capturing_dsr(SR_obs, SR_0, gamma3, gamma4, T):
        captured_sr0.append(SR_0)
        return real_dsr(SR_obs=SR_obs, SR_0=SR_0, gamma3=gamma3, gamma4=gamma4, T=T)

    bot_state = {"sym-A": {"name": "SR0 Null Test", "account_uuid": "acc-1"}}
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

    # The re-ranking loop must have invoked the DSR per trial.
    reranking_sr0 = [s for s in captured_sr0 if math.isfinite(s)]
    assert reranking_sr0, (
        "compute_deflated_sharpe_ratio was never called with a finite SR_0 — the "
        "DSR re-ranking loop did not run. Test scenario is not exercising AC-5."
    )

    # Every SR_0 fed into the re-ranking loop must be the null-mean value.
    for sr0 in reranking_sr0:
        assert sr0 == pytest.approx(sr0_null, abs=1e-6), (
            f"AC-5 VIOLATED: the DSR re-ranking consumed SR_0={sr0!r}.\n"
            f"  null-mean SR_0 (AC-5 target)      = {sr0_null!r}\n"
            f"  mean-inclusive SR_0 (the H-6 bug) = {sr0_with_mean!r}\n"
            f"The DSR `SR_0` benchmark must be evaluated under the zero-skill "
            f"null (mean term = 0), keeping only the trial-spread term."
        )


# ===========================================================================
# Source-level — the DSR SR_0 derivation does not pass the empirical mean
# ===========================================================================


def test_dsr_sr0_derivation_does_not_pass_mean_v():
    """
    AC-5: the `compute_expected_max_sharpe` call whose result becomes the DSR
    `SR_0` must NOT pass the empirical trial mean (`mean_v`) as `sr_mean`.

    AST check: every `compute_expected_max_sharpe(...)` call in autotuner.py must
    pass `sr_mean` as either the literal `0.0`/`0` OR a name that is not a
    cross-trial-mean name (`mean_v`, `mean_tv`, etc.). The spread input
    (`sr_std=std_v`) stays — only the mean term is nulled.

    The implementer may instead delete the `sr_mean` argument if they add a
    dedicated null-benchmark helper; this test then still passes because no
    `compute_expected_max_sharpe` call would carry `sr_mean=mean_v`.
    """
    tree = ast.parse(_AUTOTUNER_SRC.read_text(encoding="utf-8"))

    ems_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "compute_expected_max_sharpe"
    ]

    assert len(ems_calls) >= 2, (
        f"Expected at least 2 compute_expected_max_sharpe call sites in "
        f"autotuner.py (per-symphony + calibration sweep); found {len(ems_calls)}."
    )

    for call in ems_calls:
        sr_mean_kwargs = [kw for kw in call.keywords if kw.arg == "sr_mean"]
        if not sr_mean_kwargs:
            # No sr_mean kwarg — acceptable if the implementer routed via a
            # dedicated null-benchmark helper. Nothing to flag here.
            continue
        value = sr_mean_kwargs[0].value

        if isinstance(value, ast.Constant):
            assert value.value in (0, 0.0), (
                f"line {call.lineno}: compute_expected_max_sharpe is passed "
                f"sr_mean={value.value!r}. AC-5 requires the DSR SR_0 to be "
                f"evaluated under the zero-skill null — sr_mean must be 0.0."
            )
        elif isinstance(value, ast.Name):
            assert "mean" not in value.id.lower(), (
                f"line {call.lineno}: compute_expected_max_sharpe is passed "
                f"sr_mean=`{value.id}` — the empirical cross-trial mean. AC-5 "
                f"requires the DSR SR_0's mean term to be 0 (zero-skill null)."
            )
        else:
            pytest.fail(
                f"line {call.lineno}: compute_expected_max_sharpe `sr_mean` "
                f"argument is an unexpected expression ({ast.dump(value)}). "
                f"AC-5 expects a literal 0.0 (or no sr_mean argument)."
            )
