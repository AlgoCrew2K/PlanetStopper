"""
R3-c / MA-11 AC-9 — scope guards. Wiring MAX_SQUEEZE_FLOOR must NOT expand the
Optuna search space nor touch the MAX_PARABOLIC_SQUEEZE range constants (their
re-examination is explicitly deferred to R3-d).

These are REGRESSION guards (GREEN at HEAD and post-impl): they must keep
passing — an impl that pulled MAX_SQUEEZE_FLOOR into the search space, or nudged
the parabolic-squeeze bounds, turns them RED.
"""

from __future__ import annotations

import autotuner

_EXPECTED_OPTUNA_KEYS = frozenset(
    {
        "TAKE_PROFIT_MC_PCT",
        "VWAP_CROSS_HWM_PCT",
        "VWAP_BLEED_MULTIPLIER",
        "VWAP_BLEED_TICKS",
        "PARABOLIC_VELOCITY_THRESHOLD",
        "MAX_PARABOLIC_SQUEEZE",
    }
)


def test_optuna_search_space_is_exactly_the_six_keys() -> None:
    """AC-9: MAX_SQUEEZE_FLOOR stays OUT of the Optuna search space (Optuna
    structurally never tunes it — it is hand-set/advisor-suggested only)."""
    assert autotuner.OPTUNA_SEARCH_SPACE_KEYS == _EXPECTED_OPTUNA_KEYS, (
        "OPTUNA_SEARCH_SPACE_KEYS must remain exactly the 6 tuned keys; "
        f"got {set(autotuner.OPTUNA_SEARCH_SPACE_KEYS)}."
    )
    assert "MAX_SQUEEZE_FLOOR" not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "MAX_SQUEEZE_FLOOR must NOT enter the Optuna search space in R3-c."
    )


def test_parabolic_squeeze_range_constants_untouched() -> None:
    """AC-9: the MAX_PARABOLIC_SQUEEZE [0.1, 0.8] Optuna range is untouched
    (its re-examination is deferred to R3-d)."""
    assert autotuner._SS_MAX_PARA_SQUEEZE_MIN == 0.1, (
        f"_SS_MAX_PARA_SQUEEZE_MIN must stay 0.1, got {autotuner._SS_MAX_PARA_SQUEEZE_MIN}."
    )
    assert autotuner._SS_MAX_PARA_SQUEEZE_MAX == 0.8, (
        f"_SS_MAX_PARA_SQUEEZE_MAX must stay 0.8, got {autotuner._SS_MAX_PARA_SQUEEZE_MAX}."
    )
