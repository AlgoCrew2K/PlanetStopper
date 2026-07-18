"""
R3-c / MA-11 AC-1 + AC-2 — golden-fixture tests for the OPTIONAL post-squeeze
``squeeze_floor`` clamp on ``math_engine.compute_active_trailing_stop``.

WHAT THIS PINS
--------------
The seam gains a NEW optional trailing parameter
``squeeze_floor: float | None = None``. When (para_armed OR breakeven_locked)
AND squeeze_floor is a positive finite number, the post-squeeze distance is
clamped UP to a floor that can only limit SHRINKAGE (never widen):

    active = max(active * parabolic_squeeze_multiplier,
                 min(squeeze_floor, pre_squeeze_active))

- squeeze_floor None / 0 / negative  -> NO clamp (byte-identical legacy).
- squeeze_floor non-finite (NaN/Inf) -> ValueError at entry (M-2), even when
  the squeeze branch does not fire.
- The clamp is scoped INSIDE the squeeze branch: with both flags False the
  floor is NEVER applied (7-arg twin of the untouched fixture 06).

RED taxonomy: at HEAD 1a7bf2fd the seam has NO ``squeeze_floor`` parameter, so
every call here raises ``TypeError: ... unexpected keyword argument
'squeeze_floor'`` -- a CONTRACT RED (the param is not built yet; the R3-b
AttributeError-on-missing-seam precedent). Post-impl each fixture asserts its
hand-derived value / rejection. The BEHAVIORAL non-vacuity RED lives in
tests/autotuner/test_squeeze_floor_behavioral_golden.py.

Fixture provenance (HARD): every expected float in
tests/fixtures/math_engine/squeeze_floor/*.json is DERIVED BY HAND from the
clamp formula and pinned in the fixture's 'derivation' field -- never captured
from an implementation (the param does not exist yet). No producer-computed
value is hardcoded in this test body; the arithmetic lives in the fixtures.

Tolerance: pytest.approx(rel=1e-9, abs=1e-12) -- identical rationale to
tests/math_engine/test_active_trailing_stop.py (multiplication + max + min are
exact in IEEE-754; a wrong impl misses by a structural factor, not ~1 ulp).
"""

from __future__ import annotations

import json
import math
import pathlib
from typing import Any

import pytest

import math_engine

FIXTURE_DIR = pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "squeeze_floor"

APPROX_REL = 1e-9
APPROX_ABS = 1e-12


def _coerce_floor(value: Any) -> Any:
    """Map the fixtures' non-finite string sentinels to real floats.

    Strict JSON cannot express NaN/Inf, so rejection fixtures store the floor
    as "nan" / "inf" / "-inf". Everything else (numbers, null) passes through.
    """
    if isinstance(value, str):
        mapping = {"nan": math.nan, "inf": math.inf, "-inf": -math.inf}
        assert value in mapping, f"unexpected squeeze_floor sentinel: {value!r}"
        return mapping[value]
    return value


def _load_fixtures() -> list[tuple[str, dict[str, Any]]]:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    out: list[tuple[str, dict[str, Any]]] = []
    for p in paths:
        with p.open("r", encoding="utf-8") as fh:
            out.append((p.name, json.load(fh)))
    return out


FIXTURES = _load_fixtures()
VALUE_FIXTURES = [(n, f) for n, f in FIXTURES if "expect_raises" not in f]
REJECT_FIXTURES = [(n, f) for n, f in FIXTURES if "expect_raises" in f]


def _call(fixture: dict[str, Any]) -> float:
    inp = fixture["inputs"]
    return math_engine.compute_active_trailing_stop(
        symphony_vol=inp["symphony_vol"],
        dynamic_multiplier=inp["dynamic_multiplier"],
        dynamic_min_stop=inp["dynamic_min_stop"],
        para_armed=inp["para_armed"],
        breakeven_locked=inp["breakeven_locked"],
        parabolic_squeeze_multiplier=inp["parabolic_squeeze_multiplier"],
        squeeze_floor=_coerce_floor(inp["squeeze_floor"]),
    )


def test_squeeze_floor_fixture_directory_is_populated() -> None:
    """Guard against a silently-empty glob (a passing suite of zero cases)."""
    assert len(VALUE_FIXTURES) >= 7, (
        f"expected >=7 value goldens, found {len(VALUE_FIXTURES)} in {FIXTURE_DIR}"
    )
    assert len(REJECT_FIXTURES) >= 3, (
        f"expected >=3 rejection goldens, found {len(REJECT_FIXTURES)} in {FIXTURE_DIR}"
    )


@pytest.mark.parametrize(
    "fixture_name,fixture",
    VALUE_FIXTURES,
    ids=[n for n, _ in VALUE_FIXTURES],
)
def test_squeeze_floor_clamp_matches_derived_expected(
    fixture_name: str, fixture: dict[str, Any]
) -> None:
    """AC-1/AC-2: the 7-arg seam returns each fixture's hand-derived value.

    RED at HEAD: TypeError (no squeeze_floor param). GREEN post-impl.
    """
    assert fixture["function"] == "compute_active_trailing_stop"
    actual = _call(fixture)
    expected = fixture["expected"]
    assert actual == pytest.approx(expected, rel=APPROX_REL, abs=APPROX_ABS), (
        f"[{fixture_name}] expected {expected} (derivation: {fixture['derivation']}), got {actual}"
    )


def test_no_widening_floor_never_exceeds_pre_squeeze() -> None:
    """AC-2 crux, stated as an explicit named assertion (not just a golden).

    fixture 03 has floor 0.30 > pre_squeeze 0.15; the result MUST equal
    pre_squeeze (0.15), never the floor (0.30). This is the exact inversion a
    naive max(squeezed, floor) would produce.
    """
    fx = next(f for n, f in VALUE_FIXTURES if "no_widening" in n)
    result = _call(fx)
    # pre_squeeze here is max(0.15*1.0, 0.10) = 0.15; floor is 0.30.
    assert result == pytest.approx(0.15, rel=APPROX_REL, abs=APPROX_ABS), (
        f"no-widening violated: floor 0.30 > pre_squeeze 0.15 must clamp AT "
        f"pre_squeeze (0.15), got {result}. A widening impl returns 0.30."
    )
    assert result < 0.30, "the clamped stop must NEVER reach the (too-high) floor value"


@pytest.mark.parametrize(
    "fixture_name,fixture",
    REJECT_FIXTURES,
    ids=[n for n, _ in REJECT_FIXTURES],
)
def test_nonfinite_squeeze_floor_is_rejected(fixture_name: str, fixture: dict[str, Any]) -> None:
    """AC-1 / M-2: a non-finite squeeze_floor raises ValueError at entry.

    RED at HEAD: the call raises TypeError (no such param) BEFORE reaching the
    reject guard, so pytest.raises(ValueError) fails -> RED. GREEN post-impl:
    the guard raises ValueError.
    """
    assert fixture["expect_raises"] == "ValueError"
    with pytest.raises(ValueError):
        _call(fixture)
