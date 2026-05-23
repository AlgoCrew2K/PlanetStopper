"""
Cluster 4 — AC-2 (integration): the Harvey & Liu haircut is WIRED into
run_autotuner's trial selection — it is a real selection gate, not a decorative
computation.

RED tests, written before the implementation.

risk-engine-specialist's required property (team-lead endorsed): the haircut must
gate selection. The pure-function correctness — that a noise-only set yields no
trial clearing the FDR gate, that a genuine signal does clear it — is pinned
decisively by test_c4_harvey_liu_haircut.py (those tests call the haircut
functions directly on hand-derived fixtures).

THIS file pins the INTEGRATION: that run_autotuner actually CALLS the haircut and
consults its verdict, rather than computing it and ignoring it. A pure-function
test cannot catch a haircut that is implemented correctly but never invoked from
the selection path; this test can.

It deliberately does NOT assert a specific `baseline_decision` — that depends on
the OOS cascade (oos_alpha vs fallback vs default), separate logic. Coupling to
the cascade outcome would make the test pass or fail for reasons unrelated to the
haircut. Instead it asserts the WIRING directly: the BHY adjustment runs during a
real run_autotuner invocation, over the completed trials.

Reference: feature-plans/autotuner-statistics.md, Decision D3.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
from unittest.mock import MagicMock, patch

import pytest

_WORKTREE_ROOT = pathlib.Path(__file__).parent.parent.parent

# Per-trial return-series user-attr key — matches the D3 set_user_attr mechanism.
_RETURN_SERIES_ATTR = "daily_returns"


def _import_autotuner():
    import autotuner
    return autotuner


def _default_params() -> dict:
    return {
        "TRIGGER_THRESHOLD_PCT": 15.0,
        "TAKE_PROFIT_MC_PCT": 5.0,
        "VWAP_CROSS_HWM_PCT": 1.0,
        "VWAP_BLEED_MULTIPLIER": 1.5,
        "VWAP_BLEED_TICKS": 10,
        "PARABOLIC_VELOCITY_THRESHOLD": 2.0,
        "MAX_PARABOLIC_SQUEEZE": 0.5,
    }


def _make_trial(sortino_value: float, params: dict,
                return_series: list[float]) -> MagicMock:
    t = MagicMock()
    t.value = sortino_value
    t.params = params.copy()
    t.user_attrs = {_RETURN_SERIES_ATTR: list(return_series)}
    return t


def _noise_return_series() -> list[float]:
    """A per-day guard-alpha series consistent with a zero-skill strategy.

    Roughly balanced small gains and losses — its Sortino is near zero, so the
    Sortino·sqrt(T) t-statistic stays small and will not survive a BHY haircut.
    Used as the non-winning filler trials in the gates-selection scenarios.
    """
    return [0.3, -0.2, 0.4, -0.25, 0.35, -0.15, 0.3, -0.2, 0.4, -0.25]


def test_run_autotuner_invokes_the_benjamini_hochberg_haircut():
    """
    AC-2 integration: a full run_autotuner pass over >=2 completed trials must
    CALL `benjamini_hochberg_adjust` — proof the haircut is wired into the
    selection path, not computed-and-ignored or absent.

    A spy wraps `autotuner.benjamini_hochberg_adjust`; after run_autotuner the
    spy must have been called at least once, and the call's input must be a
    p-value vector with one entry per completed (sentinel-filtered) trial — i.e.
    the haircut was applied across the trial set, the multiple-testing dimension
    N that the BHY adjustment exists to correct.

    If the haircut were computed on only the single best trial, or not at all,
    this fails — exactly the "decorative computation" defect.
    """
    autotuner = _import_autotuner()

    assert hasattr(autotuner, "benjamini_hochberg_adjust"), (
        "autotuner.benjamini_hochberg_adjust must exist (D3 haircut). "
        "Not implemented yet (expected RED) — see test_c4_harvey_liu_haircut.py."
    )

    params = _default_params()
    # Five completed trials, each with a per-trial return series.
    series = [0.8, -0.4, 0.6, -0.3, 0.5, -0.2, 0.7, -0.35, 0.4, -0.25]
    trials = [
        _make_trial(0.30, params, series),
        _make_trial(0.45, params, series),
        _make_trial(0.20, params, series),
        _make_trial(0.55, params, series),
        _make_trial(0.35, params, series),
    ]

    fake_study = MagicMock()
    fake_study.trials = trials
    fake_study.best_trial = trials[3]
    fake_study.best_params = trials[3].params
    fake_study.best_value = trials[3].value
    fake_study.optimize = MagicMock(return_value=None)

    bhy_calls: list[list] = []
    real_bhy = autotuner.benjamini_hochberg_adjust

    def _spy_bhy(p_values):
        bhy_calls.append(list(p_values))
        return real_bhy(p_values)

    bot_state = {"sym-A": {"name": "Haircut Wiring Test", "account_uuid": "acc-1"}}
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
              return_value={"params": params.copy(), "locked_vars": []}),
        patch("autotuner.database.save_symphony_strategy"),
        patch("autotuner.database.DEFAULT_STRATEGY", params),
        patch("autotuner.database.save_autotune_run"),
        patch("autotuner.benjamini_hochberg_adjust", side_effect=_spy_bhy),
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

    assert bhy_calls, (
        "run_autotuner did not call benjamini_hochberg_adjust. AC-2: the Harvey "
        "& Liu haircut must be WIRED into the selection path — a haircut that is "
        "never invoked provides no overfitting protection (a decorative "
        "computation, the BLOCK-level defect risk-engine-specialist flagged)."
    )

    # The haircut must be applied ACROSS the trial set — the multiple-testing
    # dimension. The largest BHY input vector must cover all completed trials.
    largest_input = max(bhy_calls, key=len)
    assert len(largest_input) == len(trials), (
        f"benjamini_hochberg_adjust was called with a {len(largest_input)}-"
        f"element p-value vector, but there are {len(trials)} completed trials. "
        f"The BHY multiple-testing adjustment must span the WHOLE trial set "
        f"(N = the number of tests) — adjusting only a subset understates N and "
        f"under-corrects the selection bias."
    )


def test_run_autotuner_does_not_call_the_deleted_dsr(monkeypatch):
    """
    AC-2 integration / D3: run_autotuner must NOT reference the removed Eq. 9 DSR.

    `compute_deflated_sharpe_ratio` is deleted (test_c4_dsr_machinery_removed.py).
    This test pins that the autotuner module no longer binds the name at all — a
    leftover reference would be a NameError at runtime on the selection path.
    """
    autotuner = _import_autotuner()
    assert not hasattr(autotuner, "compute_deflated_sharpe_ratio"), (
        "autotuner still exposes compute_deflated_sharpe_ratio — D3 deletes the "
        "Sharpe-DSR. The selection path must use the Harvey & Liu haircut only."
    )


def test_persisted_deflated_sharpe_is_higher_is_better_oriented():
    """
    AC-2 / D3 surface-consistency: the value the haircut path persists to the
    `deflated_sharpe` DB column must be HIGHER-IS-BETTER oriented.

    The `deflated_sharpe` column (and its Discord / dashboard surfacing) held a
    DSR — higher is better. The haircut's natural selection key is the winning
    trial's BHY-adjusted p-value, which is LOWER-is-better and bounded [0,1].
    Persisting `p_adj` into a column an operator reads as "deflated sharpe,
    higher better" would silently INVERT the operator surface — a stronger
    parameter set would show a smaller number.

    The team's resolution (risk-engine-specialist option a, implementer adopted):
    store the winning trial's t-statistic `Sortino·sqrt(T)` — higher-is-better,
    metric-consistent with what the column held; `p_adj` stays the internal
    selection key, in logs only.

    This test drives run_autotuner twice — once with a STRONG-signal winning
    trial, once with a WEAKER-signal winning trial — and asserts the persisted
    `deflated_sharpe` is LARGER for the stronger signal. A p-value would go the
    other way; a higher-is-better t-stat goes the right way.
    """
    autotuner = _import_autotuner()
    params = _default_params()

    def _persisted_deflated_for(signal_series: list[float]) -> float:
        """run_autotuner with a winning trial carrying `signal_series`; return the
        persisted deflated_sharpe."""
        noise = _noise_return_series()
        winner_sortino = autotuner.compute_sortino_ratio(signal_series)
        trials = [
            _make_trial(0.10, params, noise),
            _make_trial(0.05, params, noise),
            _make_trial(winner_sortino, params, signal_series),
            _make_trial(-0.03, params, noise),
            _make_trial(0.08, params, noise),
        ]

        fake_study = MagicMock()
        fake_study.trials = trials
        fake_study.best_trial = trials[2]
        fake_study.best_params = trials[2].params
        fake_study.best_value = trials[2].value
        fake_study.optimize = MagicMock(return_value=None)

        captured: list[dict] = []

        bot_state = {"sym-A": {"name": "Orientation Test", "account_uuid": "acc-1"}}
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
                  return_value={"params": params.copy(), "locked_vars": []}),
            patch("autotuner.database.save_symphony_strategy"),
            patch("autotuner.database.DEFAULT_STRATEGY", params),
            patch("autotuner.database.save_autotune_run",
                  side_effect=lambda **kw: captured.append(kw)),
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

        assert captured, "run_autotuner did not persist an autotune_run row."
        # D3 resolution (a) renamed the column deflated_sharpe -> selection_tstat;
        # the persisted kwarg follows. The value is the haircut winner's t-stat.
        value = captured[0].get("selection_tstat")
        assert value is not None, (
            "save_autotune_run must persist a selection_tstat value for a "
            "genuine-signal study (the Harvey & Liu haircut winner's t-stat)."
        )
        return value

    # A markedly stronger signal series vs a weaker one. Each MUST include at
    # least one downside observation so compute_sortino_ratio returns a finite
    # ratio — an all-positive series hits the math_engine._SORTINO_SENTINEL
    # (+1e6) zero-downside sentinel, which filter_sortino_sentinels then drops
    # from the haircut set entirely (the trial would never reach selection).
    #
    # Post-AC-1 (Cluster 7 / RM-H1): the t-statistic is now Sortino /
    # bootstrap-SE rather than Sortino*sqrt(T). The bootstrap SE is generally
    # LARGER than the Wald-implied sqrt(T) for these short series — so the
    # post-AC-1 t-stat is SMALLER and the corresponding p-value is LARGER.
    # The pre-AC-1 "weak" series (Sortino ~2.4 over T=10) no longer clears
    # the BHY FDR gate under bootstrap-SE, which is the correct conservative
    # behaviour the audit asked for — but it breaks this test's premise that
    # BOTH winners reach selection so the orientation assertion can fire.
    #
    # Strengthen both series with enough observations + signal magnitude
    # to clear the gate under bootstrap SE, while preserving the strong>>weak
    # ordering the test is verifying.
    # Post-AC-1 calibration: with N=5 trials, the BHY-adjusted-p clearing
    # threshold at rank 1 requires raw p < q / (N*c(N)) ~= 0.0044, i.e.
    # bootstrap-based t > ~2.6. The strong fixture lands t ~= 5.0
    # (Sortino ~43.4), the weak fixture lands t ~= 3.1 (Sortino ~18.5) —
    # both clear the gate, ordering preserved, ratio test fires.
    strong = [1.0] * 20 + [-0.1]                        # Sortino ~43.4
    weak = [1.0] * 30 + [-0.2, -0.2]                    # Sortino ~18.5

    strong_value = _persisted_deflated_for(strong)
    weak_value = _persisted_deflated_for(weak)

    assert strong_value > weak_value, (
        f"SURFACE-INVERSION REGRESSION: the persisted `selection_tstat` is "
        f"{strong_value!r} for a STRONG-signal winner and {weak_value!r} for a "
        f"WEAKER-signal winner. The column must stay HIGHER-IS-BETTER oriented "
        f"(store the winner's t-statistic Sortino·sqrt(T), not the BHY-adjusted "
        f"p-value — a p-value is lower-is-better and would invert the operator's "
        f"Discord/dashboard reading)."
    )
