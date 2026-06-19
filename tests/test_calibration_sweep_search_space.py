"""
RED tests for AC-1: Optuna walk-forward search space expansion.

Asserts that OPTUNA_SEARCH_SPACE_KEYS includes all 5 AC-1 params
(PARABOLIC_VELOCITY_THRESHOLD, VWAP_CROSS_HWM_PCT, VWAP_BREAK_CONFIRM_TICKS,
VWAP_BLEED_ARM_MIN, VWAP_BLEED_ARM_MAX) with correct bound constants and
integer-vs-float suggestion types.

No live API calls. No hardcoded producer values — bounds derived from fixture.
"""

import json
import pathlib
import re
import sys

import pytest

# ---------------------------------------------------------------------------
# Fixture loading
# ---------------------------------------------------------------------------
_FIXTURE_PATH = (
    pathlib.Path(__file__).parent / "fixtures" / "math" / "calibration_sweep_study_fixture.json"
)


@pytest.fixture(scope="module")
def fixture():
    return json.loads(_FIXTURE_PATH.read_text())


# ---------------------------------------------------------------------------
# AC-1.1 — all 5 required params present in OPTUNA_SEARCH_SPACE_KEYS
# ---------------------------------------------------------------------------

def test_all_five_ac1_params_present_in_search_space_keys(fixture):
    """OPTUNA_SEARCH_SPACE_KEYS must contain every param named in AC-1.

    Fails RED until VWAP_BREAK_CONFIRM_TICKS, VWAP_BLEED_ARM_MIN, and
    VWAP_BLEED_ARM_MAX are added to the frozenset.
    """
    import autotuner

    required = set(fixture["required_search_space_keys"])
    missing = required - autotuner.OPTUNA_SEARCH_SPACE_KEYS
    assert not missing, (
        f"OPTUNA_SEARCH_SPACE_KEYS is missing AC-1 params: {sorted(missing)}. "
        "Implementer must add them."
    )


# ---------------------------------------------------------------------------
# AC-1.2 — VWAP_BLEED_ARM_MIN bound constants
# ---------------------------------------------------------------------------

def test_vwap_bleed_arm_min_lower_bound_is_minus_five(fixture):
    """The most-negative allowed value for VWAP_BLEED_ARM_MIN sweep must be -5.0.

    Named constant _SS_VWAP_BLEED_ARM_MIN_LOW must equal the fixture-specified
    lower bound. Fails RED until the constant is added to autotuner.py.
    """
    import autotuner

    expected_low = fixture["search_space_bounds"]["VWAP_BLEED_ARM_MIN"]["low"]
    actual = getattr(autotuner, "_SS_VWAP_BLEED_ARM_MIN_LOW", None)
    assert actual is not None, (
        "_SS_VWAP_BLEED_ARM_MIN_LOW not found in autotuner — implementer must add it"
    )
    assert actual == pytest.approx(expected_low, abs=1e-9), (
        # tolerance: 1e-9 because this is an exact named constant, not a computed float
        f"_SS_VWAP_BLEED_ARM_MIN_LOW should be {expected_low}, got {actual}"
    )


def test_vwap_bleed_arm_min_upper_bound_is_minus_one(fixture):
    """The least-negative allowed value for VWAP_BLEED_ARM_MIN sweep must be -1.0.

    Named constant _SS_VWAP_BLEED_ARM_MIN_HIGH must equal the fixture-specified
    upper bound. Fails RED until the constant is added.
    """
    import autotuner

    expected_high = fixture["search_space_bounds"]["VWAP_BLEED_ARM_MIN"]["high"]
    actual = getattr(autotuner, "_SS_VWAP_BLEED_ARM_MIN_HIGH", None)
    assert actual is not None, (
        "_SS_VWAP_BLEED_ARM_MIN_HIGH not found in autotuner — implementer must add it"
    )
    assert actual == pytest.approx(expected_high, abs=1e-9), (
        f"_SS_VWAP_BLEED_ARM_MIN_HIGH should be {expected_high}, got {actual}"
    )


# ---------------------------------------------------------------------------
# AC-1.3 — VWAP_BLEED_ARM_MAX bound constants
# ---------------------------------------------------------------------------

def test_vwap_bleed_arm_max_lower_bound_is_minus_one(fixture):
    """The most-negative allowed value for VWAP_BLEED_ARM_MAX sweep must be -1.0.

    Fails RED until _SS_VWAP_BLEED_ARM_MAX_LOW is added.
    """
    import autotuner

    expected_low = fixture["search_space_bounds"]["VWAP_BLEED_ARM_MAX"]["low"]
    actual = getattr(autotuner, "_SS_VWAP_BLEED_ARM_MAX_LOW", None)
    assert actual is not None, (
        "_SS_VWAP_BLEED_ARM_MAX_LOW not found in autotuner — implementer must add it"
    )
    assert actual == pytest.approx(expected_low, abs=1e-9), (
        f"_SS_VWAP_BLEED_ARM_MAX_LOW should be {expected_low}, got {actual}"
    )


def test_vwap_bleed_arm_max_upper_bound_is_minus_point_one(fixture):
    """The least-negative allowed value for VWAP_BLEED_ARM_MAX sweep must be -0.1.

    Fails RED until _SS_VWAP_BLEED_ARM_MAX_HIGH is added.
    """
    import autotuner

    expected_high = fixture["search_space_bounds"]["VWAP_BLEED_ARM_MAX"]["high"]
    actual = getattr(autotuner, "_SS_VWAP_BLEED_ARM_MAX_HIGH", None)
    assert actual is not None, (
        "_SS_VWAP_BLEED_ARM_MAX_HIGH not found in autotuner — implementer must add it"
    )
    assert actual == pytest.approx(expected_high, abs=1e-9), (
        f"_SS_VWAP_BLEED_ARM_MAX_HIGH should be {expected_high}, got {actual}"
    )


# ---------------------------------------------------------------------------
# AC-1.4 — VWAP_BREAK_CONFIRM_TICKS is suggested as int (not float)
# ---------------------------------------------------------------------------

def test_vwap_break_confirm_ticks_bound_constants_are_integers(fixture):
    """VWAP_BREAK_CONFIRM_TICKS must use suggest_int — its bounds must be ints.

    Verifies _SS_VWAP_BREAK_CONFIRM_TICKS_LOW and _HIGH are Python int, not
    float. A suggest_float would produce non-integer tick counts which are
    semantically incorrect (you cannot confirm on 2.7 ticks).
    Fails RED until the constants are added.
    """
    import autotuner

    low_attr = "_SS_VWAP_BREAK_CONFIRM_TICKS_LOW"
    high_attr = "_SS_VWAP_BREAK_CONFIRM_TICKS_HIGH"

    low = getattr(autotuner, low_attr, None)
    high = getattr(autotuner, high_attr, None)

    assert low is not None, f"{low_attr} not found in autotuner"
    assert high is not None, f"{high_attr} not found in autotuner"

    assert isinstance(low, int), (
        f"{low_attr}={low!r} must be int — VWAP_BREAK_CONFIRM_TICKS is a tick count"
    )
    assert isinstance(high, int), (
        f"{high_attr}={high!r} must be int — VWAP_BREAK_CONFIRM_TICKS is a tick count"
    )

    # sanity: positive range
    assert low >= 1, f"{low_attr} must be >= 1 (at least one tick to confirm)"
    assert high > low, f"{high_attr} must be > {low_attr}"


# ---------------------------------------------------------------------------
# AC-1.5 — validate_search_space_nn1 still passes after expansion
# ---------------------------------------------------------------------------

def test_nn1_validation_passes_after_search_space_expansion():
    """Adding the 3 new params must NOT introduce a theory-frozen facet.

    validate_search_space_nn1() must not raise after the expansion.
    If it raises, the implementer violated the NN1 hard gate.
    """
    import autotuner

    # Should not raise — if it does, the implementer added a frozen facet
    try:
        autotuner.validate_search_space_nn1()
    except RuntimeError as exc:
        pytest.fail(
            f"validate_search_space_nn1 raised NN1 violation after search-space "
            f"expansion: {exc}"
        )


# ---------------------------------------------------------------------------
# AC-1.6 — bleed arm bounds are both negative (domain sanity)
# ---------------------------------------------------------------------------

def test_vwap_bleed_arm_min_bounds_are_negative():
    """Both VWAP_BLEED_ARM_MIN bounds must be negative (arm threshold is always negative).

    Fails RED until the constants exist; passes once they're negative.
    """
    import autotuner

    low = getattr(autotuner, "_SS_VWAP_BLEED_ARM_MIN_LOW", None)
    high = getattr(autotuner, "_SS_VWAP_BLEED_ARM_MIN_HIGH", None)

    if low is None or high is None:
        pytest.fail(
            "_SS_VWAP_BLEED_ARM_MIN_LOW or _HIGH not found — implementer must add them"
        )

    assert low < 0, f"_SS_VWAP_BLEED_ARM_MIN_LOW={low} must be negative"
    assert high < 0, f"_SS_VWAP_BLEED_ARM_MIN_HIGH={high} must be negative"
    assert low < high, (
        f"lower bound {low} must be more-negative than upper bound {high}"
    )


def test_vwap_bleed_arm_max_bounds_are_negative():
    """Both VWAP_BLEED_ARM_MAX bounds must be negative (arm threshold is always negative).

    Fails RED until the constants exist; passes once they're negative.
    """
    import autotuner

    low = getattr(autotuner, "_SS_VWAP_BLEED_ARM_MAX_LOW", None)
    high = getattr(autotuner, "_SS_VWAP_BLEED_ARM_MAX_HIGH", None)

    if low is None or high is None:
        pytest.fail(
            "_SS_VWAP_BLEED_ARM_MAX_LOW or _HIGH not found — implementer must add them"
        )

    assert low < 0, f"_SS_VWAP_BLEED_ARM_MAX_LOW={low} must be negative"
    assert high < 0, f"_SS_VWAP_BLEED_ARM_MAX_HIGH={high} must be negative"
    assert low < high, (
        f"lower bound {low} must be more-negative than upper bound {high}"
    )
