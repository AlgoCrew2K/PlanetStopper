# Tests for AC-1: V1 calibration sweep search space (2-param, corrected)
#
# AC-1 (corrected 2026-06-19): the V1 calibration sweep ALREADY EXISTS on
# origin/main (autotuner.py run_calibration_sweep) and covers TWO params:
# PARABOLIC_VELOCITY_THRESHOLD + VWAP_CROSS_HWM_PCT.
# VWAP_BREAK_CONFIRM_TICKS / VWAP_BLEED_ARM_MIN / VWAP_BLEED_ARM_MAX are
# hand-set per V1 methodology review -- no expansion this cycle.
#
# These tests are GREEN on correct state and RED if the design is violated.

import json
import pathlib

import pytest

_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "math" / "calibration_sweep_study_fixture.json"
)


@pytest.fixture(scope="module")
def fixture():
    return json.loads(_FIXTURE_PATH.read_text())


def test_para_velocity_threshold_in_search_space_keys():
    """PARABOLIC_VELOCITY_THRESHOLD must be present in OPTUNA_SEARCH_SPACE_KEYS."""
    import autotuner

    assert "PARABOLIC_VELOCITY_THRESHOLD" in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "PARABOLIC_VELOCITY_THRESHOLD missing from OPTUNA_SEARCH_SPACE_KEYS"
    )


def test_vwap_cross_hwm_pct_in_search_space_keys():
    """VWAP_CROSS_HWM_PCT must be present in OPTUNA_SEARCH_SPACE_KEYS."""
    import autotuner

    assert "VWAP_CROSS_HWM_PCT" in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "VWAP_CROSS_HWM_PCT missing from OPTUNA_SEARCH_SPACE_KEYS"
    )


def test_fixture_required_search_space_keys_matches_v1_params(fixture):
    """Fixture must list exactly the 2 V1 params, not the old 5-param version."""
    required = set(fixture["required_search_space_keys"])
    expected = {"PARABOLIC_VELOCITY_THRESHOLD", "VWAP_CROSS_HWM_PCT"}
    assert required == expected, (
        f"Fixture required_search_space_keys={sorted(required)} does not match "
        f"the corrected V1 2-param set {sorted(expected)}."
    )


def test_vwap_bleed_arm_min_not_in_search_space():
    """VWAP_BLEED_ARM_MIN must NOT be in OPTUNA_SEARCH_SPACE_KEYS.

    Intentionally hand-set per V1 methodology review. Fails if a PR adds it
    without a documented methodology decision -- AC-1 violation.
    """
    import autotuner

    assert "VWAP_BLEED_ARM_MIN" not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "VWAP_BLEED_ARM_MIN found in OPTUNA_SEARCH_SPACE_KEYS -- hand-set per "
        "V1 methodology review; requires documented decision to sweep."
    )


def test_vwap_bleed_arm_max_not_in_search_space():
    """VWAP_BLEED_ARM_MAX must NOT be in OPTUNA_SEARCH_SPACE_KEYS."""
    import autotuner

    assert "VWAP_BLEED_ARM_MAX" not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "VWAP_BLEED_ARM_MAX found in OPTUNA_SEARCH_SPACE_KEYS -- hand-set per "
        "V1 methodology review; requires documented decision to sweep."
    )


def test_vwap_break_confirm_ticks_not_in_search_space():
    """VWAP_BREAK_CONFIRM_TICKS must NOT be in OPTUNA_SEARCH_SPACE_KEYS."""
    import autotuner

    assert "VWAP_BREAK_CONFIRM_TICKS" not in autotuner.OPTUNA_SEARCH_SPACE_KEYS, (
        "VWAP_BREAK_CONFIRM_TICKS found in OPTUNA_SEARCH_SPACE_KEYS -- hand-set per "
        "V1 methodology review; requires documented decision to sweep."
    )


def test_para_velocity_sweep_bounds_are_positive_and_ordered():
    """PARABOLIC_VELOCITY_THRESHOLD sweep bounds must be positive and low < high."""
    import autotuner

    low = autotuner._SS_PARA_VEL_MIN
    high = autotuner._SS_PARA_VEL_MAX
    assert low > 0, f"_SS_PARA_VEL_MIN={low} must be positive"
    assert high > 0, f"_SS_PARA_VEL_MAX={high} must be positive"
    assert low < high, f"_SS_PARA_VEL_MIN={low} must be < _SS_PARA_VEL_MAX={high}"


def test_vwap_cross_hwm_v1_sweep_bounds_are_positive_and_ordered():
    """V1 VWAP_CROSS_HWM_PCT sweep bounds must be positive and low < high."""
    import autotuner

    low = autotuner._SS_VWAP_CROSS_HWM_V1_MIN
    high = autotuner._SS_VWAP_CROSS_HWM_V1_MAX
    assert low > 0, f"_SS_VWAP_CROSS_HWM_V1_MIN={low} must be positive"
    assert high > 0, f"_SS_VWAP_CROSS_HWM_V1_MAX={high} must be positive"
    assert low < high, f"V1 bounds must be ordered: {low} < {high}"


def test_v1_vwap_cross_hwm_lower_bound_is_below_production_lower_bound():
    """V1 lower bound must be below production lower bound (designed asymmetry)."""
    import autotuner

    v1_low = autotuner._SS_VWAP_CROSS_HWM_V1_MIN
    prod_low = autotuner._SS_VWAP_CROSS_HWM_MIN
    assert v1_low < prod_low, (
        f"V1 lower bound {v1_low} must be below production lower bound {prod_low}"
    )


def test_v1_vwap_cross_hwm_upper_bound_is_below_production_upper_bound():
    """V1 upper bound must be below production upper bound (designed asymmetry)."""
    import autotuner

    v1_high = autotuner._SS_VWAP_CROSS_HWM_V1_MAX
    prod_high = autotuner._SS_VWAP_CROSS_HWM_MAX
    assert v1_high < prod_high, (
        f"V1 upper bound {v1_high} must be below production upper bound {prod_high}"
    )


def test_nn1_validation_passes_on_current_search_space():
    """validate_search_space_nn1() must not raise on the current search space."""
    import autotuner

    try:
        autotuner.validate_search_space_nn1()
    except RuntimeError as exc:
        pytest.fail(f"validate_search_space_nn1 raised NN1 violation: {exc}.")
