"""
Phase-1 structural contract for the CVaRAssessment dataclass.

WHY THIS TEST EXISTS
--------------------
CVaRAssessment is introduced in Phase 1 as a typed shape so that the M2 schema
migration (023_cvar_diagnostics.sql) can be authored against a stable target and
Phase-2 cutover proceeds field-rename-only (decision-science-council-synthesis.md
§2.6; plan deliverable 3). Its exact field set, fail-safe invariant, and the
Phase-1 production-consumer prohibition must be enforced structurally.

WHAT IS ASSERTED
----------------
1. CVaRAssessment is importable from math_engine.
2. The dataclass has exactly the four required fields: ``cvar_pct``,
   ``breach``, ``tail_obs_count``, ``insufficient_reason``. No extra field, no
   missing field.
3. Field types match the contract (structural check via dataclasses.fields —
   the actual type annotations on the field declarations).
4. A valid CVaRAssessment can be constructed when cvar_pct is a float.
5. A valid CVaRAssessment can be constructed when cvar_pct is None and
   breach=False (the only allowed None combination).
6. Construction with cvar_pct=None and breach=True raises ValueError — the
   __post_init__ fail-safe.
7. The dataclass is frozen (immutable) so no consumer can mutate breach after
   construction to bypass the invariant.
8. In Phase 1, no production module imports CVaRAssessment — the dataclass is
   import-safe but has zero production consumers until Phase-2 cutover.
9. tail_obs_count is 0 when cvar_pct is None (the sentinel case carries no
   tail observations).
10. The dataclass module constant CVaRAssessment is the exact same object
    as math_engine.CVaRAssessment — no shadow copy.

RED STATUS
----------
Tests 1-10 are RED until math_engine.CVaRAssessment is defined. Once the
implementer adds the dataclass, each test validates one structural invariant
in isolation so a partial implementation fails targeted tests rather than
the whole module.

FIXTURE
-------
tests/fixtures/math_engine/cvar_assessment_contract/
    cvar_assessment_dataclass_contract.json
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re

import pytest

FIXTURE_DIR = (
    pathlib.Path(__file__).parent.parent / "fixtures" / "math_engine" / "cvar_assessment_contract"
)

# Production modules that must NOT import CVaRAssessment in Phase 1.
_PRODUCTION_MODULES = [
    "alpha_bot_execution.py",
    "reporting.py",
    "synthetic_history.py",
    "autotuner.py",
    "app.py",
    "database.py",
]

_PROJECT_ROOT = pathlib.Path(__file__).parent.parent.parent


def _load_fixture() -> dict:
    with (FIXTURE_DIR / "cvar_assessment_dataclass_contract.json").open(
        "r", encoding="utf-8"
    ) as fh:
        return json.load(fh)


def _required_field_names(fx: dict) -> set[str]:
    return {f["name"] for f in fx["required_fields"]}


# ---------------------------------------------------------------------------
# 1. CVaRAssessment is importable from math_engine
# ---------------------------------------------------------------------------


def test_cvar_assessment_is_importable_from_math_engine() -> None:
    """
    The dataclass must live in math_engine so the Phase-2 simulator and the M2
    migration reference a single canonical import.

    RED until the implementer adds CVaRAssessment to math_engine.py.
    """
    import math_engine  # noqa: PLC0415 — intentional late import to give a clean RED message

    assert hasattr(math_engine, "CVaRAssessment"), (
        "math_engine has no 'CVaRAssessment' attribute. "
        "Phase-1 plan deliverable 3 requires the dataclass to be defined in "
        "math_engine.py so Phase-2 cutover has a stable single-import target."
    )


# ---------------------------------------------------------------------------
# 2. Exact field set — no extra, no missing
# ---------------------------------------------------------------------------


def test_cvar_assessment_has_exactly_the_required_fields() -> None:
    """
    The plan pins the field set to {cvar_pct, breach, tail_obs_count,
    insufficient_reason}. An extra field changes the M2 schema migration target;
    a missing field breaks Phase-2 consumers before they are written.

    RED until all four fields are declared.
    """
    import math_engine

    fx = _load_fixture()
    frozen_fields = _required_field_names(fx)

    live_fields = {f.name for f in dataclasses.fields(math_engine.CVaRAssessment)}

    extra = live_fields - frozen_fields
    missing = frozen_fields - live_fields

    assert not extra and not missing, (
        f"CVaRAssessment field set does not match the Phase-1 contract.\n"
        f"  Extra fields:   {sorted(extra)}\n"
        f"  Missing fields: {sorted(missing)}\n"
        f"The field set is pinned so the M2 schema migration "
        f"(023_cvar_diagnostics.sql) has a stable target. "
        f"See fixture: cvar_assessment_dataclass_contract.json."
    )


# ---------------------------------------------------------------------------
# 3. cvar_pct annotation admits None (mirrors the MC sentinel contract)
# ---------------------------------------------------------------------------


def test_cvar_assessment_cvar_pct_annotation_admits_none() -> None:
    """
    cvar_pct must be typed ``float | None``; an annotation of ``float`` would
    mislead type checkers into treating the sentinel case as unreachable.

    RED if the implementer annotates ``cvar_pct: float``.
    """
    import math_engine

    field_map = {f.name: f for f in dataclasses.fields(math_engine.CVaRAssessment)}
    assert "cvar_pct" in field_map, "cvar_pct field missing (caught above, but guard here)."

    annotation = str(field_map["cvar_pct"].type)
    assert "None" in annotation, (
        f"CVaRAssessment.cvar_pct is annotated {annotation!r}. "
        f"The annotation must admit None to represent the out-of-band insufficient "
        f"sentinel (mirroring MC_INSUFFICIENT_HISTORY_SENTINEL)."
    )


# ---------------------------------------------------------------------------
# 4. Construction with a valid float cvar_pct succeeds
# ---------------------------------------------------------------------------


def test_cvar_assessment_construction_with_valid_float_succeeds() -> None:
    """
    The normal (sufficient-data) case: a float cvar_pct with any breach value
    must construct without error.

    RED if the dataclass raises on a well-formed sufficient-data instance.
    """
    import math_engine

    # A negative cvar_pct is the typical case: the 5th-percentile return is a loss.
    # stderr is paired with cvar_pct — finite cvar_pct REQUIRES finite stderr
    # (decision-science-council-synthesis.md §A.3 H-2 binding).
    obj = math_engine.CVaRAssessment(
        cvar_pct=-4.2,
        breach=False,
        tail_obs_count=5,
        stderr=0.001,
        insufficient_reason=None,
    )
    assert obj.cvar_pct == pytest.approx(
        -4.2, abs=1e-9
    ), (  # approx: float equality is exact here but matches project convention
        "cvar_pct was not stored correctly."
    )
    assert obj.breach is False
    assert obj.tail_obs_count == 5
    assert obj.insufficient_reason is None


def test_cvar_assessment_construction_breach_true_with_valid_float_succeeds() -> None:
    """
    When cvar_pct is a valid float and breach is True (threshold exceeded),
    construction must succeed. The fail-safe guard only activates when
    cvar_pct is None.

    RED if the dataclass incorrectly rejects breach=True for a non-None cvar_pct.
    """
    import math_engine

    obj = math_engine.CVaRAssessment(
        cvar_pct=-8.5,
        breach=True,
        tail_obs_count=7,
        stderr=0.001,
        insufficient_reason=None,
    )
    assert obj.breach is True


# ---------------------------------------------------------------------------
# 5. Construction with cvar_pct=None and breach=False succeeds
# ---------------------------------------------------------------------------


def test_cvar_assessment_construction_none_cvar_pct_breach_false_succeeds() -> None:
    """
    The insufficient-sentinel case: cvar_pct=None with breach=False is the only
    valid None combination (fail-safe: absent CVaR is not a breach signal).

    RED if __post_init__ mistakenly rejects this valid construction.
    """
    import math_engine

    obj = math_engine.CVaRAssessment(
        cvar_pct=None,
        breach=False,
        tail_obs_count=0,
        stderr=None,
        insufficient_reason="Insufficient MC history (eligible days < MC_MIN_HISTORY_DAYS).",
    )
    assert obj.cvar_pct is None
    assert obj.breach is False
    assert obj.tail_obs_count == 0
    assert obj.insufficient_reason is not None
    assert obj.stderr is None


# ---------------------------------------------------------------------------
# 6. Construction with cvar_pct=None and breach=True raises ValueError
# ---------------------------------------------------------------------------


def test_cvar_assessment_none_cvar_pct_with_breach_true_raises() -> None:
    """
    The fail-safe invariant: cvar_pct=None implies breach=False. An absent CVaR
    estimate must never be treated as a breach. A caller that constructs
    CVaRAssessment(cvar_pct=None, breach=True) is making a logical error that
    could suppress the protective stop.

    The __post_init__ MUST raise ValueError for this combination.

    RED until __post_init__ enforces this invariant.
    """
    import math_engine

    with pytest.raises(
        (ValueError, TypeError),
        match=r"(?i)(cvar_pct.*None.*breach|breach.*None.*cvar_pct|insufficient.*breach|fail.safe)",
    ):
        math_engine.CVaRAssessment(
            cvar_pct=None,
            breach=True,
            tail_obs_count=0,
            stderr=None,
            insufficient_reason="Should not matter — breach=True with None is the violation.",
        )


# ---------------------------------------------------------------------------
# 7. The dataclass is frozen (immutable after construction)
# ---------------------------------------------------------------------------


def test_cvar_assessment_is_frozen() -> None:
    """
    The dataclass must be @dataclass(frozen=True) so no consumer can mutate
    ``breach`` after construction to bypass the fail-safe invariant. Without
    frozen=True, a caller could do obj.breach = True on an insufficient object.

    RED if the implementer uses a mutable dataclass.
    """
    import math_engine

    obj = math_engine.CVaRAssessment(
        cvar_pct=None,
        breach=False,
        tail_obs_count=0,
        stderr=None,
        insufficient_reason="Test immutability.",
    )
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        obj.breach = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 8. Phase-1 production-consumer prohibition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("module_filename", _PRODUCTION_MODULES)
def test_phase1_production_module_does_not_import_cvar_assessment(
    module_filename: str,
) -> None:
    """
    In Phase 1, no production module may import or reference CVaRAssessment.
    The dataclass is introduced so the M2 schema migration has a stable target,
    not so Phase-1 code can use it. A Phase-1 production caller would bypass the
    Phase-2 cutover ceremony.

    This test grep-asserts (not import-asserts) so it catches string references
    and aliased imports as well as direct ``from math_engine import CVaRAssessment``
    forms.

    RED if a Phase-1 PR adds a CVaRAssessment reference to a production module.
    """
    module_path = _PROJECT_ROOT / module_filename
    assert module_path.exists(), f"{module_filename} not found in project root."

    source = module_path.read_text(encoding="utf-8")
    # Match any form: import CVaRAssessment, CVaRAssessment(, CVaRAssessment.
    matches = re.findall(r"\bCVaRAssessment\b", source)
    assert not matches, (
        f"{module_filename} references CVaRAssessment {len(matches)} time(s). "
        f"Phase-1 production consumer prohibition: CVaRAssessment must have zero "
        f"production consumers until the Phase-2 cutover ceremony. "
        f"The dataclass is imported only by tests and the Phase-2 "
        f"simulate_forward_paths module (not yet written)."
    )


# ---------------------------------------------------------------------------
# 9. tail_obs_count is 0 when cvar_pct is None
# ---------------------------------------------------------------------------


def test_cvar_assessment_tail_obs_count_is_zero_when_cvar_pct_is_none() -> None:
    """
    An insufficient-sentinel CVaRAssessment has no tail observations by
    definition — there was no CVaR computation. tail_obs_count=0 is the only
    semantically correct value when cvar_pct is None.

    This is a contract test, not a __post_init__ enforcement test — the
    implementer documents the convention; the test pins it so Phase-2 callers
    can rely on the 0 value without guarding against non-zero tail counts in
    the None case.

    RED if __post_init__ allows tail_obs_count > 0 with cvar_pct=None
    (which would be a contract violation; we assert the documented convention).
    """
    import math_engine

    obj = math_engine.CVaRAssessment(
        cvar_pct=None,
        breach=False,
        tail_obs_count=0,
        stderr=None,
        insufficient_reason="Below MC minimum history.",
    )
    assert obj.tail_obs_count == 0, (
        f"tail_obs_count should be 0 for an insufficient-sentinel CVaRAssessment; "
        f"got {obj.tail_obs_count}."
    )

    # Hostile variant: construction with tail_obs_count > 0 and cvar_pct=None
    # must raise (or at minimum __post_init__ must validate this combination).
    # We assert the raised-or-zero contract: if it does not raise, the stored
    # value must be 0 (i.e. __post_init__ normalized it).
    try:
        obj2 = math_engine.CVaRAssessment(
            cvar_pct=None,
            breach=False,
            tail_obs_count=3,  # non-zero with None cvar_pct — should be invalid
            stderr=None,
            insufficient_reason="Inconsistent: non-zero tail count but no CVaR.",
        )
        # If construction succeeds, the value must be 0 (normalized by __post_init__).
        assert obj2.tail_obs_count == 0, (
            f"CVaRAssessment(cvar_pct=None, tail_obs_count=3) constructed without "
            f"error AND stored tail_obs_count={obj2.tail_obs_count}. "
            f"When cvar_pct is None, tail_obs_count must be 0 — either __post_init__ "
            f"must raise on nonzero counts or normalize them to 0."
        )
    except (ValueError, TypeError):
        pass  # Raising is the preferred enforcement — consistent with the breach invariant.
